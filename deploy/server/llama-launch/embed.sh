#!/bin/bash
# DejaView AMD-server llama-server launcher — embed (Qwen3-Embedding-0.6B Q8_0).
# Built from source at /root/llama.cpp (HIP, gfx1100). Handbook §6.1.
# Weights at /root/dejaview-models/embed/. Port 8004 matches deploy/server/litellm.yaml.
# Resident ~0.7 GB VRAM. Kill: pkill -f "alias embed".
set -euo pipefail
MODELS_DIR="${DEV_MODELS_DIR:-/root/dejaview-models}"
BIN="${LLAMA_BIN:-/root/llama.cpp/build/bin/llama-server}"
MODEL="$MODELS_DIR/embed/Qwen3-Embedding-0.6B-Q8_0.gguf"

exec "$BIN" \
  -m "$MODEL" \
  --alias embed \
  -ngl 99 \
  -c 8192 \
  --embedding --pooling last \
  --host 127.0.0.1 --port 8004 \
  --log-disable \
  --jinja
