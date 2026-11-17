#!/usr/bin/env bash
# Launch NuExtract3-FP8 with the Desktop vllm_env (host PM2, not k8s).
set -euo pipefail

REPO="${OCR_REPO:-/home/utec/ocr}"
VENV="${VLLM_VENV:-/home/utec/Desktop/vllm_env}"
MODEL_DIR="${VLLM_MODEL_DIR:-$REPO/models/NuExtract3-FP8}"
HOST="${VLLM_HOST:-0.0.0.0}"
PORT="${VLLM_PORT:-8000}"
MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-32768}"
# Six classifier few-shots plus the page being classified.
LIMIT_MM_PER_PROMPT='{"image": 8, "video": 0}'

if [[ ! -x "$VENV/bin/vllm" ]]; then
  echo "vLLM binary not found at $VENV/bin/vllm" >&2
  exit 1
fi
if [[ ! -f "$MODEL_DIR/config.json" ]]; then
  echo "Model checkpoint missing at $MODEL_DIR" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

export HF_HOME="${HF_HOME:-/home/utec/.cache/huggingface}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export VLLM_MODEL_DIR="$MODEL_DIR"

python "$REPO/deploy/vllm/check_checkpoint.py"

exec vllm serve "$MODEL_DIR" \
  --served-model-name numind/NuExtract3-FP8 \
  --host "$HOST" \
  --port "$PORT" \
  --trust-remote-code \
  --quantization compressed-tensors \
  --max-model-len "$MAX_MODEL_LEN" \
  --limit-mm-per-prompt "$LIMIT_MM_PER_PROMPT" \
  --chat-template-content-format openai \
  --generation-config vllm \
  --structured-outputs-config.backend xgrammar
