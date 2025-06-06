#
# Copyright 2016 The BigDL Authors.
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
#
import torch
from typing import Optional, Union
from vllm.distributed import tensor_model_parallel_gather, tensor_model_parallel_all_gather
from vllm.logger import init_logger
from vllm.model_executor.models.llama import LlamaMLP, LlamaAttention, LlamaForCausalLM
from vllm.model_executor.models.qwen2 import Qwen2MLP, Qwen2Attention, Qwen2ForCausalLM
from vllm.model_executor.models.qwen import QWenMLP, QWenAttention, QWenLMHeadModel
from vllm.model_executor.models.baichuan import BaiChuanMLP, BaiChuanAttention
from vllm.model_executor.models.baichuan import BaiChuanBaseForCausalLM
from vllm.model_executor.models.chatglm import GLMMLP, GLMAttention, ChatGLMForCausalLM
from vllm.model_executor.model_loader import get_model
from vllm.model_executor.layers.vocab_parallel_embedding import (
    VocabParallelEmbedding)
from vllm.attention import AttentionMetadata
from vllm.config import DeviceConfig
from typing import Tuple
from ipex_llm.transformers.low_bit_linear import LowBitLinear


def _sample_get_logits(
    self,
    hidden_states: torch.Tensor,
    lm_head: Union[VocabParallelEmbedding, LowBitLinear],
    embedding_bias: Optional[torch.Tensor],
) -> torch.Tensor:
    # HINT: we do not support other types of quantization for now
    # TODO: we may encounter tie-word-embedding problems
    if isinstance(lm_head, VocabParallelEmbedding):
        logits = lm_head.quant_method.apply(lm_head,
                                            hidden_states,
                                            bias=embedding_bias)
    else:
        logits = lm_head(hidden_states)
        if embedding_bias is not None:
            logits += embedding_bias
    if self.use_all_gather:
        logits = tensor_model_parallel_gather(logits)
    else:
        logits = tensor_model_parallel_all_gather(logits)
    if logits is not None:
        logits = logits[:, : self.org_vocab_size]
    return logits


def _model_sample_convert():
    from vllm.model_executor.layers.logits_processor import LogitsProcessor
    setattr(LogitsProcessor, "_get_logits", _sample_get_logits)


def _ipex_llm_convert(load_in_low_bit):
    # import pdb
    # pdb.set_trace()
    from vllm.worker.xpu_model_runner import XPUModelRunner, XPUModelRunnerBase
    from ipex_llm.vllm.xpu.ipex_llm_wrapper import get_ipex_llm_wrapper
    from ipex_llm.vllm.xpu.ipex_llm_v1_wrapper import get_ipex_llm_v1_wrapper
    import vllm.executor.ray_utils as ray_utils_v0
    import vllm.v1.executor.ray_utils as ray_utils_v1
    from vllm.v1.worker.gpu_model_runner import GPUModelRunner
    setattr(XPUModelRunner, "load_model", get_load_function(load_in_low_bit))
    setattr(XPUModelRunnerBase, "load_model", get_load_function(load_in_low_bit))
    setattr(GPUModelRunner, "load_model", get_load_function(load_in_low_bit))
    setattr(ray_utils_v0, "RayWorkerWrapper", get_ipex_llm_wrapper(load_in_low_bit))
    setattr(ray_utils_v1, "RayWorkerWrapper", get_ipex_llm_v1_wrapper(load_in_low_bit))


