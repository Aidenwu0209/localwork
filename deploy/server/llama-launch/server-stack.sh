#!/bin/bash
# DejaView AMD-server inference stack controller. Handbook §6.1 / S1 deployment.
#
# Usage:
#   ./server-stack.sh up <role...>   # start llama-servers + gateway
#   ./server-stack.sh down [role...] # stop roles (or everything if none given)
#   ./server-stack.sh status
#
# Roles: sentinel fast embed perceive brain. brain defaults to Q6_K on a shared
# GPU (BRAIN_QUANT env overrides). The gateway always starts on `up`.
# Runtime files default to /tmp/dejaview and can be isolated with
# DEJAVIEW_RUNTIME_DIR. PID files include a process-start fingerprint so stale
# or forged PID-only files never authorize a signal.
#
# CAUTION: this server is shared with another job (Dolphin, ~10.6 GB VRAM).
# Default常驻 four (sentinel+fast+embed+perceive) = ~12 GB, leaving ~25 GB free.
# brain Q6_K adds ~21 GB → ~43 GB total with Dolphin. Do NOT start brain Q8_0
# (28 GB) alongside Dolphin; that OOMs the GPU.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${DEJAVIEW_RUNTIME_DIR:-/tmp/dejaview}"
ROLE_READY_TIMEOUT="${DEJAVIEW_ROLE_READY_TIMEOUT:-240}"
GATEWAY_READY_TIMEOUT="${DEJAVIEW_GATEWAY_READY_TIMEOUT:-120}"
POLL_SECONDS="${DEJAVIEW_POLL_SECONDS:-2}"
STOP_TIMEOUT="${DEJAVIEW_STOP_TIMEOUT:-10}"
CURL_TIMEOUT="${DEJAVIEW_CURL_TIMEOUT:-5}"

mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"

role_pidfile() { printf '%s/dejaview-%s.pid\n' "$RUNTIME_DIR" "$1"; }
role_logfile() { printf '%s/dejaview-%s.log\n' "$RUNTIME_DIR" "$1"; }
gw_pidfile() { printf '%s/dejaview-gateway.pid\n' "$RUNTIME_DIR"; }
gw_logfile() { printf '%s/dejaview-gateway.log\n' "$RUNTIME_DIR"; }

role_port() {
  case "$1" in
    sentinel) printf '8003\n' ;;
    fast) printf '8005\n' ;;
    embed) printf '8004\n' ;;
    perceive) printf '8002\n' ;;
    brain) printf '8001\n' ;;
    *) return 1 ;;
  esac
}

validate_role() {
  role_port "$1" >/dev/null || {
    echo "unknown role '$1' (expected sentinel|fast|embed|perceive|brain)" >&2
    return 1
  }
}

process_fingerprint() {
  local pid="$1" start
  if [[ -r "/proc/$pid/stat" ]]; then
    start="$(sed 's/^[^)]*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}')"
    [[ -n "$start" ]] && { printf 'proc:%s\n' "$start"; return 0; }
  fi
  start="$(ps -p "$pid" -o lstart= 2>/dev/null | awk '{$1=$1; print}')"
  [[ -n "$start" ]] && printf 'ps:%s\n' "$start"
}

process_is_zombie() {
  local pid="$1" state
  state="$(ps -p "$pid" -o stat= 2>/dev/null | awk '{$1=$1; print}')"
  case "$state" in Z*) return 0 ;; *) return 1 ;; esac
}

