#!/bin/bash
# DejaView AMD-server launcher — sentinel (MiniCPM-V 4.6 Q4_K_M). Handbook §6.1.
# Privacy gate, fast-lane vision. mmproj MUST be f16 (quantised projector breaks).
# Resident ~1.6 GB VRAM. Kill: pkill -f "alias sentinel".
set -euo pipefail
MODELS_DIR="${DEV_MODELS_DIR:-/root/dejaview-models}"
BIN="${LLAMA_BIN:-/root/llama.cpp/build/bin/llama-server}"
MODEL="$MODELS_DIR/sentinel/MiniCPM-V-4_6-Q4_K_M.gguf"
MMPROJ="$MODELS_DIR/sentinel/mmproj-model-f16.gguf"

exec "$BIN" \
  -m "$MODEL" --mmproj "$MMPROJ" \
  --alias sentinel \
  -ngl 99 \
  -c 4096 -np 4 \
  --host 127.0.0.1 --port 8003 \
  --log-disable \
  --jinja
