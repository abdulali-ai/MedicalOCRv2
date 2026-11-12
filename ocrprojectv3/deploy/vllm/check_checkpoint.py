#!/usr/bin/env python3
"""Refuse to serve unless the NuExtract3-FP8 checkpoint config matches this deployment.

Reads config.json from Hugging Face. Does not download weights.
MTP is reported and then left disabled: model_mtp.safetensors is in the repo and
text_config.mtp_num_hidden_layers is 1, but speculative decoding on this FP8
build is not turned on.
"""
from __future__ import annotations

import json
import sys
import urllib.request

CONFIG_URL = "https://huggingface.co/numind/NuExtract3-FP8/resolve/main/config.json"
MAX_MODEL_LEN = 32768


def main() -> None:
    try:
        with urllib.request.urlopen(CONFIG_URL, timeout=60) as response:
            config = json.load(response)
    except Exception as exc:
        sys.exit(f"NuExtract3-FP8 config.json is missing or unreadable: {exc}")

    if config.get("model_type") != "qwen3_5":
        sys.exit(f"Unexpected model_type: {config.get('model_type')}")

    quant = config.get("quantization_config") or {}
    if quant.get("quant_method") != "compressed-tensors":
        sys.exit(f"Expected compressed-tensors FP8 quantization, got {quant.get('quant_method')}")

    vision = config.get("vision_config") or {}
    if vision.get("patch_size") != 16 or vision.get("spatial_merge_size") != 2:
        sys.exit(
            "Vision preprocessor does not match the recorded budget "
            f"(patch_size={vision.get('patch_size')}, spatial_merge_size={vision.get('spatial_merge_size')})"
        )

    text = config.get("text_config") or {}
    max_pos = int(text.get("max_position_embeddings") or 0)
    if max_pos < MAX_MODEL_LEN:
        sys.exit(f"Checkpoint context {max_pos} is smaller than --max-model-len {MAX_MODEL_LEN}")

    print(
        "checkpoint ok:"
        f" quantization={quant.get('quant_method')}"
        f" patch={vision.get('patch_size')}"
        f" merge={vision.get('spatial_merge_size')}"
        f" max_position_embeddings={max_pos}"
        f" serving_max_model_len={MAX_MODEL_LEN}"
        f" mtp_num_hidden_layers={text.get('mtp_num_hidden_layers')}"
        " mtp_enabled=false"
    )


if __name__ == "__main__":
    main()