read_owned_pidfile() {
  local pidfile="$1" expected="$2" pid fingerprint kind current
  [[ -f "$pidfile" ]] || return 1
  IFS='|' read -r pid fingerprint kind < "$pidfile" || return 1
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  [[ "$kind" == "$expected" && -n "$fingerprint" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  process_is_zombie "$pid" && return 1
  current="$(process_fingerprint "$pid")"
  [[ -n "$current" && "$current" == "$fingerprint" ]] || return 1
  OWNED_PID="$pid"
  return 0
}

write_pidfile() {
  local pidfile="$1" pid="$2" kind="$3" fingerprint i=0
  fingerprint="$(process_fingerprint "$pid")"
  while [[ -z "$fingerprint" && "$i" -lt 20 ]]; do
    sleep 0.01
    fingerprint="$(process_fingerprint "$pid")"
    i=$((i + 1))
  done
  [[ -n "$fingerprint" ]] || {
    echo "could not fingerprint $kind process $pid" >&2
    return 1
  }
  printf '%s|%s|%s\n' "$pid" "$fingerprint" "$kind" > "$pidfile"
  chmod 600 "$pidfile"
}

attempt_count() {
  local timeout="$1" poll="$2"
  awk -v timeout="$timeout" -v poll="$poll" 'BEGIN {
    if (poll <= 0) poll = 0.1
    attempts = int(timeout / poll)
    if (attempts < 1) attempts = 1
    print attempts
  }'
}

wait_for_port() {
  local port="$1" timeout="$2" attempts i=0
  attempts="$(attempt_count "$timeout" "$POLL_SECONDS")"
  while [[ "$i" -lt "$attempts" ]]; do
    curl -fsS --connect-timeout "$CURL_TIMEOUT" --max-time "$CURL_TIMEOUT" \
      -o /dev/null "http://127.0.0.1:$port/models" 2>/dev/null && return 0
    curl -fsS --connect-timeout "$CURL_TIMEOUT" --max-time "$CURL_TIMEOUT" \
      -o /dev/null "http://127.0.0.1:$port/v1/models" 2>/dev/null && return 0
    i=$((i + 1))
    [[ "$i" -lt "$attempts" ]] && sleep "$POLL_SECONDS"
  done
  return 1
}

STARTED_LAST=0
start_managed() {
  local kind="$1" command="$2" pidfile="$3" logfile="$4" pid
  STARTED_LAST=0
  if read_owned_pidfile "$pidfile" "$kind"; then
    echo "  $kind already running (pid $OWNED_PID)"
    return 0
  fi
  [[ ! -f "$pidfile" ]] || {
    echo "  removing stale or untrusted $kind pidfile" >&2
    rm -f "$pidfile"
  }
  echo "  starting $kind -> $logfile"
  DEJAVIEW_RUNTIME_DIR="$RUNTIME_DIR" nohup "$command" > "$logfile" 2>&1 &
  pid=$!
  if ! write_pidfile "$pidfile" "$pid" "$kind"; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    return 1
  fi
  STARTED_LAST=1
}

start_role() {
  local role="$1" script="$ROOT/$role.sh"
  [[ -x "$script" ]] || {
    echo "no executable launcher for role '$role': $script" >&2
    return 1
  }
  start_managed "$role" "$script" "$(role_pidfile "$role")" "$(role_logfile "$role")"
}

stop_managed() {
  local kind="$1" pidfile="$2" attempts i=0 pid
  if ! read_owned_pidfile "$pidfile" "$kind"; then
    if [[ -f "$pidfile" ]]; then
      echo "  ignored stale or untrusted $kind pidfile"
      rm -f "$pidfile"
    fi
    return 0
  fi
  pid="$OWNED_PID"
  kill "$pid"
  attempts="$(attempt_count "$STOP_TIMEOUT" "$POLL_SECONDS")"
  while read_owned_pidfile "$pidfile" "$kind"; do
    i=$((i + 1))
    if [[ "$i" -ge "$attempts" ]]; then
      echo "  $kind did not stop within ${STOP_TIMEOUT}s" >&2
      return 1
    fi
    sleep "$POLL_SECONDS"
  done
  rm -f "$pidfile"
  echo "  stopped $kind"
}

cleanup_started() {
  local gateway_started="$1" started_roles="$2" role
  if [[ "$gateway_started" -eq 1 ]]; then
    stop_managed gateway "$(gw_pidfile)" || true
  fi
  for role in $started_roles; do
    stop_managed "$role" "$(role_pidfile "$role")" || true
  done
}

cmd_up() {
  local role port started_roles="" gateway_started=0
  [[ $# -gt 0 ]] || {
    echo "usage: $0 up <role...> (e.g. up embed fast sentinel perceive)" >&2
    return 1
  }
  for role in "$@"; do validate_role "$role"; done

  echo "starting roles: $*"
  for role in "$@"; do
    if start_role "$role"; then
      [[ "$STARTED_LAST" -eq 0 ]] || started_roles="$started_roles $role"
    else
      cleanup_started 0 "$started_roles"
      return 1
    fi
  done

  echo "waiting for model servers..."
  for role in "$@"; do
    port="$(role_port "$role")"
    if wait_for_port "$port" "$ROLE_READY_TIMEOUT" && \
      read_owned_pidfile "$(role_pidfile "$role")" "$role"; then
      echo "  $role ready on :$port"
    else
      echo "  $role NOT ready (check $(role_logfile "$role"))"
      cleanup_started 0 "$started_roles"
      return 1
    fi
  done

  if ! start_managed gateway "$ROOT/gateway.sh" "$(gw_pidfile)" "$(gw_logfile)"; then
    cleanup_started 0 "$started_roles"
    return 1
  fi
  gateway_started="$STARTED_LAST"
  if wait_for_port 4000 "$GATEWAY_READY_TIMEOUT" && \
    read_owned_pidfile "$(gw_pidfile)" gateway; then
    echo "  gateway ready on :4000"
  else
    echo "  gateway NOT ready (check $(gw_logfile))"
    cleanup_started "$gateway_started" "$started_roles"
    return 1
  fi
  echo "  VRAM now:"
  rocm-smi --showmeminfo vram 2>/dev/null | grep "VRAM Total Used" || true
}

cmd_down() {
  local role result=0
  if [[ $# -gt 0 ]]; then
    for role in "$@"; do validate_role "$role"; done
    for role in "$@"; do
      stop_managed "$role" "$(role_pidfile "$role")" || result=1
    done
  else
    echo "stopping everything..."
    stop_managed gateway "$(gw_pidfile)" || result=1
    for role in sentinel fast embed perceive brain; do
      stop_managed "$role" "$(role_pidfile "$role")" || result=1
    done
  fi
  return "$result"
}

cmd_status() {
  local role
  if read_owned_pidfile "$(gw_pidfile)" gateway; then
    echo "gateway: up $OWNED_PID"
  else
    echo "gateway: down"
  fi
  for role in sentinel fast embed perceive brain; do
    if read_owned_pidfile "$(role_pidfile "$role")" "$role"; then
      printf '%-10s up %s\n' "$role" "$OWNED_PID"
    else
      printf '%-10s down\n' "$role"
    fi
  done
  echo "  VRAM:"
  rocm-smi --showmeminfo vram 2>/dev/null | grep "VRAM Total Used" || true
}

case "${1:-}" in
  up) shift; cmd_up "$@" ;;
  down) shift; cmd_down "$@" ;;
  status) cmd_status ;;
  *) echo "usage: $0 {up <role...>|down [role...]|status}" >&2; exit 1 ;;
esac
