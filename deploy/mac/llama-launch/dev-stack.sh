#!/bin/bash
# DejaView dev inference stack controller (Apple M5 / 16GB).
#
# Usage:
#   ./dev-stack.sh up <role...>   # start llama-servers + gateway (roles space-sep)
#   ./dev-stack.sh down           # stop everything
#   ./dev-stack.sh status         # what's running
#   ./dev-stack.sh smoke          # hit each logical name once via :4000
#
# Roles: sentinel fast embed perceive (brain is dev-mapped to perceive, so never
# start brain separately). Gateway always starts on `up`. Examples:
#   ./dev-stack.sh up embed fast sentinel        # ~3.5GB, the small trio
#   ./dev-stack.sh up perceive                   # ~5.5GB, brain+perceive share it
#   ./dev-stack.sh up embed fast sentinel perceive  # ~9GB, full dev pyramid
#
# Per-instance logs land in /tmp/dejaview-<role>.log. Kill individuals with the
# pkill hint in each *.sh header.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG=/tmp

role_pidfile() { echo "/tmp/dejaview-$1.pid"; }
gw_pidfile()   { echo "/tmp/dejaview-gateway.pid"; }

is_running() {  # <pidfile>
  [[ -f "$1" ]] && kill -0 "$(cat "$1")" 2>/dev/null
}

start_role() {
  local role="$1"
  local script="$ROOT/$role.sh"
  [[ -f "$script" ]] || { echo "no launcher for role '$role'"; return 1; }
  if is_running "$(role_pidfile "$role")"; then
    echo "  $role already running (pid $(cat "$(role_pidfile "$role")"))"
    return
  fi
  echo "  starting $role -> /tmp/dejaview-$role.log"
  nohup "$script" > "/tmp/dejaview-$role.log" 2>&1 &
  echo $! > "$(role_pidfile "$role")"
}

wait_for_port() {  # <port> <timeout_s>
  local port="$1" timeout="${2:-60}" i
  for ((i=0; i<timeout; i++)); do
    if curl -s -o /dev/null "http://127.0.0.1:$port/health"; then
      return 0
    fi
    # llama-server has no /health on all builds; /completion or /models works too
    if curl -s -o /dev/null "http://127.0.0.1:$port/models" || \
       curl -s -o /dev/null "http://127.0.0.1:$port/v1/models"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

cmd_up() {
  [[ $# -eq 0 ]] && { echo "usage: $0 up <role...> (e.g. up embed fast sentinel)"; exit 1; }
  echo "starting roles: $*"
  for role in "$@"; do start_role "$role"; done
  echo "waiting for model servers to be ready..."
  for role in "$@"; do
    case "$role" in
      sentinel) port=8003 ;; fast) port=8005 ;;
      embed) port=8004 ;; perceive) port=8002 ;;
      *) port="" ;;
    esac
    [[ -z "$port" ]] && continue
    if wait_for_port "$port" 90; then
      echo "  $role ready on :$port"
    else
      echo "  $role NOT ready on :$port (check /tmp/dejaview-$role.log)"
    fi
  done
  echo "starting gateway -> /tmp/dejaview-gateway.log"
  if ! is_running "$(gw_pidfile)"; then
    nohup "$ROOT/gateway.sh" > /tmp/dejaview-gateway.log 2>&1 &
    echo $! > "$(gw_pidfile)"
  fi
  if wait_for_port 4000 60; then
    echo "  gateway ready on :4000"
  else
    echo "  gateway NOT ready (check /tmp/dejaview-gateway.log)"
  fi
}

cmd_down() {
  echo "stopping dev stack..."
  for pf in /tmp/dejaview-*.pid; do
    [[ -f "$pf" ]] || continue
    local pid; pid="$(cat "$pf")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      echo "  killed $(basename "$pf" .pid) (pid $pid)"
    fi
    rm -f "$pf"
  done
  # also pkill any strays by alias (covers scripts started without this ctl)
  pkill -f "alias (sentinel|fast|embed|perceive)" 2>/dev/null || true
  pkill -f "litellm --config" 2>/dev/null || true
}

cmd_status() {
  echo "gateway:    $(is_running "$(gw_pidfile)" && echo "up (pid $(cat "$(gw_pidfile)"))" || echo down)"
  for role in sentinel fast embed perceive; do
    printf "%-10s  %s\n" "$role" "$(is_running "$(role_pidfile "$role")" && echo "up (pid $(cat "$(role_pidfile "$role")"))" || echo down)"
  done
}

cmd_smoke() {
  local key="sk-dejaview-local"
  echo "smoke test via gateway :4000 (master_key $key)"
  # text models
  for name in fast perceive brain; do
    echo "--- $name (chat) ---"
    curl -s -X POST http://127.0.0.1:4000/v1/chat/completions \
      -H "Authorization: Bearer $key" -H "Content-Type: application/json" \
      -d "{\"model\":\"$name\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word: hello\"}],\"max_tokens\":10}" \
      | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("choices",[{}])[0].get("message",{}).get("content",d))' 2>/dev/null || echo "  (failed)"
  done
  # embed
  echo "--- embed (embedding) ---"
  curl -s -X POST http://127.0.0.1:4000/v1/embeddings \
    -H "Authorization: Bearer $key" -H "Content-Type: application/json" \
    -d '{"model":"embed","input":"hello dejaview"}' \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);e=d.get("data",[{}])[0].get("embedding",[]);print(f"dim={len(e)} first3={e[:3]}")' 2>/dev/null || echo "  (failed)"
  # sentinel (vision) — needs an image; skip if not running
  if curl -s -o /dev/null http://127.0.0.1:8003/models; then
    echo "--- sentinel (vision, on :8003) ---"
    echo "  sentinel up; vision smoke left to T2.1 (needs image payload)"
  else
    echo "--- sentinel: not running, skipped ---"
  fi
}

case "${1:-}" in
  up)     shift; cmd_up "$@" ;;
  down)   cmd_down ;;
  status) cmd_status ;;
  smoke)  cmd_smoke ;;
  *) echo "usage: $0 {up <role...>|down|status|smoke}"; exit 1 ;;
esac
