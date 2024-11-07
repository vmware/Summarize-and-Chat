# Copyright 2023-2024 Broadcom
# SPDX-License-Identifier: Apache-2.0

from langchain.llms.base import LLM
from langchain.callbacks.manager import CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun
from langchain.schema import Document
from langchain.chains import LLMChain
import time, os
from typing import List, Any, Optional
from openai import OpenAI
from llama_index.core.llms import (
    CustomLLM,
    CompletionResponse,
    CompletionResponseGen,
    LLMMetadata,
)
from llama_index.core.llms.callbacks import llm_completion_callback

from llama_index.core.indices.postprocessor import SentenceTransformerRerank
from llama_index.postprocessor.nvidia_rerank import NVIDIARerank
from llama_index.embeddings.nvidia import NVIDIAEmbedding

from src.config import logger
from src.utils.env import _env
from src.model.embedding import EmbeddingModel

os.environ['CURL_CA_BUNDLE'] = ''

llm_config = _env.get_llm_values()
llm_client = OpenAI(api_key = llm_config['API_KEY'], base_url = llm_config['API_BASE'])
defaultModel, modelValue = _env.get_default_model()
model_name: str = defaultModel

qa_config = _env.get_qamodel_values()
qa_client = OpenAI(api_key = qa_config['API_KEY'], base_url = qa_config['API_BASE'])

def completions(client, model: str, prompt: str, max_tokens: int = 1024,
                temperature: float = 0, stream: bool = True):
    try:
        response = client.completions.create(prompt=prompt,
                                            model=model,
                                            stream=stream,
                                            max_tokens=max_tokens,
                                            temperature=temperature)
        return response
    except Exception as e:
        logger.error(f'----request error------{e}')

# use chat/completion  endpoint, mistral model not support system role
def chat_completions(client, model: str, prompt: str, system_config: str, max_tokens: int = 1024,
                     temperature: float = 0, stream: bool = False):
    if model.startswith('mistral'):
        prefix = "<|im_start|>"
        suffix = "<|im_end|>\n"
        sys_format = prefix + "system\n" + system_config + suffix
        user_format = prefix + "user\n" + prompt + suffix
        assistant_format = prefix + "assistant\n"
        input_text = sys_format + user_format + assistant_format
        messages = [
            {"role": "user", "content": input_text},
        ]
    else:
        messages = [
            {"role": "system", "content": system_config},
            {"role": "user", "content": prompt},
        ]
    response = client.chat.completions.create(model=model,
                                              messages=messages,
                                              stream=stream,
                                              max_tokens=max_tokens,
                                              temperature=temperature)
    return response


# langchain custom LLM
class LCCustomLLM(LLM):
    model_name: str = defaultModel
    # model_name: str = config['MODEL']
    temperature: float = 0

    @property
    def _llm_type(self) -> str:
        return "custom"

    def _call(
            self,
            prompt: str,
            stop: Optional[List[str]] = None,
            run_manager: Optional[CallbackManagerForLLMRun] = None,
    ) -> str:
        if stop is not None:
            raise ValueError("stop kwargs are not permitted.")
        try:
            response = completions(client=llm_client,
                                   prompt=prompt,
                                   model=self.model_name,
                                   max_tokens=llm_config['MAX_COMPLETION'],
                                   temperature=self.temperature)
            result = response.choices[0].text
        except Exception as e:
            logger.error("llm call error: {}".format(str(e)))
            result = ''
        return result

    async def _acall(
            self,
            prompt: str,
            stop: Optional[List[str]] = None,
            run_manager: Optional[AsyncCallbackManagerForLLMRun] = None
    ) -> str:
        """Run the LLM on the given prompt and input."""
        if stop is not None:
            raise ValueError("stop kwargs are not permitted.")
        try:
            response = completions(client=llm_client,
                                   prompt=prompt,
                                   model=self.model_name,
                                   max_tokens=llm_config['MAX_COMPLETION'],
                                   temperature=self.temperature)
            result = response.choices[0].text
        except Exception as e:
            logger.error("llm acall error: {}".format(str(e)))
            result = ''
        return result

    # support all the model
    def tokens(text, method="max"):
        # method can be "average", "words", "chars", "max", "min", defaults to "max"
        # "average" is the average of words and chars
        # "words" is the word count divided by 0.75
        # "chars" is the char count divided by 4
        # "max" is the max of word and char
        # "min" is the min of word and char
        word_count = len(text.split(" "))
        char_count = len(text)
        tokens_count_word_est = word_count / 0.75
        tokens_count_char_est = char_count / 3.0
        output = 0
        if method == "average":
            output = (tokens_count_word_est + tokens_count_char_est) / 2
        elif method == "words":
            output = tokens_count_word_est
        elif method == "chars":
            output = tokens_count_char_est
        elif method == 'max':
            output = max([tokens_count_word_est, tokens_count_char_est])
        elif method == 'min':
            output = min([tokens_count_word_est, tokens_count_char_est])
        return output

    async def async_generate(chain: LLMChain, text: str, prompt: str, sem):
        async with sem:
            t = time.time()
            resp = await chain.arun(text=text, prompt=prompt)
            logger.info(f'---call vllm  finished---{time.time() - t}')
        return resp

    async def async_page_generate(chain: LLMChain, doc: Document, sem):
        async with sem:
            resp = await chain.arun(text=doc.page_content)
        result = {
            "summary": resp,
            "metadata": doc.metadata
        }
        return result


