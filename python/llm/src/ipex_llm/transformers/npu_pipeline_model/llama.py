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
import numpy as np
import os
from .common import update_names_of_IR_and_export_blob, LowBitLLMLMHead, \
    obtain_weight_from_single_layer, obtain_embedding_from_model


def convert_lm_head_and_embedding(model, n_splits_linear, temp_dir, weight_dir,
                                  convert_model=False, max_prompt_len=1,
                                  keep_ir=False, compile_blob=True):
    num_heads = model.model.layers[0].self_attn.num_heads
    num_key_value_heads = model.model.layers[0].self_attn.num_key_value_heads
    head_dim = model.model.layers[0].self_attn.head_dim
    rms_norm_eps = model.config.rms_norm_eps
    vocab_size = model.config.vocab_size
    model_norm = model.model.norm
    lm_head = model.lm_head
    asym = getattr(model.config, "asym", False)
    if n_splits_linear == 1:
        asym = lm_head.qtype == "asym_int4_rtn"
        if asym:
            weights = [(lm_head.weight, lm_head.scale, lm_head.zero)]
        else:
            weights = [(lm_head.weight, lm_head.scale)]
    else:
        lm_heads = lm_head.lm_heads
        asym = lm_heads[0].qtype == "asym_int4_rtn"
        lm_head_weights = []
        scales = []
        zeros = []
        for l in lm_heads:
            lm_head_weights.append(l.weight)
            scales.append(l.scale)
            if l.zero is not None:
                zeros.append(l.zero)
        if len(zeros):
            weights = [(torch.stack(lm_head_weights, axis=0),
                        torch.stack(scales, axis=0),
                        torch.stack(zeros, axis=0))]
        else:
            weights = [(torch.stack(lm_head_weights, axis=0),
                        torch.stack(scales, axis=0))]
    if isinstance(weights[0], tuple):
        np_dtype = np.int8 if weights[0][0].dtype == torch.int8 else np.uint8
    else:  # FP16 Linear
        np_dtype = np.float16

    new_lm_head = LowBitLLMLMHead(
        [1, 1, num_heads * head_dim],
        num_heads=num_heads,
        max_seq_len=1,
        rms_norm_eps=rms_norm_eps,
        mode="decode",
        transpose_value=False,
        dtype=np_dtype,
        model_norm_weight=model_norm.weight.to(torch.float16),
        vocab_size=vocab_size,
        n_splits=n_splits_linear,
        asym=asym
    )
    last_blob_path = update_names_of_IR_and_export_blob(new_lm_head, "lm_head", temp_dir,
                                                        keep_ir=keep_ir, compile_blob=compile_blob)
    os.remove(os.path.join(temp_dir, "lm_head.bin"))

    # save weights bins files
    if n_splits_linear == 1:
        if not asym:
            weight_numpy = [
                lm_head.weight.data.numpy(), lm_head.scale.data.numpy(),
            ]
        else:
            weight_numpy = [
                lm_head.weight.data.numpy(), lm_head.scale.data.numpy(),
                lm_head.zero.data.numpy()
            ]
    else:
        weight_numpy = [v.numpy() for v in weights[0]]

    for idx, weight in enumerate(weight_numpy):
        bin_file = os.path.join(weight_dir, f"model_lm_head_input_{1+idx}.bin")
        weight.tofile(bin_file)

    first_blob_path = obtain_embedding_from_model(model, convert_model,
                                                  temp_dir, weight_dir,
                                                  max_prompt_len,
                                                  keep_ir, compile_blob)

    return first_blob_path, last_blob_path