def get_load_function(low_bit):
    def _ipex_llm_load_model(self) -> None:
        if "gemma-3" not in self.model_config.model.lower():
            _model_sample_convert()

        # from vllm.utils import measure_device_memory
        from vllm.utils import DeviceMemoryProfiler
        with DeviceMemoryProfiler() as m:
            import os
            from dataclasses import replace
            new_device_config = DeviceConfig("cpu")
            new_vllm_config = replace(self.vllm_config, device_config=new_device_config)
            # We are loading an low-bit model, where all the optimizations should have been
            # applied...
            # We can skip the following optimizations
            self.model = get_model(
                vllm_config=new_vllm_config
            )
            if self.vllm_config.model_config.low_bit_model_path is None:
                if ("qwen" in self.vllm_config.model_config.model.lower() or
                        "baichuan" in self.vllm_config.model_config.model.lower() or
                        "codegeex4-all" in self.vllm_config.model_config.model.lower() or
                        "chatglm" in self.vllm_config.model_config.model.lower()) and \
                        "gptq" not in self.model_config.model.lower() and \
                        "awq" not in self.model_config.model.lower() and \
                        "qwen3" not in self.model_config.model.lower():
                    self.model.apply(padding_mlp)
                from ipex_llm import optimize_model
                not_convert_last_mlp = os.getenv("IPEX_LLM_NOT_CONVERT_LAST_MLP", None)
                if not_convert_last_mlp is not None:
                    # only use to avoid nan value in last mlp forward running glm4-9b-chat
                    modules = ["35.mlp", "36.mlp", "37.mlp", "38.mlp", "39.mlp"]
                else:
                    modules = None
                not_convert_o_proj = os.getenv("IPEX_LLM_NOT_CONVERT_O_PROJ", None)
                if not_convert_o_proj is not None:
                    # only use to avoid nan value in o_proj running DeepSeek-R1-Distill-Qwen-14B
                    modules = ["o_proj"]
                else:
                    modules = None
                if "minicpm" in self.vllm_config.model_config.model.lower():
                    modules = ["vpm", "resampler"]
                if "internvl2" in self.vllm_config.model_config.model.lower():
                    modules = ["vision_model", "mlp1"]
                if "deepseek-v2" in self.vllm_config.model_config.model.lower():
                    modules = ["down_proj"]
                if "whisper" in self.vllm_config.model_config.model.lower():
                    modules = ["proj_out"]
                if "glm-4v" in self.vllm_config.model_config.model.lower() and \
                        low_bit in ("sym_int4", "woq_int4"):
                    modules = ["dense_4h_to_h"]
                if "phi4mm" in self.vllm_config.model_config.hf_config.model_type:
                    modules = ["vision_encoder", "embed_tokens_extend"]
                if low_bit == "fp16":
                    # to fix qwen2.5-vl and glm-4v
                    if modules is None:
                        modules = ["vision", "visual"]
                    else:
                        modules.append("vision")
                        modules.append("visual")
                optimize_model(self.model,
                               low_bit=low_bit,
                               torch_dtype=self.vllm_config.model_config.dtype,
                               modules_to_not_convert=modules)
            # Guancheng: We have to save the model before moving it to the XPU device.
            # The `to` method will convert the underlying data.
            # Saving it before will help to avoid converting two times.
            if self.vllm_config.model_config.low_bit_save_path is not None:
                # The local_rank is used for loading models with tensor parallel settings.
                local_rank = os.environ["LOCAL_RANK"]
                saved_path = os.path.join(self.vllm_config.model_config.low_bit_save_path,
                                          str(local_rank))
                self.model.save_low_bit(saved_path)

            self.model = self.model.to(device=self.vllm_config.device_config.device,
                                       dtype=self.vllm_config.model_config.dtype)

        self.model_memory_usage = m.consumed_memory
        logger = init_logger(__name__)
        logger.info("Loading model weights took %.4f GB",
                    self.model_memory_usage / float(2**30))

    return _ipex_llm_load_model


def padding_mlp(module: torch.nn.Module):
    mlp_gate_up_name = None
    mlp_down_name = None
    if isinstance(module, Qwen2MLP):
        mlp_gate_up_name = "gate_up_proj"
        mlp_down_name = "down_proj"
    elif isinstance(module, GLMMLP):
        mlp_gate_up_name = "dense_h_to_4h"
        mlp_down_name = "dense_4h_to_h"
    elif isinstance(module, BaiChuanMLP):
        mlp_gate_up_name = "gate_up_proj"
        mlp_down_name = "down_proj"
    else:
        return
    hidden_size = getattr(module, mlp_down_name).output_size
    # devide by rank
    intermediate_size = getattr(module, mlp_down_name).input_size_per_partition
    padding_size = 256
    padding_intermediate_size = \
        (intermediate_size + padding_size - 1) // padding_size * padding_size
    if intermediate_size % padding_size == 0:
        return
    gate_up_weight = getattr(module, mlp_gate_up_name).weight.data
    new_gate_up_weight = torch.zeros([padding_intermediate_size * 2, hidden_size],
                                     dtype=gate_up_weight.dtype, device=gate_up_weight.device)
    # merge_gate_up_weight
    new_gate_up_weight[:intermediate_size, :] = gate_up_weight[:intermediate_size, :]
    new_gate_up_weight[padding_intermediate_size:padding_intermediate_size+intermediate_size, :] = gate_up_weight[intermediate_size:, :]  # noqa
    getattr(module, mlp_gate_up_name).output_size_per_partition = padding_intermediate_size * 2
    getattr(module, mlp_gate_up_name).output_size = padding_intermediate_size * 2
    getattr(module, mlp_gate_up_name).weight = \
        torch.nn.Parameter(new_gate_up_weight, requires_grad=False)

    down_weight = getattr(module, mlp_down_name).weight.data
    new_down_weight = torch.zeros([hidden_size, padding_intermediate_size],
                                  dtype=down_weight.dtype, device=down_weight.device)
    new_down_weight[:, :intermediate_size] = down_weight
    getattr(module, mlp_down_name).input_size_per_partition = padding_intermediate_size
    getattr(module, mlp_down_name).input_size = padding_intermediate_size
    getattr(module, mlp_down_name).weight = torch.nn.Parameter(new_down_weight, requires_grad=False)