# llama_index custom LLM use for chat with document
class LocalLLM(CustomLLM):
    # config = _env.get_qamodel_values()
    context_window: int =  qa_config['MAX_TOKEN']
    num_output: int = qa_config['MAX_COMPLETION']
    model_name: str = qa_config['MODEL']
    dummy_response: str = "My response"

    @property
    def metadata(self) -> LLMMetadata:
        """Get LLM metadata."""
        return LLMMetadata(
            context_window=self.context_window,
            num_output=self.num_output,
            model_name=self.model_name,
        )

    @llm_completion_callback()
    def complete(self, prompt: str, **kwargs: Any) -> CompletionResponse:
        try:
            # logger.info(f'---request-----{prompt}')
            start = time.time()
            response = completions(client=qa_client,
                                    prompt=prompt,
                                    model=self.model_name,
                                    stream=False,
                                    max_tokens=self.num_output,
                                    temperature=0)
            result = response.choices[0].text
            logger.info(f'---call llm spend time-------{time.time() - start}')
        except Exception as e:
            logger.error(f'----request error------{e}')
            result = ''
        # logger.info(f'----request result-----{result}')
        return CompletionResponse(text=result)

    @llm_completion_callback()
    def stream_complete(
            self, prompt: str, **kwargs: Any
    ) -> CompletionResponseGen:
        logger.info(f'---stream call request--------{prompt}')

        def gen() -> CompletionResponseGen:
            text = ""
            for response in completions(client=qa_client, 
                                        prompt=prompt,
                                        model=self.model_name,
                                        stream=True,
                                        max_tokens=self.num_output,
                                        temperature=0):
                if len(response.choices) > 0:
                    delta = response.choices[0].text
                else:
                    delta = ""
                text += delta
                yield CompletionResponse(
                    delta=delta,
                    text=text,
                    raw=response,
                )

        return gen()


# generate the stream response
def call_stream(client,
                prompt: str,
                model: str = "mistralai/Mixtral-8x7B-Instruct-v0.1",
                max_tokens: int = 1024,
                temperature: float = 0,
                stream: bool = True):
    response = ''
    try:
        response = completions(client=client,
                               prompt=prompt,
                               model=model,
                               stream=stream,
                               max_tokens=max_tokens,
                               temperature=temperature)
    except Exception as e:
        logger.error("llm call error: {}".format(str(e)))
    return response

def get_rerank_model():
    reranker_config = _env.get_reranker_values()
    if not reranker_config["RERANK_ENABLED"]:
        return None

    model_name =  reranker_config["MODEL"]
    if model_name.startswith("nvidia"):
        re_ranker = NVIDIARerank(
            model=reranker_config['MODEL'],
            base_url=reranker_config['API_BASE'],
            api_key=reranker_config['API_KEY'],
            top_n=reranker_config['RERANK_TOP_N'],
            truncate="END",
        )
    else:
        re_ranker = SentenceTransformerRerank(
            model=reranker_config['MODEL'],
            top_n=reranker_config['RERANK_TOP_N'],
        )
    return re_ranker

def get_embedder():
    embedder_config = _env.get_embedder_values()
    model_name =  embedder_config["MODEL"]
    if model_name.startswith("nvidia"):
        embed_model = NVIDIAEmbedding(
            base_url=embedder_config['API_BASE'],
            model=embedder_config['MODEL'],
            embed_batch_size=embedder_config['BATCH_SIZE'],
            truncate="END",
        )
    else:
        embed_model = EmbeddingModel(
            model_name=embedder_config['MODEL'],
            embed_batch_size=embedder_config['BATCH_SIZE']
        )
    return embed_model, embedder_config["VECTOR_DIM"]