def convert_llama_layer(model, layer_idx, n_splits_linear, n_splits_down_proj,
                        temp_dir, weight_dir, transpose_value_cache, kv_len, group_size,
                        const_parameter, mode="decode",
                        keep_ir=False, compile_blob=True):
    num_heads = model.model.layers[0].self_attn.num_heads
    num_key_value_heads = model.model.layers[0].self_attn.num_key_value_heads
    head_dim = model.model.layers[0].self_attn.head_dim
    intermediate_size = model.config.intermediate_size
    rms_norm_eps = model.config.rms_norm_eps
    asym = getattr(model.config, "asym", False)

    from ipex_llm.transformers.npu_models.llama_mp import LowBitLlamaMultiDecoderlayer
    curr_layer = model.model.layers[layer_idx]
    attn_layer = curr_layer.self_attn
    mlp_layer = curr_layer.mlp
    weights = obtain_weight_from_single_layer(attn_layer, mlp_layer)
    if hasattr(curr_layer.self_attn.rotary_emb, "cos_cached"):
        # llama-2-7B & llama-3-8B
        cached_cos = curr_layer.self_attn.rotary_emb.cos_cached.to(torch.float16)
        cached_sin = curr_layer.self_attn.rotary_emb.sin_cached.to(torch.float16)
    else:
        # llama-3.2-3B & llama-3.2-1B
        cached_cos = None
        cached_sin = None
    layer_norm_0 = curr_layer.input_layernorm.weight.to(torch.float16)
    layer_norm_1 = curr_layer.post_attention_layernorm.weight.to(torch.float16)

    if isinstance(weights[0], tuple):
        np_dtype = np.int8 if weights[0][0].dtype == torch.int8 else np.uint8
    else:  # FP16 Linear
        np_dtype = np.float16

    if mode == "decode":
        input_len = 1
        decoder_name = f"decoder_layer_{layer_idx}"
        keep_position_ids = True
        npu_dpu_groups = None
    else:
        input_len = kv_len
        decoder_name = "decoder_layer_prefill"
        const_parameter = False
        keep_position_ids = False
        npu_dpu_groups = 6

    single_decoder = LowBitLlamaMultiDecoderlayer(
        [1, input_len, num_heads * head_dim],
        input_layernorm_weights=[layer_norm_0] if const_parameter else None,
        post_attn_layernorm_weights=[layer_norm_1] if const_parameter else None,
        cached_cos=cached_cos,
        cached_sin=cached_sin,
        num_heads=num_heads,
        num_key_value_heads=num_key_value_heads,
        num_layers=1,
        max_seq_len=kv_len,
        rms_norm_eps=rms_norm_eps,
        intermediate_size=intermediate_size,
        mode=mode,
        transpose_value=transpose_value_cache,
        dtype=np_dtype,
        n_splits_linear=n_splits_linear,
        n_splits_down_proj=n_splits_down_proj,
        group_size=group_size,
        cos_len=input_len,
        keep_position_ids=keep_position_ids,
        asym=asym
    )

    rest_blob_path = update_names_of_IR_and_export_blob(single_decoder,
                                                        decoder_name,
                                                        temp_dir,
                                                        keep_ir=keep_ir, compile_blob=compile_blob,
                                                        npu_dpu_groups=npu_dpu_groups)
    os.remove(os.path.join(temp_dir, decoder_name + ".bin"))

    if mode == "decode":
        if hasattr(curr_layer.self_attn.rotary_emb, "cos_cached"):
            # llama-2-7B & llama-3-8B
            if const_parameter:
                st_idx = 5
            else:
                input_lm_bin_file = os.path.join(weight_dir, f"model_{layer_idx}_input_3.bin")
                post_lm_bin_file = os.path.join(weight_dir, f"model_{layer_idx}_input_4.bin")
                layer_norm_0.data.numpy().tofile(input_lm_bin_file)
                layer_norm_1.data.numpy().tofile(post_lm_bin_file)
                st_idx = 7
        else:
            # llama-3.2-3B & llama-3.2-1B
            if const_parameter:
                st_idx = 6
            else:
                input_lm_bin_file = os.path.join(weight_dir, f"model_{layer_idx}_input_4.bin")
                post_lm_bin_file = os.path.join(weight_dir, f"model_{layer_idx}_input_5.bin")
                layer_norm_0.data.numpy().tofile(input_lm_bin_file)
                layer_norm_1.data.numpy().tofile(post_lm_bin_file)
                st_idx = 8
        if not asym:
            for idx, (weight, scale) in enumerate(weights):
                bin_file = os.path.join(weight_dir, f"model_{layer_idx}_input_{st_idx+idx*2}.bin")
                weight.numpy().tofile(bin_file)
                bin_file = os.path.join(weight_dir,
                                        f"model_{layer_idx}_input_{st_idx+idx*2+1}.bin")
                scale.numpy().tofile(bin_file)
        else:
            for idx, (weight, scale, zero) in enumerate(weights):
                bin_file = os.path.join(weight_dir, f"model_{layer_idx}_input_{st_idx+idx*3}.bin")
                weight.numpy().tofile(bin_file)
                bin_file = os.path.join(weight_dir,
                                        f"model_{layer_idx}_input_{st_idx+idx*3+1}.bin")
                scale.numpy().tofile(bin_file)
                bin_file = os.path.join(weight_dir,
                                        f"model_{layer_idx}_input_{st_idx+idx*3+2}.bin")
                zero.numpy().tofile(bin_file)

        del single_decoder


