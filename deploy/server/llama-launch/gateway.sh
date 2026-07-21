#!/bin/bash
# DejaView AMD-server LiteLLM gateway (:4000). Routes the five logical names to
# the local llama-server instances. Config: deploy/server/litellm.yaml.
#
# IMPORTANT: the dev (Mac) litellm.yaml dual-maps brain -> perceive because the
# 27B doesn't fit on a 16 GB laptop. On the server we override `brain` to point
# at the dedicated :8001 ThinkingCap-27B instance via a server-specific config
# generated here (litellm.server.yaml). See handbook §3.
#
# Run inside the llamavenv: /root/llamavenv/bin/pip install 'litellm[proxy]'
# (once). Kill: pkill -f "litellm.server".
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${LLITELLM_VENV:-/root/llamavenv}"
CONF="/tmp/dejaview-litellm.server.yaml"

# Generate the server config from the shared logical names, pointing brain at
# the real 27B instance on :8001 (overriding the dev dual-map).
cat > "$CONF" <<'YAML'
model_list:
  - model_name: brain
    litellm_params: { model: openai/brain, api_base: http://127.0.0.1:8001/v1, api_key: "none" }
  - model_name: perceive
    litellm_params: { model: openai/perceive, api_base: http://127.0.0.1:8002/v1, api_key: "none" }
  - model_name: sentinel
    litellm_params: { model: openai/sentinel, api_base: http://127.0.0.1:8003/v1, api_key: "none" }
  - model_name: fast
    litellm_params: { model: openai/fast, api_base: http://127.0.0.1:8005/v1, api_key: "none" }
  - model_name: embed
    litellm_params: { model: openai/embed, api_base: http://127.0.0.1:8004/v1, api_key: "none" }
litellm_settings:
  drop_params: true
  request_timeout: 300
general_settings:
  disable_spend_logs: true
YAML

exec "$VENV/bin/litellm" --config "$CONF" --host 0.0.0.0 --port 4000
