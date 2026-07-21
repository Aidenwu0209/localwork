#!/bin/bash
# DejaView AMD-server launcher — brain (ThinkingCap-Qwen3.6-27B).
# Deep tier: reasoning / planning / deep vision / writing. Resident ~28 GB Q8_0
# or ~21 GB Q6_K. Handbook §6.1.
#
# SHARED-GPU MODE: this server also runs another job (Dolphin ~10.6 GB VRAM).
# Q8_0 (28 GB) + Dolphin + the常驻 four (~12 GB) = ~50.6 GB > 48 GB → OOM.
# Default to Q6_K (~21 GB) so total stays ~43 GB (4.6 GB headroom). Override
# with BRAIN_QUANT=Q8_0 when the GPU is free. Kill: pkill -f "alias brain".
set -euo pipefail
MODELS_DIR="${DEV_MODELS_DIR:-/root/dejaview-models}"
BIN="${LLAMA_BIN:-/root/llama.cpp/build/bin/llama-server}"
QUANT="${BRAIN_QUANT:-Q6_K}"

if [ "$QUANT" = "Q8_0" ]; then
  MODEL="$MODELS_DIR/brain/ThinkingCap-Qwen3.6-27B-Q8_0.gguf"
  CTX=32768
else
  # Q6_K falls back to Q8_0 if the Q6 file is missing (so this script is usable
  # before the Q6 download finishes — prints a clear note).
  MODEL="$MODELS_DIR/brain/ThinkingCap-Qwen3.6-27B-Q6_K.gguf"
  if [ ! -f "$MODEL" ]; then
    echo "WARNING: $MODEL missing, falling back to Q8_0 (watch VRAM vs Dolphin)" >&2
    MODEL="$MODELS_DIR/brain/ThinkingCap-Qwen3.6-27B-Q8_0.gguf"
    QUANT=Q8_0
  fi
  CTX=32768
fi
MMPROJ="$MODELS_DIR/brain/mmproj-ThinkingCap-Qwen3.6-27B-f16.gguf"

echo "brain: serving $QUANT ($MODEL)" >&2
exec "$BIN" \
  -m "$MODEL" --mmproj "$MMPROJ" \
  --alias brain \
  -ngl 99 \
  -c "$CTX" -np 2 \
  --host 127.0.0.1 --port 8001 \
  --log-disable \
  --jinja