def convert_fused_llama_layer(model, fused_layers, n_splits_linear, n_splits_down_proj,
                              save_dir, weight_dir, transpose_value_cache, kv_len, group_size,
                              const_parameter, mode="decode",
                              keep_ir=False, compile_blob=True):
    num_heads = model.model.layers[0].self_attn.num_heads
    num_key_value_heads = model.model.layers[0].self_attn.num_key_value_heads
    head_dim = model.model.layers[0].self_attn.head_dim
    intermediate_size = model.config.intermediate_size
    rms_norm_eps = model.config.rms_norm_eps
    layer_num = len(model.model.layers)
    fused_layer_num = layer_num // fused_layers
    asym = getattr(model.config, "asym", False)

    from ipex_llm.transformers.npu_models.llama_mp import LowBitLlamaMultiDecoderlayer
    for i in range(fused_layers):
        layer_start = i * fused_layer_num
        layer_end = min((i + 1) * fused_layer_num, layer_num)
        layer_weights = []
        input_layer_norm_weights = []
        post_attn_layernorm_weights = []
        layer_indexs = range(layer_start, layer_end)
        for layer_idx in layer_indexs:
            curr_layer = model.model.layers[layer_idx]
            attn_layer = curr_layer.self_attn
            mlp_layer = curr_layer.mlp
            weights = obtain_weight_from_single_layer(attn_layer, mlp_layer)
            if hasattr(curr_layer.self_attn.rotary_emb, "cos_cached"):
                # llama-2-7B & llama-3-8B
                cached_cos = curr_layer.self_attn.rotary_emb.cos_cached.to(torch.float16)
                cached_sin = curr_layer.self_attn.rotary_emb.sin_cached.to(torch.float16)
            else:
                # llama-3.2-3B & llama-3.2-1B
                cached_cos = None
                cached_sin = None
            layer_norm_0 = curr_layer.input_layernorm.weight.to(torch.float16)
            layer_norm_1 = curr_layer.post_attention_layernorm.weight.to(torch.float16)

            layer_weights.extend(weights)
            input_layer_norm_weights.append(layer_norm_0)
            post_attn_layernorm_weights.append(layer_norm_1)

            # save weight
            input_lm_bin_file = os.path.join(weight_dir, f"model_{layer_idx}_input_3.bin")
            post_lm_bin_file = os.path.join(weight_dir, f"model_{layer_idx}_input_4.bin")
            layer_norm_0.data.numpy().tofile(input_lm_bin_file)
            layer_norm_1.data.numpy().tofile(post_lm_bin_file)
            st_idx = 5
            # 6, 7 are past k/v
            if not asym:
                for idx, (weight, scale) in enumerate(weights):
                    bin_file = os.path.join(weight_dir,
                                            f"model_{layer_idx}_input_{st_idx+idx*2}.bin")
                    weight.numpy().tofile(bin_file)
                    bin_file = os.path.join(weight_dir,
                                            f"model_{layer_idx}_input_{st_idx+idx*2+1}.bin")
                    scale.numpy().tofile(bin_file)
            else:
                for idx, (weight, scale, zero) in enumerate(weights):
                    bin_file = os.path.join(weight_dir,
                                            f"model_{layer_idx}_input_{st_idx+idx*3}.bin")
                    weight.numpy().tofile(bin_file)
                    bin_file = os.path.join(weight_dir,
                                            f"model_{layer_idx}_input_{st_idx+idx*3+1}.bin")
                    scale.numpy().tofile(bin_file)
                    bin_file = os.path.join(weight_dir,
                                            f"model_{layer_idx}_input_{st_idx+idx*3+2}.bin")
                    zero.numpy().tofile(bin_file)

        if isinstance(weights[0], tuple):
            np_dtype = np.int8 if weights[0][0].dtype == torch.int8 else np.uint8
        else:  # FP16 Linear
            np_dtype = np.float16

        if not const_parameter:
            input_layer_norm_weights = None
            post_attn_layernorm_weights = None

        fused_decoder = LowBitLlamaMultiDecoderlayer(
            [1, 1, num_heads * head_dim],
            input_layernorm_weights=input_layer_norm_weights,
            post_attn_layernorm_weights=post_attn_layernorm_weights,
            cached_cos=cached_cos,
            cached_sin=cached_sin,
            num_heads=num_heads,
            num_key_value_heads=num_key_value_heads,
            num_layers=fused_layer_num,
            max_seq_len=kv_len,
            rms_norm_eps=rms_norm_eps,
            intermediate_size=intermediate_size,
            mode=mode,
            transpose_value=transpose_value_cache,
            dtype=np_dtype,
            n_splits_linear=n_splits_linear,
            n_splits_down_proj=n_splits_down_proj,
            group_size=group_size,
            asym=asym
        )
        update_names_of_IR_and_export_blob(fused_decoder,
                                           f"decoder_layer_{i}",
                                           save_dir,
                                           keep_ir=keep_ir,
                                           compile_blob=compile_blob)
        os.remove(os.path.join(save_dir, f"decoder_layer_{i}" + ".bin"))
    return 0
