#!/bin/bash
# DejaView AMD-server inference stack controller. Handbook §6.1 / S1 deployment.
#
# Usage:
#   ./server-stack.sh up <role...>   # start llama-servers + gateway
#   ./server-stack.sh down [role...] # stop roles (or everything if none given)
#   ./server-stack.sh status
#
# Roles: sentinel fast embed perceive brain. brain defaults to Q6_K on a shared
# GPU (BRAIN_QUANT env overrides). The gateway always starts on `up`. Instances
# log to /tmp/dejaview-<role>.log.
#
# CAUTION: this server is shared with another job (Dolphin, ~10.6 GB VRAM).
# Default常驻 four (sentinel+fast+embed+perceive) = ~12 GB, leaving ~25 GB free.
# brain Q6_K adds ~21 GB → ~43 GB total with Dolphin. Do NOT start brain Q8_0
# (28 GB) alongside Dolphin; that OOMs the GPU.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDS=/tmp

role_pidfile() { echo "/tmp/dejaview-$1.pid"; }
gw_pidfile()   { echo "/tmp/dejaview-gateway.pid"; }
is_running() { [[ -f "$1" ]] && kill -0 "$(cat "$1")" 2>/dev/null; }

declare -A PORTS=(
  [sentinel]=8003 [fast]=8005 [embed]=8004 [perceive]=8002 [brain]=8001
)

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

wait_for_port() {
  local port="$1" timeout="${2:-120}" i
  for ((i=0; i<timeout; i++)); do
    curl -s -o /dev/null "http://127.0.0.1:$port/models" && return 0
    curl -s -o /dev/null "http://127.0.0.1:$port/v1/models" && return 0
    sleep 2
  done
  return 1
}

cmd_up() {
  [[ $# -eq 0 ]] && { echo "usage: $0 up <role...> (e.g. up embed fast sentinel perceive)"; exit 1; }
  echo "starting roles: $*"
  for role in "$@"; do start_role "$role"; done
  echo "waiting for model servers..."
  for role in "$@"; do
    local port="${PORTS[$role]:-}"
    [[ -z "$port" ]] && continue
    if wait_for_port "$port" 120; then
      echo "  $role ready on :$port"
    else
      echo "  $role NOT ready (check /tmp/dejaview-$role.log)"
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
  echo "  VRAM now:"; rocm-smi --showmeminfo vram 2>/dev/null | grep "VRAM Total Used" || true
}

cmd_down() {
  if [[ $# -gt 0 ]]; then
    for role in "$@"; do
      local pf; pf="$(role_pidfile "$role")"
      if is_running "$pf"; then kill "$(cat "$pf")"; echo "  stopped $role"; fi
      rm -f "$pf"
    done
  else
    echo "stopping everything..."
    for pf in /tmp/dejaview-*.pid; do
      [[ -f "$pf" ]] || continue
      local pid; pid="$(cat "$pf")"
      kill -0 "$pid" 2>/dev/null && kill "$pid" 2>/dev/null && echo "  killed $(basename "$pf" .pid)"
      rm -f "$pf"
    done
    pkill -f "alias (sentinel|fast|embed|perceive|brain)" 2>/dev/null || true
    pkill -f "litellm.server" 2>/dev/null || true
  fi
}

cmd_status() {
  echo "gateway: $(is_running "$(gw_pidfile)" && echo "up $(cat "$(gw_pidfile)")" || echo down)"
  for role in sentinel fast embed perceive brain; do
    printf "%-10s %s\n" "$role" "$(is_running "$(role_pidfile "$role")" && echo "up $(cat "$(role_pidfile "$role")")" || echo down)"
  done
  echo "  VRAM:"; rocm-smi --showmeminfo vram 2>/dev/null | grep "VRAM Total Used" || true
}

case "${1:-}" in
  up)     shift; cmd_up "$@" ;;
  down)   shift; cmd_down "$@" ;;
  status) cmd_status ;;
  *) echo "usage: $0 {up <role...>|down [role...]|status}"; exit 1 ;;
esac
