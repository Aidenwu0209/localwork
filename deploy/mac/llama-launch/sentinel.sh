#!/bin/bash
# DejaView dev llama-server launcher — sentinel (MiniCPM-V 4.6 Q4_K_M, Metal).
# Handbook §6.1 / §2.3: privacy gate, fast-lane vision. Classifies screenshots
# into password_prompt|banking_finance|private_chat|id_document|adult|normal.
# mmproj MUST be f16 (quantised projector has known crash/quality issues).
# Port 8003 matches deploy/server/litellm.yaml. ~1.6GB resident.
# Kill: pkill -f "alias sentinel".
set -euo pipefail
MODELS_DIR="${DEV_MODELS_DIR:-$HOME/Projects/Aidenwu0209/models/dejaview}"
MODEL="$MODELS_DIR/sentinel/MiniCPM-V-4_6-Q4_K_M.gguf"
MMPROJ="$MODELS_DIR/sentinel/mmproj-model-f16.gguf"

exec llama-server \
  -m "$MODEL" --mmproj "$MMPROJ" \
  --alias sentinel \
  -ngl 99 \
  -c 4096 -np 4 \
  --host 127.0.0.1 --port 8003 \
  --log-disable \
  --jinja
