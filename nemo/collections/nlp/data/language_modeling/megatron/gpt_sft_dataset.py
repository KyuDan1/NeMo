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

from typing import List, Optional

import numpy as np
import torch
from datasets import load_dataset

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
        context_keys: str = "text",
        label_key: str = "answer",
        answer_only_loss: bool = True,
        truncation_fields: str = "text",
        pad_to_max_length: bool = False,  # (@adithyare) allows for much faster training especially in PEFT settings.
        index_mapping_dir: str = None,
        prompt_template: str = None,
        virtual_tokens: int = 0,
        tokens_to_generate: int = 0,
        memmap_workers: Optional[int] = None,
        hf_dataset: bool = False,
        truncation_method: str = 'right',
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
        answer_only_loss: If True, will compute the loss only on the answer part of the input. If False, will compute the loss on the entire input.
        truncation_fields: Field to use for truncation. (Options: keys in context_keys). Field to be used for truncation if the combined length exceeds the max sequence length.
        pad_to_max_length: Whether to pad the input to the max sequence length. If False, will pad to the max length of the current batch.
        index_mapping_dir: Directory to save the index mapping to. If None, will write to the same folder as the dataset.
        prompt_template: Prompt template to inject via an fstring. Formatted like Q: {context_key}\n\nA: {label_key}
        hf_dataset: Whether to load the json file with the HuggingFace dataset. otherwise, will load the jsonl file with the JSONLMemMapDataset.
        truncation_method: Truncation from which position. Options: ['random', 'left', 'right']
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
        self.context_keys = context_keys.split(',')
        self.label_key = label_key
        self.answer_only_loss = answer_only_loss
        self.truncation_fields = truncation_fields.split(',')
        self.pad_to_max_length = pad_to_max_length
        self.index_mapping_dir = index_mapping_dir
        self.prompt_template = prompt_template
        self.virtual_tokens = virtual_tokens
        self.tokens_to_generate = tokens_to_generate
        assert (
            self.prompt_template is not None
        ), f'we need prompt_template to combine contexts {context_keys} and label {label_key}'
        # When providing things like newlines in the prompt template via the CLI, they are escaped. This line unescapes them.
        self.prompt_template = self.prompt_template.encode('utf-8').decode('unicode_escape')

        # Legacy checkpoints has self.truncation_fields = ['context'] and self.context_keys = ['input']
        if (
            len(self.truncation_fields) == 1
            and len(self.context_keys) == 1
            and self.context_keys[0] == 'input'
            and self.truncation_fields[0] == 'context'
        ):
            self.truncation_fields[0] = self.context_keys[0]
        assert set(self.truncation_fields).issubset(
            self.context_keys
        ), f'truncation_fields {self.truncation_fields} must in {self.context_keys}'

        if hf_dataset:
            self.indexed_dataset = load_dataset(
                'json', data_files=file_path, cache_dir=index_mapping_dir, num_proc=memmap_workers, split='train'
            )
        else:
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
        # idx may < 0 because we pad_samples_to_global_batch_size, e.g. id = -1
        if idx < 0:
            idx = len(self) + idx
        try:
            example = self.indexed_dataset[idx]
        except Exception as e:
            logging.error(f"Error while loading example {idx} from dataset {self.file_path}")
            raise e
        return self._process_example(example)

    def _separate_template(self, contexts: List[str], label: str):
        """
        Combine contexts and label based on prompt_template into a list of strings and a list of keys.

        Args:
            contexts (List[str]): the list of context strings in jsonl file.
            label (str): the label string in jsonl file.

        Returns:
            prompt_strings (List[str]): separated prompt_template with contexts/label placeholder filled with corresponding strings
            prompt_keys (List[str]): strings point to placeholder keys or <template>
            
        Examples:
            prompt_template = 'Context: {context} Question: {question} Answer: {label}'
            contexts = ['CONTEXT', 'QUESTION']
            label = ['LABEL']
            
            prompt_strings = ['Context:', ' CONTEXT', ' Question:', ' QUESTION', ' Answer:', ' LABEL'] # tokenizer.space_sensitive = True
            prompt_strings = ['Context:', 'CONTEXT', 'Question:', 'QUESTION', 'Answer:', 'LABEL'] # tokenizer.space_sensitive = False
            prompt_keys = ['<template>', 'context', '<template>', 'question', '<template>', 'label']
        """
        prompt_strings = []
        prompt_keys = []

        # context placeholder
        context_phs = [f'{{{ck}}}' for ck in self.context_key]
        for cph in context_phs:
            assert cph in self.prompt_template, f'{cph} must in {self.prompt_template}'
        context_ph_to_context_s = {cph: cs for cph, cs in zip(context_phs, contexts)}
        context_ph_to_context_k = {cph: ck for cph, ck in zip(context_phs, self.context_key)}

        # label placeholder
        label_ph = f'{{{self.label_key}}}'
        assert label_ph in self.prompt_template, f'{label_ph} must in {self.prompt_template}'
        assert self.prompt_template.index(label_ph) == len(self.prompt_template) - len(
            label_ph
        ), f'{{{self.label_key}}} must be at the end of prompt_template.'

        # get sorted placeholder positions
        context_positions = [
            p
            for cph in context_phs
            for p in (self.prompt_template.index(cph), self.prompt_template.index(cph) + len(cph))
        ]
        context_positions = [0] + context_positions + [self.prompt_template.index(label_ph)]
        context_positions = sorted(context_positions)

        # iterate each placeholder position bucket
        for i in range(len(context_positions) - 1):
            pi = context_positions[i]
            pj = context_positions[i + 1]
            
            # if tokenizer is space sensitive e.g. AutoTokenizer(pretrained_model_name='gpt2', 
            # we should check leading space from last position of prev bucket or first position of curr bucket
            # and add one space back to the left of string after we do string.strip(' ')
            if i != 0 and getattr(self.tokenizer, 'space_sensitive', False):
                has_leading_space = self.prompt_template[pi - 1] == ' ' or self.prompt_template[pi] == ' '
            else:
                has_leading_space = False

            # get string for the bucket from placeholder jsonl string or from prompt_template string
            cs = context_ph_to_context_s.get(self.prompt_template[pi:pj], self.prompt_template[pi:pj]).strip(' ')
            if len(cs) > 0:
                prompt_strings.append(' ' + cs if has_leading_space else cs)
                prompt_keys.append(context_ph_to_context_k.get(self.prompt_template[pi:pj], '<template>'))

        has_leading_space = self.prompt_template[pj - 1] == ' ' if getattr(self.tokenizer, 'space_sensitive', False) else False
        prompt_strings.append(' ' + label if has_leading_space else label)
        prompt_keys.append(self.label_key)

        return prompt_strings, prompt_keys

    def _multiple_truncation(self, prompt_ids: List[List[int]], prompt_keys: List[str]):
        """
        Calculate total tokens and truncate multiple contexts in truncation_fields
        Args:
            prompt_ids (List[List[int]]): the list of separate prompt_template ids.
            prompt_keys (List[str]): strings point to placeholder keys or <template> (used to check truncation_fields).

        Returns:
            context_ids (List[int]): all context ids.
            label_ids (List[int]): all label ids.
        """
        context_ids = prompt_ids[:-1]
        label_ids = prompt_ids[-1]
        total_ids = (
            self.virtual_tokens
            + sum(len(ids) for ids in context_ids)
            + max(len(label_ids), self.tokens_to_generate)
            + self.add_bos
            + self.add_sep
            + self.add_eos  # Only training need to consider eos token
        )

        if total_ids > self.max_seq_length:
            truncation_length_total = total_ids - self.max_seq_length
            num_fields = len(self.truncation_fields)
            # Sorted equal divide length to each field
            # Examples:
            #   truncation_length_total = 3
            #   num_fields = 11
            #   truncation_length_list = [3,4,4]
            truncation_length_list = [
                truncation_length_total // num_fields + (1 if i < truncation_length_total % num_fields else 0)
                for i in range(num_fields)[::-1]
            ]

            for i, (ids, key) in enumerate(zip(context_ids, prompt_keys[:-1])):
                if key in self.truncation_fields:
                    truncation_length = truncation_length_list.pop()
                    assert len(ids) >= truncation_length, f'{key} is not long enough to truncate.'
                    if self.truncation_method == 'random':
                        ids_offset = random.randint(0, truncation_length)
                    elif self.truncation_method == 'left':
                        ids_offset = truncation_length
                    elif self.truncation_method == 'right':
                        ids_offset = 0
                    else:
                        raise ValueError(f'{self.truncation_method} is not supported')

                    ids_length = len(ids) - truncation_length
                    context_ids[i] = ids[ids_offset : ids_offset + ids_length]

        context_ids = [i for ids in context_ids for i in ids]
        return context_ids, label_ids

    def _process_example(self, example):
        """
        Create an example by concatenating text and answer.
        Truncation is carried out when needed, but it is performed only on the prompt side.
        BOS, EOS, and SEP, are added if specified.
        """
        contexts = [example[c].strip(' ') for c in self.context_keys]
        label = example[self.label_key]

        prompt_strings, prompt_keys = self._separate_template(contexts, label)
        prompt_ids = [self.tokenizer.text_to_ids(p) for p in prompt_strings]
        context_ids, answer_ids = self._multiple_truncation(prompt_ids, prompt_keys)

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
        if self.add_eos:
            input_ids = input_ids + [self.tokenizer.eos_id]

        if len(input_ids) > self.max_seq_length:
            logging.warning(f'Input ids length {len(input_ids)} exceed max sequence length {self.max_seq_length}')
            input_ids = input_ids[: self.max_seq_length]

        # store metadata in dataset, in case user may have keys required in the prediction json files
        metadata = {k: v for k, v in example.items() if k not in [self.context_key, self.label_key]}

        processed_example = {
            'input_ids': input_ids,
            'answer_start_idx': answer_start_idx,
            'context_ids': context_ids,
            'context_length': len(context_ids),
            'answer_ids': answer_ids,
            'metadata': metadata,
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
        answers = [item['answer_ids'] for item in batch]
        loss_mask = [self._build_loss_mask(item)[1:] for item in batch]
        metadata = [item['metadata'] for item in batch]

        max_length = max(max([len(x) for x in input_ids]), max([len(x) for x in contexts]) + self.tokens_to_generate)
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
        answers = torch.LongTensor(self._collate_item(answers, max_length=max_length, pad_id=self.tokenizer.eos_id))

        processed_batch = {
            'tokens': input_ids,
            'labels': labels,
            'attention_mask': attention_mask,
            'loss_mask': loss_mask,
            'position_ids': position_ids,
            'contexts': contexts,
            'context_lengths': context_lengths,
            'answers': answers,
            'metadata': metadata,
        }

        return processed_batch
