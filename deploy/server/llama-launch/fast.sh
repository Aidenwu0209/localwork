#!/bin/bash
# DejaView AMD-server launcher — fast (MiniCPM5-1B Q8_0). Handbook §6.1.
# Fast-track text lane: novelty gate / event merge / tagging. --jinja required.
# Resident ~1.2 GB VRAM. Kill: pkill -f "alias fast".
set -euo pipefail
MODELS_DIR="${DEV_MODELS_DIR:-/root/dejaview-models}"
BIN="${LLAMA_BIN:-/root/llama.cpp/build/bin/llama-server}"
MODEL="$MODELS_DIR/fast/MiniCPM5-1B-Q8_0.gguf"

exec "$BIN" \
  -m "$MODEL" \
  --alias fast \
  -ngl 99 \
  -c 8192 -np 4 \
  --host 127.0.0.1 --port 8005 \
  --log-disable \
  --jinja
