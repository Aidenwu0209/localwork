#!/bin/bash
# DejaView dev llama-server launcher — fast (MiniCPM5-1B Q8_0, Metal).
# Handbook §6.1 / §2.3: fast-track text lane. novelty gate / event merge /
# tagging / proactive-trigger prefilter; Honcho deriver candidate (T0.9 A/B).
# --jinja required for MiniCPM5 per official llama.cpp cookbook.
# Port 8005 matches deploy/server/litellm.yaml. ~1.2GB resident.
# Kill: pkill -f "alias fast".
set -euo pipefail
MODELS_DIR="${DEV_MODELS_DIR:-$HOME/Projects/Aidenwu0209/models/dejaview}"
MODEL="$MODELS_DIR/fast/MiniCPM5-1B-Q8_0.gguf"

exec llama-server \
  -m "$MODEL" \
  --alias fast \
  -ngl 99 \
  -c 8192 -np 4 \
  --host 127.0.0.1 --port 8005 \
  --log-disable \
  --jinja
