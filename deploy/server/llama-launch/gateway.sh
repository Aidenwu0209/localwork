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
# (once). Stop through server-stack.sh so only the tracked process is signalled.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${LLITELLM_VENV:-/root/llamavenv}"
RUNTIME_DIR="${DEJAVIEW_RUNTIME_DIR:-/tmp/dejaview}"
CONF="$RUNTIME_DIR/litellm.server.yaml"

mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"

# Generate the server config from the shared logical names, pointing brain at
# the real 27B instance on :8001 (overriding the dev dual-map).
cat > "$CONF" <<'YAML'
model_list:
  - model_name: brain
    litellm_params: { model: openai/brain, api_base: http://127.0.0.1:8001/v1, api_key: "none" }
  - model_name: perceive
    litellm_params: { model: openai/perceive, api_base: http://127.0.0.1:8002/v1, api_key: "none", extra_body: { chat_template_kwargs: { enable_thinking: false } } }
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

exec "$VENV/bin/litellm" --config "$CONF" --host 127.0.0.1 --port 4000
