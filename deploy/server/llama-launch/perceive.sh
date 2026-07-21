#!/bin/bash
# DejaView AMD-server launcher — perceive (Gemma 4 E4B Q8_0). Handbook §6.1.
# Mid-tier: screen understanding, Honcho deriver baseline. mmproj MUST be BF16.
# Resident ~8.4 GB VRAM. Kill: pkill -f "alias perceive".
set -euo pipefail
MODELS_DIR="${DEV_MODELS_DIR:-/root/dejaview-models}"
BIN="${LLAMA_BIN:-/root/llama.cpp/build/bin/llama-server}"
MODEL="$MODELS_DIR/perceive/gemma-4-E4B-it-Q8_0.gguf"
MMPROJ="$MODELS_DIR/perceive/mmproj-gemma-4-E4B-it-BF16.gguf"

exec "$BIN" \
  -m "$MODEL" --mmproj "$MMPROJ" \
  --alias perceive \
  -ngl 99 \
  -c 16384 -np 2 \
  --host 127.0.0.1 --port 8002 \
  --log-disable \
  --jinja
