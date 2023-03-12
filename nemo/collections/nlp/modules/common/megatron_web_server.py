# Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
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

import gradio as gr
import numpy as np
import time

from nemo.collections.nlp.modules.common.megatron.retrieval_services.util import (
    convert_retrieved_to_md,
    request_data,
    request_post_data,
    convert_qa_evidence_to_md,
)


def get_generation(prompt, greedy, add_BOS, token_to_gen, min_tokens, temp, top_p, top_k, repetition, port=5555):
    data = {
        "sentences": [prompt],
        "tokens_to_generate": int(token_to_gen),
        "temperature": temp,
        "add_BOS": add_BOS,
        "top_k": top_k,
        "top_p": top_p,
        "greedy": greedy,
        "all_probs": False,
        "repetition_penalty": repetition,
        "min_tokens_to_generate": int(min_tokens),
    }
    sentences = request_data(data, port=port, path='generate')['sentences']
    return sentences[0]


def get_demo(share, username, password):
    with gr.Blocks() as demo:
        with gr.Row():
            with gr.Column(scale=2, width=200):
                greedy_flag = gr.Checkbox(label="Greedy")
                add_BOS = gr.Checkbox(label="Add BOS token", value=False)
                token_to_gen = gr.Number(label='Number of Tokens to generate', value=300, type=int)
                min_token_to_gen = gr.Number(label='Min number of Tokens to generate', value=1, type=int)
                temperature = gr.Slider(minimum=0.0, maximum=10.0, value=1.0, label='Temperature', step=0.1)
                top_p = gr.Slider(minimum=0.0, maximum=1.0, step=0.02, value=0.9, label='Top P')
                top_k = gr.Slider(minimum=0, maximum=10000, step=2, value=0, label='Top K')
                repetition_penality = gr.Slider(
                    minimum=1.0, maximum=5.0, step=0.02, value=1.2, label='Repetition penalty'
                )
            with gr.Column(scale=1, min_width=800):
                input_prompt = gr.Textbox(
                    label="Input",
                    value="Ariel was playing basketball. 1 of her shots went in the hoop. 2 of her shots did not go in the hoop. How many shots were there in total?",
                    lines=5,
                )
                output_box = gr.Textbox(value="", label="Output")
                btn = gr.Button(value="Submit")
                btn.click(
                    get_generation,
                    inputs=[
                        input_prompt,
                        greedy_flag,
                        add_BOS,
                        token_to_gen,
                        min_token_to_gen,
                        temperature,
                        top_p,
                        top_k,
                        repetition_penality,
                    ],
                    outputs=[output_box],
                )
    demo.launch(share=share, server_port=13570, server_name='0.0.0.0', auth=(username, password))


