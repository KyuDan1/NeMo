# Copyright (c) 2023, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import List, Optional, Union

import numpy as np
import torch

from nemo.collections.common.tokenizers.tokenizer_spec import TokenizerSpec
from nemo.collections.nlp.data.language_modeling.megatron.dataset_utils import get_samples_mapping
from nemo.collections.nlp.data.language_modeling.text_memmap_dataset import JSONLMemMapDataset
from nemo.core.classes import Dataset
from nemo.utils import logging

__all__ = ['GPTSFTDataset']


class GPTSFTDataset(Dataset):
    def __init__(
        self,
        file_path: str,
        tokenizer: TokenizerSpec,
        max_seq_length: int = 1024,
        min_seq_length: int = 1,
        add_bos: bool = False,
        add_eos: bool = True,
        add_sep: bool = False,
        sep_id: int = None,
        max_num_samples: int = None,
        seed: int = 1234,
        context_keys: Union[List[str], str] = "text",
        label_key: str = "answer",
        separate_prompt_and_response_with_newline: bool = False,
        answer_only_loss: bool = True,
        truncation_fields: Union[List[str], str] = "text",
        pad_to_max_length: bool = False,  # (@adithyare) allows for much faster training especially in PEFT settings.
        index_mapping_dir: str = None,
        prompt_template: str = None,
        virtual_tokens: int = 0,
        tokens_to_generate: int = 0,
        memmap_workers: Optional[int] = None,
    ):
        """
        file_path: Path to a JSONL GPT supervised fine-tuning dataset. Data is formatted as multiple JSON lines with each line formatted as follows. {'input': 'John von Neumann\nVon Neumann made fundamental contributions .... Q: What did the math of artificial viscosity do?', 'output': 'smoothed the shock transition without sacrificing basic physics'}
        tokenizer: Tokenizer for the dataset. Instance of a class that inherits TokenizerSpec (ex: YTTM, SentencePiece).
        max_seq_length (int): maximum sequence length for each dataset examples. Examples will either be truncated to fit this length or dropped if they cannot be truncated.
        min_seq_length (int): min length of each data example in the dataset. Data examples will be dropped if they do not meet the min length requirements.
        add_bos (bool): Whether to add a beginning of sentence token to each data example
        add_eos (bool): Whether to add an end of sentence token to each data example
        add_sep (bool): Whether to add a separation token to each data example (goes between prompt and answer)
        tokens_to_generate (int): (inference only) Number of tokens to generate during inference
        seed: Random seed for data shuffling.
        max_num_samples: Maximum number of samples to load. This can be > dataset length if you want to oversample data. If None, all samples will be loaded.
        seed: int = 1234,
        context_keys: Key to use for the context in your JSONL file
        label_key: Key to use for the label in your JSONL file
        separate_prompt_and_response_with_newline: Adds a newline between prompt and response.
        answer_only_loss: If True, will compute the loss only on the answer part of the input. If False, will compute the loss on the entire input.
        truncation_field: Field to use for truncation. (Options: keys in context_keys). Field to be used for truncation if the combined length exceeds the max sequence length.
        pad_to_max_length: Whether to pad the input to the max sequence length. If False, will pad to the max length of the current batch.
        index_mapping_dir: Directory to save the index mapping to. If None, will write to the same folder as the dataset.
        prompt_template: Prompt template to inject via an fstring. Formatted like Q: {input}\n\nA: {output}
        """
        self.tokenizer = tokenizer
        self.file_path = file_path
        self.max_seq_length = max_seq_length
        self.min_seq_length = min_seq_length
        self.add_bos = add_bos
        self.add_eos = add_eos
        self.add_sep = add_sep
        self.sep_id = sep_id
        self.max_num_samples = max_num_samples
        self.seed = seed
        if isinstance(context_keys, str):
            self.context_keys = context_keys.split(',')
        elif isinstance(context_keys, list) or isinstance(context_keys, list):
            self.context_keys = context_keys
        else:
            raise RuntimeError("context_key is invalid type")
        self.label_key = label_key
        self.separate_prompt_and_response_with_newline = separate_prompt_and_response_with_newline
        self.answer_only_loss = answer_only_loss
        if isinstance(truncation_fields, str):
            self.truncation_fields = truncation_fields.split(',')
        elif isinstance(truncation_fields, list):
            self.truncation_fields = truncation_fields
        else:
            raise RuntimeError("truncation_fields is invalid type")

        self.pad_to_max_length = pad_to_max_length
        self.index_mapping_dir = index_mapping_dir
        self.prompt_template = prompt_template
        self.virtual_tokens = virtual_tokens
        self.tokens_to_generate = tokens_to_generate
        if self.prompt_template is not None:
            # When providing things like newlines in the prompt template via the CLI, they are escaped. This line unescapes them.
            self.prompt_template = self.prompt_template.encode('utf-8').decode('unicode_escape')

        # Previous models has self.truncation_fields = ['context'] and self.context_keys = ['input']
        if len(self.truncation_fields) == 1 and len(self.context_keys) == 1 and self.truncation_fields[0] == 'context':
            self.truncation_fields[0] = self.context_keys[0]
        assert set(self.truncation_fields).issubset(
            self.context_keys
        ), f'truncation_fields {self.truncation_fields} must in {self.context_keys}'

        self.indexed_dataset = JSONLMemMapDataset(
            dataset_paths=[file_path],
            tokenizer=None,
            header_lines=0,
            index_mapping_dir=index_mapping_dir,
            workers=memmap_workers,
        )

        # Will be None after this call if `max_num_samples` is None
        self._build_samples_mapping()

    def _build_samples_mapping(self):
        if self.max_num_samples is not None:
            self.samples_mapping = get_samples_mapping(
                indexed_dataset=self.indexed_dataset,
                data_prefix=self.file_path,
                num_epochs=None,
                max_num_samples=self.max_num_samples,
                max_seq_length=self.max_seq_length - 2,
                short_seq_prob=0,
                seed=self.seed,
                name=self.file_path.split('/')[-1],
                binary_head=False,
                index_mapping_dir=self.index_mapping_dir,
            )
        else:
            self.samples_mapping = None

    def __len__(self):
        if self.max_num_samples is None:
            return len(self.indexed_dataset)
        else:
            return len(self.samples_mapping)

    def __getitem__(self, idx):
        if isinstance(idx, np.int64):
            idx = idx.item()

        if self.samples_mapping is not None:
            assert idx < len(self.samples_mapping)
            idx, _, _ = self.samples_mapping[idx]
            if isinstance(idx, np.uint32):
                idx = idx.item()

        assert idx < len(self.indexed_dataset)
        example = self.indexed_dataset[idx]
        return self._process_example(example)

    def _process_prompt(self, contexts: List[str], label: str):
        """
        Combine contexts and label string into a unifed string.
        """
        if self.prompt_template is not None:
            for ck in self.context_keys:
                assert f'{{{ck}}}' in self.prompt_template
            assert f'{{{self.label_key}}}' in self.prompt_template
            # Make sure that '{output}' always occurs at the end of the prompt template string
            assert self.prompt_template.index(f'{{{self.label_key}}}') == len(self.prompt_template) - len(
                f'{{{self.label_key}}}'
            )
            # Get the context by replacing only the input
            original_contexts = contexts[:]  # copy all text from context fields
            context = self.prompt_template[:]  # copy the prompt template string
            context = context.replace(f'{{{self.label_key}}}', '')  # remove the label from the content
            for ct, ck in zip(contexts, self.context_keys):
                context = context.replace(f'{{{ck}}}', ct)  # replace each context key with the context text
            context = context.strip(' ')

            # Replace the input and output placeholders with the actual input and output
            text = self.prompt_template[:]
            text = text.replace(f'{{{self.label_key}}}', label)
            for ct, ck in zip(original_contexts, self.context_keys):
                text = text.replace(f'{{{ck}}}', ct)  # replace each context key with the context text

        elif self.separate_prompt_and_response_with_newline:
            context = '\n'.join(contexts)
            text = context + '\n' + label

        else:
            context = ' '.join(contexts)
            text = context + ' ' + label

        context_ids = self.tokenizer.text_to_ids(context)
        # Make sure always take the same string for label_ids
        label_ids = self.tokenizer.text_to_ids(text[len(context):])

        return context_ids, label_ids

    def _process_truncation(self, contexts: List[str], label: str):
        """
        Calculate total tokens and truncate contexts.
        """
        context_ids, label_ids = self._process_prompt(contexts, label)
        total_ids = (
            self.virtual_tokens
            + len(context_ids)
            + max(len(label_ids), self.tokens_to_generate)
            + self.add_bos
            + self.add_sep
        )
        # Only training need to consider eos token
        if self.tokens_to_generate == 0:
            total_ids += self.add_eos

        if total_ids > self.max_seq_length:
            truncation_length_total = total_ids - self.max_seq_length
            field_length = len(self.truncation_fields)
            # Sorted equal divide length to each field
            truncation_length_list = [
                truncation_length_total // field_length + (1 if i < truncation_length_total % field_length else 0)
                for i in range(field_length)[::-1]
            ]

            for i, ck in enumerate(self.context_keys):
                if ck in self.truncation_fields:
                    # We use text_to_tokens because ids may be unknown to raise an issue if we detokenize back
                    context_tokens = self.tokenizer.text_to_tokens(contexts[i])
                    truncation_length = truncation_length_list.pop()
                    assert len(context_tokens) >= truncation_length, f'{ck} is not long enough to truncate.'
                    context_tokens = context_tokens[: -min(truncation_length, len(context_tokens))]
                    contexts[i] = self.tokenizer.tokens_to_text(context_tokens)

        return contexts

    def _process_example(self, example):
        """
        Create an example by concatenating text and answer.
        Truncation is carried out when needed, but it is performed only on the prompt side.
        BOS, EOS, and SEP, are added if specified.
        """
        contexts = [example[c] for c in self.context_keys]
        label = example[self.label_key]

        contexts = self._process_truncation(contexts, label)
        context_ids, label_ids = self._process_prompt(contexts, label)

        if self.virtual_tokens:
            # (@adithyare) we are going to insert "pad/eos" tokens in the beginning of the text and context
            # these pad/eos tokens are placeholders for virtual tokens
            context_ids = [self.tokenizer.eos_id] * self.virtual_tokens + context_ids

        input_ids = context_ids
        answer_start_idx = len(input_ids)

        # Adds bos token in the start
        if self.add_bos:
            context_ids = [self.tokenizer.bos_id] + context_ids
            input_ids = [self.tokenizer.bos_id] + input_ids
            answer_start_idx += 1

        # Adds sep token between text/prompt and answer
        if self.add_sep:
            context_ids = context_ids + [self.sep_id]
            input_ids = input_ids + [self.sep_id]
            answer_start_idx += 1

        input_ids = input_ids + label_ids

        # Only training need to consider eos token
        if self.add_eos and self.tokens_to_generate == 0:
            input_ids = input_ids + [self.tokenizer.eos_id]

        if len(input_ids) > self.max_seq_length:
            logging.warning(f'Input ids length {len(input_ids)} exceed max sequence length {self.max_seq_length}')
            input_ids = input_ids[: self.max_seq_length]

        processed_example = {
            'input_ids': input_ids,
            'answer_start_idx': answer_start_idx,
            'context_ids': context_ids,
            'context_length': len(context_ids),
        }

        return processed_example

    def _maybe_cast_to_list(self, x):
        if isinstance(x, np.ndarray):
            return [item.tolist() for item in x]
        return x

    def _ceil_to_nearest(self, n, m):
        return (n + m - 1) // m * m

    def _collate_item(self, item, max_length, pad_id):
        item = self._maybe_cast_to_list(item)
        # max_length = max([len(x) for x in item]) if item else 0
        # here [0] should be tokenizer.pad_id
        item = [x + [pad_id] * (max_length - len(x)) for x in item]
        return item

    def _build_loss_mask(self, processed_example):
        """ Pad input_ids in batch to max batch length while building loss mask """
        input_ids = processed_example['input_ids']
        answer_start_idx = processed_example['answer_start_idx']
        if self.answer_only_loss:
            loss_mask = [float(idx >= answer_start_idx) for idx in range(len(input_ids))]
        else:
            loss_mask = [1.0] * len(input_ids)

        return loss_mask

    @torch.no_grad()
    def _create_attention_mask(self, max_length):
        """Create `attention_mask`.
        Args:
            input_ids: A 1D tensor that holds the indices of tokens.
        """
        # seq_length = len(input_ids)
        # `attention_mask` has the shape of [1, seq_length, seq_length]
        attention_mask = torch.tril(torch.ones((max_length, max_length))).unsqueeze(0)
        attention_mask = attention_mask < 0.5
        return attention_mask

    def collate_fn(self, batch):
        input_ids = [item['input_ids'][:-1] for item in batch]
        labels = [item['input_ids'][1:] for item in batch]
        contexts = [item['context_ids'] for item in batch]
        context_lengths = torch.LongTensor([item['context_length'] for item in batch])
        loss_mask = [self._build_loss_mask(item)[1:] for item in batch]

        max_length = max([len(x) for x in input_ids]) + self.tokens_to_generate
        # increase max length to nearest multiple of 4 or 8
        if self.pad_to_max_length:
            max_length = self.max_seq_length
        else:
            max_length = min(self.max_seq_length, self._ceil_to_nearest(max_length, 8))
        assert max_length <= self.max_seq_length

        attention_mask = [self._create_attention_mask(max_length) for _ in batch]
        attention_mask = torch.stack(attention_mask)
        position_ids = [list(range(max_length)) for _ in batch]
        position_ids = torch.LongTensor(position_ids)
        input_ids = torch.LongTensor(
            self._collate_item(input_ids, max_length=max_length, pad_id=self.tokenizer.eos_id)
        )
        labels = torch.LongTensor(self._collate_item(labels, max_length=max_length, pad_id=self.tokenizer.eos_id))
        loss_mask = torch.LongTensor(self._collate_item(loss_mask, max_length=max_length, pad_id=0))
        contexts = torch.LongTensor(self._collate_item(contexts, max_length=max_length, pad_id=self.tokenizer.eos_id))

        processed_batch = {
            'tokens': input_ids,
            'labels': labels,
            'attention_mask': attention_mask,
            'loss_mask': loss_mask,
            'position_ids': position_ids,
            'contexts': contexts,
            'context_lengths': context_lengths,
        }

        return processed_batch