class RetroDemoWebApp:
    def __init__(self, text_service_ip, text_service_port, combo_service_ip, combo_service_port):
        self.text_service_ip = text_service_ip
        self.text_service_port = text_service_port
        self.combo_service_ip = combo_service_ip
        self.combo_service_port = combo_service_port

    def get_retro_generation(
        self, prompt, greedy, add_BOS, token_to_gen, min_tokens, temp, top_p, top_k, repetition, neighbors, weight
    ):
        data = {
            "sentences": [prompt],
            "tokens_to_generate": int(token_to_gen),
            "temperature": temp,
            "add_BOS": add_BOS,
            "top_k": top_k,
            "top_p": top_p,
            "greedy": greedy,
            "all_probs": False,
            "repetition_penalty": repetition,
            "min_tokens_to_generate": int(min_tokens),
            "neighbors": int(neighbors),
        }
        self.update_weight(weight)
        output_json = request_data(data, self.text_service_ip, self.text_service_port, path='generate')
        sentences = output_json['sentences']
        retrieved = output_json['retrieved']
        return sentences[0], convert_retrieved_to_md(retrieved)

    def update_weight(self, weight):
        data = {"update_weight": [weight, 1.0 - weight]}
        return request_data(data, self.combo_service_ip, self.combo_service_port)

    def add_doc(self, doc, add_eos):
        data = {
            "sentences": [doc],
            "add_eos": add_eos,
        }
        return request_data(data, self.combo_service_ip, self.combo_service_port)

    def reset_index(self):
        data = {"reset": None}
        return request_data(data, self.combo_service_ip, self.combo_service_port)

    def run_demo(self, share, username, password, port):
        with gr.Blocks(css="table, th, td { border: 1px solid blue; table-layout: fixed; width: 100%; }") as demo:
            with gr.Row():
                with gr.Column(scale=2, width=200):
                    greedy_flag = gr.Checkbox(label="Greedy", value=True)
                    add_BOS = gr.Checkbox(label="Add BOS token", value=False)
                    token_to_gen = gr.Number(label='Number of Tokens to generate', value=30, type=int)
                    min_token_to_gen = gr.Number(label='Min number of Tokens to generate', value=1, type=int)
                    temperature = gr.Slider(minimum=0.0, maximum=10.0, value=1.0, label='Temperature', step=0.1)
                    top_p = gr.Slider(minimum=0.0, maximum=1.0, step=0.02, value=0.9, label='Top P')
                    top_k = gr.Slider(minimum=0, maximum=10000, step=2, value=0, label='Top K')
                    repetition_penality = gr.Slider(
                        minimum=1.0, maximum=5.0, step=0.02, value=1.2, label='Repetition penalty'
                    )
                    k_neighbors = gr.Slider(minimum=0, maximum=50, step=1, value=2, label='Retrieved Documents')
                    weight = gr.Slider(
                        minimum=0.0, maximum=1.0, value=1.0, label='Weight for the Static Retrieval DB', step=0.02
                    )
                    add_retrival_doc = gr.Textbox(label="Add New Retrieval Doc", value="", lines=5,)
                    add_EOS = gr.Checkbox(label="Add EOS token to Retrieval Doc", value=False)
                    with gr.Row():
                        add_btn = gr.Button(value="Add")
                        reset_btn = gr.Button(value="Reset Index")
                    output_status = gr.Label(value='')
                    add_btn.click(self.add_doc, inputs=[add_retrival_doc, add_EOS], outputs=[output_status])
                    reset_btn.click(self.reset_index, inputs=[], outputs=[output_status])

                with gr.Column(scale=1, min_width=800):
                    input_prompt = gr.Textbox(
                        label="Input",
                        value="Ariel was playing basketball. 1 of her shots went in the hoop. 2 of her shots did not go in the hoop. How many shots were there in total?",
                        lines=5,
                    )
                    output_box = gr.Textbox(value="", label="Output")
                    btn = gr.Button(value="Submit")
                    output_retrieval = gr.HTML()
                    btn.click(
                        self.get_retro_generation,
                        inputs=[
                            input_prompt,
                            greedy_flag,
                            add_BOS,
                            token_to_gen,
                            min_token_to_gen,
                            temperature,
                            top_p,
                            top_k,
                            repetition_penality,
                            k_neighbors,
                            weight,
                        ],
                        outputs=[output_box, output_retrieval],
                    )
        demo.launch(share=share, server_port=port, server_name='0.0.0.0', auth=(username, password))


class RetroQADemoWebApp(RetroDemoWebApp):
    def __init__(self, text_service_ip, text_service_port, combo_service_ip, combo_service_port, qa_service_ip, qa_service_port):
        super().__init__(text_service_ip, text_service_port, combo_service_ip, combo_service_port)
        self.qa_service_ip = qa_service_ip
        self.qa_service_port = qa_service_port

    def get_retro_generation(
        self, prompt, greedy, add_BOS, token_to_gen, min_tokens, temp, top_p, top_k, repetition, neighbors, weight
    ):
        data = {
            "sentences": [prompt],
            "tokens_to_generate": int(token_to_gen),
            "temperature": temp,
            "add_BOS": add_BOS,
            "top_k": top_k,
            "top_p": top_p,
            "greedy": greedy,
            "all_probs": False,
            "repetition_penalty": repetition,
            "min_tokens_to_generate": int(min_tokens),
            "neighbors": int(neighbors),
        }
        self.update_weight(weight)
        output_json = request_data(data, self.text_service_ip, self.text_service_port, path='generate')
        sentences = output_json['sentences']
        retrieved = output_json['retrieved']
        first_neighbor = ''.join([i['query'] for i in retrieved]).split('\nquestion:')[0]
        knowledges = [first_neighbor] + retrieved[0]['neighbors']
        answer = sentences[0].split('answer: ')[1]
        if self.qa_service_ip is not None:
            while True:
                try:
                    better_answer = request_post_data({'question': prompt, 'answer': answer}, ip=self.qa_service_ip, port=self.qa_service_port, path='decontext')['text'].strip()
                    break
                except:
                    time.sleep(5)
            qscore = request_post_data({'response': better_answer, 'knowledges': knowledges}, ip=self.qa_service_ip, port=self.qa_service_port, path='qsquare')
            # nli_scores = request_post_data({'responses': [better_answer] * len(knowledges), 'knowledges': knowledges}, ip=self.qa_service_ip, port=self.qa_service_port, path='nli')
            # selection = np.array(nli_scores).argmax(axis=1)
            # nli_labels = ['contradiction', 'entailment', 'neutral']
            # nli = [nli_labels[s] for s in selection]
            if max(qscore['scores']) < 0.5:
                sentences[0] = sentences[0] + ' (*WARNING* the answer is not based on the retrieved documents)'
        else:
            qscore = {'scores': [None] * len(knowledges)}
        return sentences[0], convert_qa_evidence_to_md(knowledges, qscore['scores'])
