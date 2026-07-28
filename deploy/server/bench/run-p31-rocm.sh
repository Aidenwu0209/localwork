#!/bin/bash
# P3.1 ROCm ablation runner for the W7900D compute host.
#
# Safety properties:
# - captures rocm-smi before any mutation;
# - refuses GPU mutation while any unrecognized KFD process touches the GPU
#   exposed in this container (host-wide KFD entries for other GPUs are ignored);
# - requires exact quant files and never falls back to another quant;
# - stops perceive before brain and leaves benchmarked GPU roles stopped on exit
#   so an unmonitored restore load cannot risk OOM;
# - uses only synthetic prompts and writes raw evidence for every matrix cell.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/../.." && pwd)"
BENCH_ROOT="$ROOT/bench"
STACK_ROOT="$ROOT/llama-launch"
BIN="${LLAMA_BIN:-/root/llama.cpp/build/bin/llama-server}"
MODELS_DIR="${DEV_MODELS_DIR:-/root/dejaview-models}"
RUN_ID="${P31_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
RESULTS_DIR="${P31_RESULTS_DIR:-/tmp/dejaview-p31/$RUN_ID}"
EXPECTED_LLAMA_COMMIT="76f46ad29d61fd8c1401e8221842934bf62a6064"
PYTHON="${PYTHON_BIN:-python3}"
RUNS="${P31_RUNS:-3}"
WARMUP="${P31_WARMUP:-1}"
BRAIN_MAX_TOKENS="${P31_BRAIN_MAX_TOKENS:-256}"
PERCEIVE_MAX_TOKENS="${P31_PERCEIVE_MAX_TOKENS:-96}"
BRAIN_PORT="${P31_BRAIN_PORT:-18001}"
PERCEIVE_PORT="${P31_PERCEIVE_PORT:-18002}"
SERVER_LOG_VERBOSITY=4
PERCEIVE_IMAGE="${P31_PERCEIVE_IMAGE:-$REPO_ROOT/tests/assets/screenshots/code_01_p31_focus.png}"
BRAIN_PROMPT="Synthetic throughput benchmark. Output only the ascending integers from 1 through 80, separated by one space. Do not explain."
PERCEIVE_PROMPT="${P31_PERCEIVE_PROMPT:-Read the active editor tab label at the top-left. Start your answer with that exact filename, preserving every character. Then describe the visible coding activity in one concise sentence. Do not infer or rename the filename.}"
BRAIN_PID=""
BRAIN_STARTTIME=""
BRAIN_KFD_PID=""
PERCEIVE_PID=""
PERCEIVE_STARTTIME=""
PERCEIVE_KFD_PID=""
VRAM_SAMPLER_PID=""
WATCHDOG_PID=""
WATCHDOG_FLAG=""
ENTRY_PERCEIVE_WAS_UP=0
FAILURES=0
LLAMA_COMMIT=""
LLAMA_BIN_SHA256=""
RUN_MANIFEST_SHA256=""

if [[ ! "$RUN_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "P31_RUN_ID may contain only letters, digits, dot, underscore, and dash" >&2
  exit 2
fi
if [[ -d "$RESULTS_DIR" ]] &&
  [[ -n "$(find "$RESULTS_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "results directory is not empty; use a new P31_RUN_ID to prevent mixed evidence: $RESULTS_DIR" >&2
  exit 2
fi
mkdir -p "$RESULTS_DIR"

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "required file missing: $path" >&2
    exit 2
  fi
}

verify_exact_file() {
  local path="$1" expected_size="$2" expected_sha="$3" actual_size actual_sha
  require_file "$path"
  actual_size="$(stat -c %s "$path")"
  if [[ "$actual_size" != "$expected_size" ]]; then
    echo "weight size mismatch: $path ($actual_size != $expected_size)" >&2
    exit 2
  fi
  actual_sha="$(sha256sum "$path" | awk '{print $1}')"
  if [[ "$actual_sha" != "$expected_sha" ]]; then
    echo "weight sha256 mismatch: $path" >&2
    exit 2
  fi
  printf '%s  %s\n' "$actual_sha" "$path" >>"$RESULTS_DIR/weights-verified.txt"
}

port_ready() {
  local port="$1"
  curl --fail --silent --max-time 2 \
    "http://127.0.0.1:$port/v1/models" >/dev/null 2>&1
}

port_listener() {
  local port="$1"
  ss -H -ltnp "sport = :$port" 2>/dev/null
}

assert_port_free() {
  local port="$1" listener
  listener="$(port_listener "$port")"
  if [[ -n "$listener" ]]; then
    echo "benchmark port $port is already occupied; refusing stale-server evidence:" >&2
    printf '%s\n' "$listener" >&2
    return 1
  fi
}

pid_owns_port() {
  local port="$1" pid="$2"
  port_listener "$port" | grep -Fq "pid=$pid,"
}

wait_for_port() {
  local port="$1" pid="$2" attempt
  for ((attempt = 0; attempt < 180; attempt++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 1
    fi
    if port_ready "$port" && pid_owns_port "$port" "$pid"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

wait_for_port_free() {
  local port="$1" attempt
  for ((attempt = 0; attempt < 60; attempt++)); do
    [[ -z "$(port_listener "$port")" ]] && return 0
    sleep 1
  done
  echo "benchmark port $port did not close after server stop" >&2
  return 1
}

pid_starttime() {
  local pid="$1" stat_line rest
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  [[ -r "/proc/$pid/stat" ]] || return 1
  stat_line="$(<"/proc/$pid/stat")"
  rest="${stat_line##*) }"
  set -- $rest
  [[ "${20:-}" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "${20}"
}

pid_state() {
  local pid="$1" stat_line rest
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  [[ -r "/proc/$pid/stat" ]] || return 1
  stat_line="$(<"/proc/$pid/stat")"
  rest="${stat_line##*) }"
  set -- $rest
  [[ "${1:-}" =~ ^[A-Z]$ ]] || return 1
  printf '%s\n' "$1"
}

cmdline_option_equals() {
  local pid="$1" option="$2" expected="$3" token expect_value=0
  [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/cmdline" ]] || return 1
  while IFS= read -r -d '' token; do
    if (( expect_value == 1 )); then
      [[ "$token" == "$expected" ]] && return 0
      expect_value=0
    fi
    [[ "$token" == "$option" ]] && expect_value=1
  done <"/proc/$pid/cmdline"
  return 1
}

cmdline_has_exact_token() {
  local pid="$1" expected="$2" token
  [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/cmdline" ]] || return 1
  while IFS= read -r -d '' token; do
    [[ "$token" == "$expected" ]] && return 0
  done <"/proc/$pid/cmdline"
  return 1
}

cmdline_has_option() {
  local pid="$1" option="$2"
  cmdline_has_exact_token "$pid" "$option"
}

managed_pid_identity_matches() {
  local pid="$1" expected_starttime="$2" alias="$3"
  local actual_starttime executable expected_executable
  [[ -n "$pid" && -n "$expected_starttime" && -n "$alias" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  actual_starttime="$(pid_starttime "$pid")" || return 1
  [[ "$actual_starttime" == "$expected_starttime" ]] || return 1
  executable="$(readlink -f "/proc/$pid/exe" 2>/dev/null)" || return 1
  expected_executable="$(readlink -f "$BIN" 2>/dev/null)" || return 1
  [[ "$executable" == "$expected_executable" ]] || return 1
  cmdline_option_equals "$pid" --alias "$alias"
}

capture_new_child_starttime() {
  local pid="$1" attempt value
  for ((attempt = 0; attempt < 100; attempt++)); do
    if value="$(pid_starttime "$pid" 2>/dev/null)"; then
      printf '%s\n' "$value"
      return 0
    fi
    kill -0 "$pid" 2>/dev/null || return 1
    sleep 0.01
  done
  return 1
}

abort_unbound_new_child() {
  local pid="$1" port="$2" attempt
  if jobs -pr | grep -Fxq "$pid"; then
    kill "$pid" 2>/dev/null || true
    for ((attempt = 0; attempt < 60; attempt++)); do
      jobs -pr | grep -Fxq "$pid" || break
      sleep 0.5
    done
    if jobs -pr | grep -Fxq "$pid"; then
      echo "new benchmark child $pid ignored SIGTERM; sending SIGKILL" >&2
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi
  wait "$pid" 2>/dev/null || true
  wait_for_port_free "$port"
}

wait_for_managed_identity() {
  local pid="$1" starttime="$2" alias="$3" attempt
  for ((attempt = 0; attempt < 100; attempt++)); do
    managed_pid_identity_matches "$pid" "$starttime" "$alias" && return 0
    kill -0 "$pid" 2>/dev/null || return 1
    sleep 0.05
  done
  return 1
}

process_holds_dev_kfd() {
  local pid="$1" fd expected_device actual_device
  [[ "$pid" =~ ^[0-9]+$ && -d "/proc/$pid/fd" ]] || return 1
  expected_device="$(stat -Lc '%F:%t:%T' /dev/kfd 2>/dev/null)" || return 1
  for fd in "/proc/$pid"/fd/*; do
    actual_device="$(stat -Lc '%F:%t:%T' "$fd" 2>/dev/null || true)"
    [[ "$actual_device" == "$expected_device" ]] && return 0
  done
  return 1
}

bind_managed_kfd_pid() {
  local pid="$1" starttime="$2" alias="$3"
  local attempt inventory count candidate free stable_candidate="" stable_count=0
  for ((attempt = 0; attempt < 200; attempt++)); do
    if ! managed_pid_identity_matches "$pid" "$starttime" "$alias"; then
      echo "managed model exited or changed identity before KFD binding" >&2
      return 1
    fi
    if ! free="$(vram_free_bytes)"; then
      echo "assigned-GPU VRAM telemetry failed during KFD binding" >&2
      return 1
    fi
    if (( free < 6000000000 )); then
      echo "free VRAM fell below 6 GB before KFD binding: $free" >&2
      return 1
    fi
    if ! inventory="$(gpu_processes)"; then
      echo "KFD inventory failed during managed-process binding" >&2
      return 1
    fi
    if [[ -z "$inventory" ]]; then
      stable_candidate=""
      stable_count=0
      sleep 0.05
      continue
    fi
    count="$(awk 'NF { count++ } END { print count + 0 }' <<<"$inventory")"
    if [[ "$count" != "1" ]]; then
      echo "expected exactly one new assigned-GPU KFD process; found $count:" >&2
      printf '%s\n' "$inventory" >&2
      return 1
    fi
    if ! process_holds_dev_kfd "$pid"; then
      echo "an assigned-GPU KFD process appeared before the managed model held /dev/kfd; refusing ambiguous binding" >&2
      printf '%s\n' "$inventory" >&2
      return 1
    fi
    candidate="$(awk -F '\t' 'NF { print $1; exit }' <<<"$inventory")"
    [[ "$candidate" =~ ^[0-9]+$ ]] || {
      echo "managed KFD candidate has an invalid PID: $candidate" >&2
      return 1
    }
    if [[ "$candidate" == "$stable_candidate" ]]; then
      stable_count=$((stable_count + 1))
    else
      stable_candidate="$candidate"
      stable_count=1
    fi
    if (( stable_count >= 3 )); then
      printf '%s\n' "$candidate"
      return 0
    fi
    sleep 0.05
  done
  echo "managed model did not acquire a unique assigned-GPU KFD process" >&2
  return 1
}

wait_for_kfd_pid_release() {
  local kfd_pid="$1" attempt
  [[ -n "$kfd_pid" ]] || return 0
  for ((attempt = 0; attempt < 480; attempt++)); do
    if [[ ! -e "/sys/class/kfd/kfd/proc/$kfd_pid" ]]; then
      wait_for_assigned_gpu_idle
      return
    fi
    sleep 0.25
  done
  echo "managed KFD PID $kfd_pid did not release the assigned GPU" >&2
  return 1
}

stop_pid() {
  local pid="${1:-}" port="${2:-}" starttime="${3:-}" alias="${4:-}"
  local actual_starttime state attempt
  [[ -n "$pid" ]] || return 0
  [[ "$pid" =~ ^[0-9]+$ && -n "$starttime" && -n "$alias" ]] || {
    echo "invalid managed PID identity; refusing process mutation" >&2
    return 1
  }

  if kill -0 "$pid" 2>/dev/null; then
    actual_starttime="$(pid_starttime "$pid")" || {
      echo "cannot verify managed PID $pid starttime; refusing signal" >&2
      return 1
    }
    if [[ "$actual_starttime" != "$starttime" ]]; then
      echo "managed PID $pid was reused; refusing signal" >&2
      return 1
    fi
    state="$(pid_state "$pid")" || {
      echo "cannot verify managed PID $pid state; refusing signal" >&2
      return 1
    }
    if [[ "$state" != "Z" ]]; then
      if ! managed_pid_identity_matches "$pid" "$starttime" "$alias"; then
        echo "managed PID identity changed; refusing to signal PID $pid" >&2
        return 1
      fi
      kill "$pid" 2>/dev/null || true
      for ((attempt = 0; attempt < 60; attempt++)); do
        kill -0 "$pid" 2>/dev/null || break
        actual_starttime="$(pid_starttime "$pid")" || break
        if [[ "$actual_starttime" != "$starttime" ]]; then
          echo "PID $pid was reused after SIGTERM; refusing further signals" >&2
          return 1
        fi
        state="$(pid_state "$pid")" || break
        [[ "$state" == "Z" ]] && break
        sleep 0.5
      done
      if kill -0 "$pid" 2>/dev/null; then
        actual_starttime="$(pid_starttime "$pid")" || {
          echo "cannot re-verify PID $pid after SIGTERM; refusing SIGKILL" >&2
          return 1
        }
        if [[ "$actual_starttime" != "$starttime" ]]; then
          echo "PID $pid was reused after SIGTERM; refusing SIGKILL" >&2
          return 1
        fi
        state="$(pid_state "$pid")" || {
          echo "cannot re-verify PID $pid state; refusing SIGKILL" >&2
          return 1
        }
        if [[ "$state" != "Z" ]]; then
          if ! managed_pid_identity_matches "$pid" "$starttime" "$alias"; then
            echo "PID $pid identity changed after SIGTERM; refusing SIGKILL" >&2
            return 1
          fi
          echo "managed benchmark PID $pid ignored SIGTERM; sending SIGKILL" >&2
          kill -KILL "$pid" 2>/dev/null || true
        fi
      fi
    fi
    wait "$pid" 2>/dev/null || true
  fi
  [[ -z "$port" ]] || wait_for_port_free "$port"
}

stop_brain_managed() {
  local had_pid=0
  if [[ -n "$BRAIN_PID" ]]; then
    had_pid=1
    if ! stop_pid \
      "$BRAIN_PID" "$BRAIN_PORT" "$BRAIN_STARTTIME" brain-bench; then
      echo "failed to stop managed brain process; retaining its identity for EXIT cleanup" >&2
      return 1
    fi
  fi
  if [[ -n "$BRAIN_KFD_PID" ]] &&
    ! wait_for_kfd_pid_release "$BRAIN_KFD_PID"; then
    echo "brain process stopped but its KFD registration did not release; refusing the next model" >&2
    return 1
  fi
  if (( had_pid == 1 )) && [[ -z "$BRAIN_KFD_PID" ]] &&
    ! wait_for_assigned_gpu_idle; then
    echo "unbound brain child stopped but the assigned GPU did not return idle" >&2
    return 1
  fi
  BRAIN_PID=""
  BRAIN_STARTTIME=""
  BRAIN_KFD_PID=""
}

stop_perceive_managed() {
  local had_pid=0
  if [[ -n "$PERCEIVE_PID" ]]; then
    had_pid=1
    if ! stop_pid \
      "$PERCEIVE_PID" "$PERCEIVE_PORT" "$PERCEIVE_STARTTIME" perceive-bench; then
      echo "failed to stop managed perceive process; retaining its identity for EXIT cleanup" >&2
      return 1
    fi
  fi
  if [[ -n "$PERCEIVE_KFD_PID" ]] &&
    ! wait_for_kfd_pid_release "$PERCEIVE_KFD_PID"; then
    echo "perceive process stopped but its KFD registration did not release; refusing the next model" >&2
    return 1
  fi
  if (( had_pid == 1 )) && [[ -z "$PERCEIVE_KFD_PID" ]] &&
    ! wait_for_assigned_gpu_idle; then
    echo "unbound perceive child stopped but the assigned GPU did not return idle" >&2
    return 1
  fi
  PERCEIVE_PID=""
  PERCEIVE_STARTTIME=""
  PERCEIVE_KFD_PID=""
}

cleanup() {
  local original_status=$? cleanup_status=0
  trap - EXIT
  if [[ -n "$VRAM_SAMPLER_PID" ]]; then
    kill "$VRAM_SAMPLER_PID" 2>/dev/null || true
    wait "$VRAM_SAMPLER_PID" 2>/dev/null || true
  fi
  if ! stop_brain_managed; then
    cleanup_status=1
  fi
  if ! stop_perceive_managed; then
    cleanup_status=1
  fi
  if [[ -n "$WATCHDOG_PID" ]]; then
    kill "$WATCHDOG_PID" 2>/dev/null || true
    wait "$WATCHDOG_PID" 2>/dev/null || true
  fi
  if ! "$STACK_ROOT/server-stack.sh" status \
    >"$RESULTS_DIR/stack-after-benchmark.txt" 2>&1; then
    cleanup_status=1
  fi
  if (( ENTRY_PERCEIVE_WAS_UP == 1 )); then
    echo "perceive was up at entry and was intentionally left stopped; restore it only after reviewing benchmark cleanup evidence" >&2
  fi
  if (( original_status == 0 && cleanup_status != 0 )); then
    exit 1
  fi
  exit "$original_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

gpu_processes() {
  if [[ ! -d /sys/class/kfd/kfd/proc ]]; then
    echo "KFD process inventory is unavailable" >&2
    return 1
  fi
  "$PYTHON" "$BENCH_ROOT/kfd_scope.py"
}

allowed_gpu_pid() {
  local candidate="$1"
  if [[ -n "$BRAIN_PID" && -n "$BRAIN_KFD_PID" ]] &&
    [[ "$candidate" == "$BRAIN_KFD_PID" ]] &&
    managed_pid_identity_matches \
      "$BRAIN_PID" "$BRAIN_STARTTIME" brain-bench; then
    return 0
  fi
  if [[ -n "$PERCEIVE_PID" && -n "$PERCEIVE_KFD_PID" ]] &&
    [[ "$candidate" == "$PERCEIVE_KFD_PID" ]] &&
    managed_pid_identity_matches \
      "$PERCEIVE_PID" "$PERCEIVE_STARTTIME" perceive-bench; then
    return 0
  fi
  return 1
}

foreign_gpu_processes_from_inventory() {
  local inventory="$1" pid comm gpu_ids
  while IFS=$'\t' read -r pid comm gpu_ids; do
    [[ -n "$pid" ]] || continue
    allowed_gpu_pid "$pid" ||
      printf '%s\t%s\tgpu_ids=%s\n' "$pid" "$comm" "$gpu_ids"
  done <<<"$inventory"
}

foreign_gpu_processes() {
  local inventory
  [[ -d /sys/class/kfd/kfd/proc ]] || return 1
  if ! inventory="$(gpu_processes)"; then
    return 1
  fi
  foreign_gpu_processes_from_inventory "$inventory"
}

assert_no_foreign_gpu_processes() {
  local foreign
  if ! foreign="$(foreign_gpu_processes)"; then
    echo "cannot audit KFD processes; refusing GPU mutation" >&2
    return 1
  fi
  if [[ -n "$foreign" ]]; then
    echo "foreign ROCm process detected; refusing to interfere or risk OOM:" >&2
    printf '%s\n' "$foreign" >&2
    return 1
  fi
}

assert_assigned_gpu_idle() {
  local sample inventory
  for sample in 1 2 3; do
    if ! inventory="$(gpu_processes)"; then
      echo "cannot audit assigned-GPU KFD processes" >&2
      return 1
    fi
    if [[ -n "$inventory" ]]; then
      echo "assigned GPU is not idle; refusing benchmark model start:" >&2
      printf '%s\n' "$inventory" >&2
      return 1
    fi
    (( sample == 3 )) || sleep 0.15
  done
}

wait_for_assigned_gpu_idle() {
  local attempt inventory stable_count=0
  for ((attempt = 0; attempt < 240; attempt++)); do
    if ! inventory="$(gpu_processes)"; then
      echo "cannot audit assigned GPU while waiting for idle" >&2
      return 1
    fi
    if [[ -z "$inventory" ]]; then
      stable_count=$((stable_count + 1))
      (( stable_count >= 3 )) && return 0
    else
      stable_count=0
    fi
    sleep 0.25
  done
  echo "assigned GPU did not become idle; refusing benchmark:" >&2
  printf '%s\n' "$inventory" >&2
  return 1
}

capture_gpu_state() {
  local label="$1"
  local text_path="$RESULTS_DIR/$label-rocm-smi.txt"
  local json_path="$RESULTS_DIR/$label-rocm-smi.json"
  local json_tmp="$json_path.tmp"
  if ! {
    date -Is
    if ! rocm-smi \
      --showproductname --showdriverversion --showmeminfo vram --showuse; then
      echo "required rocm-smi text capture failed" >&2
      return 1
    fi
    printf 'container_assigned_kfd_gpu_ids='
    if ! "$PYTHON" "$BENCH_ROOT/kfd_scope.py" --assigned-ids; then
      echo "required assigned-GPU discovery failed" >&2
      return 1
    fi
    echo "KFD processes scoped to assigned GPU (PID, comm, GPU IDs):"
    if ! gpu_processes; then
      echo "required KFD process capture failed" >&2
      return 1
    fi
    rocm-smi --showpids verbose 2>/dev/null || true
  } >"$text_path"; then
    rm -f "$json_tmp"
    return 1
  fi
  if ! rocm-smi --showproductname --showdriverversion --showmeminfo vram \
    --showuse --json >"$json_tmp" 2>/dev/null; then
    echo "required rocm-smi JSON capture failed" >&2
    rm -f "$json_tmp"
    return 1
  fi
  if [[ ! -s "$text_path" || ! -s "$json_tmp" ]] ||
    ! "$PYTHON" - "$json_tmp" <<'PY'
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(value, dict) or not value:
    raise SystemExit("rocm-smi JSON capture is empty")
PY
  then
    echo "required GPU state capture is empty or invalid" >&2
    rm -f "$json_tmp"
    return 1
  fi
  mv "$json_tmp" "$json_path"
}

verify_llama_rocm_build() {
  local rocminfo_log="$RESULTS_DIR/rocminfo-gfx1100.txt"
  local version_output binary_reported_commit source_status
  local cmake_cache="/root/llama.cpp/build/CMakeCache.txt"
  if [[ ! -d /root/llama.cpp/.git ]]; then
    echo "cannot prove pinned llama.cpp source revision: /root/llama.cpp/.git missing" >&2
    return 1
  fi
  LLAMA_COMMIT="$(git -C /root/llama.cpp rev-parse HEAD)"
  if [[ "$LLAMA_COMMIT" != "$EXPECTED_LLAMA_COMMIT" ]]; then
    echo "llama.cpp revision mismatch: $LLAMA_COMMIT (expected $EXPECTED_LLAMA_COMMIT)" >&2
    return 1
  fi
  source_status="$(
    git -C /root/llama.cpp status --porcelain=v1 --untracked-files=all
  )"
  printf '%s' "$source_status" >"$RESULTS_DIR/llama-source-status.txt"
  if [[ -n "$source_status" ]]; then
    echo "llama.cpp source/index is dirty; refusing unproven binary provenance" >&2
    return 1
  fi
  if [[ ! -r "$cmake_cache" ]] ||
    ! grep -Eq '^GGML_HIP:BOOL=ON$' "$cmake_cache" ||
    ! grep -Eq '^AMDGPU_TARGETS:[^=]+=gfx1100$' "$cmake_cache"; then
    echo "llama.cpp CMake cache does not prove HIP gfx1100 configuration" >&2
    return 1
  fi
  grep -E '^(GGML_HIP|AMDGPU_TARGETS|CMAKE_BUILD_TYPE|GGML_NATIVE):' \
    "$cmake_cache" >"$RESULTS_DIR/llama-cmake-config.txt"
  LLAMA_BIN_SHA256="$(sha256sum "$BIN" | awk '{print $1}')"
  if ! version_output="$("$BIN" --version 2>&1)"; then
    echo "llama-server --version failed" >&2
    return 1
  fi
  binary_reported_commit="$(
    grep -Eo '\([0-9a-f]{7,40}\)' <<<"$version_output" |
      head -1 |
      tr -d '()' || true
  )"
  if [[ ${#binary_reported_commit} -lt 7 ]] ||
    [[ "$LLAMA_COMMIT" != "$binary_reported_commit"* ]]; then
    echo "llama-server binary commit $binary_reported_commit does not match source $LLAMA_COMMIT" >&2
    return 1
  fi
  if ! rocminfo 2>/dev/null | grep -i -C 2 'gfx1100' >"$rocminfo_log"; then
    echo "rocminfo did not identify the required gfx1100 target" >&2
    return 1
  fi
  if ! ldd "$BIN" >"$RESULTS_DIR/llama-linked-libraries.txt" ||
    ! grep -q 'libggml-hip' "$RESULTS_DIR/llama-linked-libraries.txt"; then
    echo "llama-server linkage does not prove the HIP backend library" >&2
    return 1
  fi
  awk '$2 == "=>" && $3 ~ /^\// { print $3 }' \
    "$RESULTS_DIR/llama-linked-libraries.txt" |
    sort -u |
    while IFS= read -r library; do
      sha256sum "$library"
    done >"$RESULTS_DIR/llama-linked-library-sha256.txt"
  {
    echo "llama_cpp_commit=$LLAMA_COMMIT"
    echo "llama_binary_reported_commit=$binary_reported_commit"
    echo "llama_bin_sha256=$LLAMA_BIN_SHA256"
    echo "expected_gpu_arch=gfx1100"
    printf '%s\n' "$version_output"
  } >"$RESULTS_DIR/llama-build-verified.txt"
}

capture_environment() {
  {
    echo "run_id=$RUN_ID"
    echo "measured_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "runs=$RUNS"
    echo "warmup_batches=$WARMUP"
    echo "brain_max_tokens=$BRAIN_MAX_TOKENS"
    echo "perceive_max_tokens=$PERCEIVE_MAX_TOKENS"
    echo "server_log_verbosity=$SERVER_LOG_VERBOSITY"
    printf 'hostname='
    hostname
    printf 'logical_cpus='
    nproc
    grep -E '^MemTotal:' /proc/meminfo
    lscpu
    uname -a
    if [[ -r /opt/rocm/.info/version ]]; then
      printf 'rocm_version='
      cat /opt/rocm/.info/version
    fi
    echo "llama_cpp_commit=$LLAMA_COMMIT"
    echo "llama_bin_sha256=$LLAMA_BIN_SHA256"
    "$BIN" --version 2>&1
    [[ -f "$RESULTS_DIR/weights-verified.txt" ]] &&
      cat "$RESULTS_DIR/weights-verified.txt"
  } >"$RESULTS_DIR/environment.txt"
}

write_run_manifest() {
  local mode="$1" image_sha="none" manifest="$RESULTS_DIR/run-manifest.txt"
  if [[ "$mode" != "brain" ]]; then
    image_sha="$(sha256sum "$PERCEIVE_IMAGE" | awk '{print $1}')"
  fi
  {
    echo "manifest_schema=1"
    echo "run_id=$RUN_ID"
    echo "mode=$mode"
    echo "runs=$RUNS"
    echo "warmup_batches=$WARMUP"
    echo "brain_max_tokens=$BRAIN_MAX_TOKENS"
    echo "perceive_max_tokens=$PERCEIVE_MAX_TOKENS"
    echo "brain_port=$BRAIN_PORT"
    echo "perceive_port=$PERCEIVE_PORT"
    echo "server_log_verbosity=$SERVER_LOG_VERBOSITY"
    echo "llama_cpp_commit=$LLAMA_COMMIT"
    echo "llama_bin_sha256=$LLAMA_BIN_SHA256"
    echo "weights_manifest_sha256=$(sha256sum "$RESULTS_DIR/weights-verified.txt" | awk '{print $1}')"
    echo "perceive_image_sha256=$image_sha"
    echo "brain_prompt_sha256=$(printf '%s' "$BRAIN_PROMPT" | sha256sum | awk '{print $1}')"
    echo "perceive_prompt_sha256=$(printf '%s' "$PERCEIVE_PROMPT" | sha256sum | awk '{print $1}')"
    echo "openai_bench_sha256=$(sha256sum "$BENCH_ROOT/openai_bench.py" | awk '{print $1}')"
    echo "summarizer_sha256=$(sha256sum "$BENCH_ROOT/summarize_p31.py" | awk '{print $1}')"
    echo "runner_sha256=$(sha256sum "$BENCH_ROOT/run-p31-rocm.sh" | awk '{print $1}')"
  } >"$manifest.tmp"
  mv "$manifest.tmp" "$manifest"
  RUN_MANIFEST_SHA256="$(sha256sum "$manifest" | awk '{print $1}')"
  printf '%s  %s\n' "$RUN_MANIFEST_SHA256" "$(basename "$manifest")" \
    >"$RESULTS_DIR/run-manifest.sha256"
}

assert_role_stopped() {
  local role="$1" attempt
  for ((attempt = 0; attempt < 30; attempt++)); do
    if ! ps -eo args |
      grep -E -- "--alias[[:space:]]+${role}([[:space:]]|$)" |
      grep -v grep >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "role '$role' still has a live llama-server process; refusing to continue" >&2
  return 1
}

safe_stack_down() {
  local role="$1" pidfile pid starttime state port
  pidfile="/tmp/dejaview-$role.pid"
  case "$role" in
    brain) port=8001 ;;
    perceive) port=8002 ;;
    *) echo "unsupported safe stop role: $role" >&2; return 2 ;;
  esac
  [[ -r "$pidfile" ]] || return 0
  pid="$(cat "$pidfile")"
  [[ "$pid" =~ ^[0-9]+$ ]] || {
    echo "invalid $role pidfile; refusing mutation: $pidfile" >&2
    return 1
  }
  if ! kill -0 "$pid" 2>/dev/null; then
    [[ "$(cat "$pidfile" 2>/dev/null)" == "$pid" ]] && rm -f "$pidfile"
    return 0
  fi
  starttime="$(pid_starttime "$pid")" || {
    echo "cannot bind $role PID $pid to a starttime; refusing kill" >&2
    return 1
  }
  state="$(pid_state "$pid")" || {
    echo "cannot inspect $role PID $pid state; refusing kill" >&2
    return 1
  }
  if [[ "$state" == "Z" ]]; then
    wait_for_port_free "$port" || return 1
    [[ "$(cat "$pidfile" 2>/dev/null)" == "$pid" ]] && rm -f "$pidfile"
    return 0
  fi
  if ! managed_pid_identity_matches "$pid" "$starttime" "$role" ||
    ! pid_owns_port "$port" "$pid"; then
    echo "stale/reused $role pidfile failed executable/alias/port identity; refusing kill" >&2
    return 1
  fi
  stop_pid "$pid" "$port" "$starttime" "$role"
  [[ "$(cat "$pidfile" 2>/dev/null)" == "$pid" ]] && rm -f "$pidfile"
}

vram_free_bytes() {
  "$PYTHON" "$BENCH_ROOT/kfd_scope.py" --vram free
}

require_brain_headroom() {
  local quant="$1" minimum free
  case "$quant" in
    Q8_0) minimum=34000000000 ;;
    Q6_K) minimum=27000000000 ;;
    Q4_K_M) minimum=22000000000 ;;
    *) echo "unsupported quant: $quant" >&2; return 2 ;;
  esac
  free="$(vram_free_bytes)"
  if (( free < minimum )); then
    echo "insufficient free VRAM for $quant: $free bytes < $minimum" >&2
    return 1
  fi
}

require_post_load_reserve() {
  local free
  free="$(vram_free_bytes)"
  if (( free < 6000000000 )); then
    echo "less than 6 GB VRAM remains after model load; aborting safely" >&2
    return 1
  fi
}

start_gpu_watchdog() {
  local label="$1" server_pid="$2" starttime="$3" alias="$4" kfd_pid="$5"
  WATCHDOG_FLAG="$RESULTS_DIR/$label-watchdog-failure.txt"
  rm -f "$WATCHDOG_FLAG"
  (
    local inventory inventory_count inventory_pid free reason
    while kill -0 "$server_pid" 2>/dev/null; do
      reason=""
      if ! managed_pid_identity_matches \
        "$server_pid" "$starttime" "$alias"; then
        {
          date -Is
          echo "managed server PID identity changed; no signal sent"
        } >"$WATCHDOG_FLAG"
        exit 1
      elif ! inventory="$(gpu_processes)"; then
        reason="KFD process inventory became unavailable"
      else
        inventory_count="$(
          awk 'NF { count++ } END { print count + 0 }' <<<"$inventory"
        )"
        inventory_pid="$(
          awk -F '\t' 'NF { print $1; exit }' <<<"$inventory"
        )"
        if [[ "$inventory_count" != "1" || "$inventory_pid" != "$kfd_pid" ]]; then
          reason="assigned-GPU KFD set changed; expected only $kfd_pid, observed: ${inventory//$'\n'/; }"
        elif ! process_holds_dev_kfd "$server_pid"; then
          reason="managed process no longer holds the assigned /dev/kfd device"
        elif ! free="$(vram_free_bytes)"; then
          reason="W7900 VRAM telemetry became unavailable"
        elif (( free < 6000000000 )); then
          reason="free VRAM fell below 6000000000 bytes: $free"
        fi
      fi
      if [[ -n "$reason" ]]; then
        {
          date -Is
          echo "$reason"
        } >"$WATCHDOG_FLAG"
        if managed_pid_identity_matches \
          "$server_pid" "$starttime" "$alias"; then
          kill "$server_pid" 2>/dev/null || true
        else
          echo "managed PID identity changed before safety stop; no signal sent" \
            >>"$WATCHDOG_FLAG"
        fi
        exit 1
      fi
      sleep 0.2
    done
  ) &
  WATCHDOG_PID="$!"
}

finish_gpu_watchdog() {
  local watchdog_pid="$1" failure_file="$2" server_pid="$3"
  if ! kill -0 "$watchdog_pid" 2>/dev/null &&
    [[ ! -s "$failure_file" ]]; then
    {
      date -Is
      echo "GPU safety watchdog exited unexpectedly before controlled stop"
    } >"$failure_file"
  fi
  kill "$watchdog_pid" 2>/dev/null || true
  wait "$watchdog_pid" 2>/dev/null || true
  if [[ -s "$failure_file" ]]; then
    echo "GPU safety watchdog aborted the managed model load:" >&2
    cat "$failure_file" >&2
    return 1
  fi
}

check_gpu_watchdog() {
  local watchdog_pid="$1" failure_file="$2" server_pid="$3"
  local starttime="$4" alias="$5"
  if [[ -s "$failure_file" ]]; then
    echo "GPU safety watchdog aborted the active load:" >&2
    cat "$failure_file" >&2
    return 1
  fi
  if ! kill -0 "$watchdog_pid" 2>/dev/null ||
    ! managed_pid_identity_matches "$server_pid" "$starttime" "$alias"; then
    {
      date -Is
      echo "GPU safety watchdog or managed server exited unexpectedly"
    } >"$failure_file"
    echo "GPU safety watchdog lost continuous coverage:" >&2
    cat "$failure_file" >&2
    return 1
  fi
}

finish_active_gpu_watchdog() {
  local server_pid="$1" status=0
  if [[ -n "$WATCHDOG_PID" ]]; then
    if ! finish_gpu_watchdog \
      "$WATCHDOG_PID" "$WATCHDOG_FLAG" "$server_pid"; then
      status=1
    fi
    WATCHDOG_PID=""
    WATCHDOG_FLAG=""
  fi
  return "$status"
}

require_perceive_headroom() {
  local free
  free="$(vram_free_bytes)"
  if (( free < 12000000000 )); then
    echo "less than 12 GB VRAM is free before perceive load; refusing" >&2
    return 1
  fi
}

vram_used_bytes() {
  "$PYTHON" "$BENCH_ROOT/kfd_scope.py" --vram used
}

sample_vram() {
  local output="$1" used
  while true; do
    used="$(vram_used_bytes)" || exit 1
    printf '%s\t%s\n' "$(date +%s%3N)" "$used" >>"$output"
    sleep 0.2
  done
}

finish_vram_sample() {
  local sampler_pid="$1" raw="$2" output="$3" was_alive=0 wait_status=0
  if kill -0 "$sampler_pid" 2>/dev/null; then
    was_alive=1
    kill "$sampler_pid" 2>/dev/null || true
  fi
  if wait "$sampler_pid" 2>/dev/null; then
    wait_status=0
  else
    wait_status=$?
  fi
  if (( was_alive == 0 )) ||
    (( wait_status != 0 && wait_status != 143 )); then
    echo "VRAM sampler exited before a controlled stop: $raw" >&2
    rm -f "$output"
    return 1
  fi
  if [[ ! -s "$raw" ]] ||
    ! awk '
      NF != 2 || $1 !~ /^[0-9]+$/ || $2 !~ /^[0-9]+$/ { exit 1 }
      END { if (NR == 0) exit 1 }
    ' "$raw"; then
    echo "VRAM sampler produced missing or malformed data: $raw" >&2
    rm -f "$output"
    return 1
  fi
  awk 'BEGIN { max = 0 } $2 > max { max = $2 } END { print max }' \
    "$raw" >"$output"
}

assert_server_identity() {
  local pid="$1" port="$2" model="$3" mmproj="$4" parallel="$5" mtp="$6"
  kill -0 "$pid" 2>/dev/null || return 1
  pid_owns_port "$port" "$pid" || return 1
  cmdline_option_equals "$pid" -m "$model" || return 1
  cmdline_option_equals "$pid" --mmproj "$mmproj" || return 1
  cmdline_option_equals "$pid" -np "$parallel" || return 1
  cmdline_option_equals "$pid" --cache-ram 0 || return 1
  cmdline_has_exact_token "$pid" --no-cache-idle-slots || return 1
  cmdline_option_equals "$pid" -lv "$SERVER_LOG_VERBOSITY" || return 1
  if [[ "$mtp" == "on" ]]; then
    cmdline_option_equals "$pid" --spec-type draft-mtp || return 1
    cmdline_option_equals "$pid" --spec-draft-n-max 4 || return 1
  else
    ! cmdline_has_option "$pid" --spec-type || return 1
    ! cmdline_has_option "$pid" --spec-draft-n-max || return 1
  fi
}

assert_server_rocm_execution() {
  local pid="$1" starttime="$2" alias="$3" log="$4" proof="$5" kfd_pid="$6"
  local executable executable_sha scoped_inventory scoped_line
  local scoped_count scoped_pid
  if ! managed_pid_identity_matches "$pid" "$starttime" "$alias"; then
    echo "managed server identity changed before ROCm proof capture" >&2
    return 1
  fi
  if ! process_holds_dev_kfd "$pid"; then
    echo "managed server does not hold /dev/kfd during ROCm proof capture" >&2
    return 1
  fi
  if ! scoped_inventory="$(gpu_processes)"; then
    echo "cannot scope llama-server KFD registration to the assigned GPU" >&2
    return 1
  fi
  scoped_count="$(
    awk 'NF { count++ } END { print count + 0 }' <<<"$scoped_inventory"
  )"
  scoped_pid="$(
    awk -F '\t' 'NF { print $1; exit }' <<<"$scoped_inventory"
  )"
  if [[ "$scoped_count" != "1" || "$scoped_pid" != "$kfd_pid" ]]; then
    echo "assigned-GPU KFD set is not exactly the bound PID $kfd_pid; refusing CPU-only, wrong-GPU, or co-tenant evidence" >&2
    printf '%s\n' "$scoped_inventory" >&2
    return 1
  fi
  scoped_line="$(awk 'NF { print; exit }' <<<"$scoped_inventory")"
  executable="$(readlink -f "/proc/$pid/exe")"
  executable_sha="$(sha256sum "$executable" | awk '{print $1}')"
  if [[ "$executable_sha" != "$LLAMA_BIN_SHA256" ]]; then
    echo "running llama-server binary hash changed: $executable_sha" >&2
    return 1
  fi
  if ! "$PYTHON" - "$log" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
if re.search(r"\b(?:ROCm|HIP)\b", text, flags=re.IGNORECASE) is None:
    raise SystemExit("server log contains no ROCm/HIP runtime marker")
matches = re.findall(
    r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers?\s+to\s+GPU",
    text,
    flags=re.IGNORECASE,
)
if not matches or not all(int(done) == int(total) > 0 for done, total in matches):
    raise SystemExit("server log does not prove full layer offload to GPU")
PY
  then
    echo "server did not prove HIP full-offload execution; see $log" >&2
    return 1
  fi
  {
    echo "pid=$pid"
    echo "pid_starttime=$starttime"
    echo "alias=$alias"
    echo "local_process_holds_dev_kfd=true"
    echo "bound_kfd_pid=$kfd_pid"
    echo "kfd_binding=exclusive_assigned_kfd_delta"
    echo "assigned_kfd_process=$scoped_line"
    echo "executable=$executable"
    echo "executable_sha256=$executable_sha"
    echo "llama_cpp_commit=$LLAMA_COMMIT"
    echo "llama_bin_sha256=$LLAMA_BIN_SHA256"
    grep -Ei '(ROCm|HIP|gfx1100|offload.*GPU)' "$log" | tail -50
  } >"$proof"
}

run_client_bench() {
  local url="$1" model="$2" concurrency="$3" label="$4" max_tokens="$5"
  local draft_mode="$6" image="${7:-}" prompt="${8:-}"
  local -a args=(
    "$PYTHON" "$BENCH_ROOT/openai_bench.py"
    --url "$url" \
    --model "$model" \
    --concurrency "$concurrency" \
    --runs "$RUNS" \
    --warmup "$WARMUP" \
    --max-tokens "$max_tokens" \
    --label "$label" \
    --run-id "$RUN_ID" \
    --manifest-sha256 "$RUN_MANIFEST_SHA256" \
    --llama-commit "$LLAMA_COMMIT" \
    --llama-bin-sha256 "$LLAMA_BIN_SHA256" \
    --output "$RESULTS_DIR/$label.json"
  )
  case "$draft_mode" in
    require) args+=(--require-draft) ;;
    forbid) args+=(--forbid-draft) ;;
    *) echo "invalid draft mode: $draft_mode" >&2; return 2 ;;
  esac
  if [[ -n "$image" ]]; then
    args+=(
      --image "$image"
      --prompt "$prompt"
      --required-content-regex '(?i)\bparse\.py\b'
    )
  else
    args+=(--prompt "$BRAIN_PROMPT" --required-numeric-prefix 80)
  fi
  "${args[@]}"
}

brain_model_path() {
  case "$1" in
    Q8_0)
      echo "$MODELS_DIR/brain/ThinkingCap-Qwen3.6-27B-Q8_0.gguf"
      ;;
    Q6_K)
      echo "$MODELS_DIR/brain/ThinkingCap-Qwen3.6-27B-Q6_K.gguf"
      ;;
    Q4_K_M)
      echo "$MODELS_DIR/brain/ThinkingCap-Qwen3.6-27B-Q4_K_M.gguf"
      ;;
    *)
      echo "unsupported quant: $1" >&2
      exit 2
      ;;
  esac
}

validate_brain_weights() {
  verify_exact_file \
    "$(brain_model_path Q8_0)" 29047082976 \
    efcb358ef86f07cf24bfd617a66bb0baa7220e9dd1c31b7d7beacd7b49e67d93
  verify_exact_file \
    "$(brain_model_path Q6_K)" 22430998496 \
    37d93cb02a08e42a2b8e917d79efc340709b90546cac1fa655121ccadf4aa791
  verify_exact_file \
    "$(brain_model_path Q4_K_M)" 16810713056 \
    b0651e28555bde7d2459ce99f091319b1a547143463e8d49f2aa7f572675fe67
  verify_exact_file \
    "$MODELS_DIR/brain/mmproj-ThinkingCap-Qwen3.6-27B-f16.gguf" \
    931145888 \
    81a714ac5e8e15687371fc95a180953a29b732962f6616f791063ff127559412
}

validate_perceive_weights() {
  verify_exact_file \
    "$MODELS_DIR/perceive/gemma-4-E4B-it-Q8_0.gguf" \
    8031242688 \
    34be82b17b4942d389b9b527170c4b058027abdd32531fda063d3d97dd8ce80a
  verify_exact_file \
    "$MODELS_DIR/perceive/mmproj-gemma-4-E4B-it-BF16.gguf" \
    991552256 \
    f77995e4b6a569ab8f0d1bfdb7e8da4a0fa5b9e6f309b9bf3bdb76164d75e29f
}

validate_perceive_fixture() {
  local expected_sha="d7903ab467f554b2fba7489380024c603c0ad3b8785ccb08f62af07cc976caf9"
  local actual_size actual_sha
  require_file "$PERCEIVE_IMAGE"
  actual_size="$(stat -c %s "$PERCEIVE_IMAGE")"
  actual_sha="$(sha256sum "$PERCEIVE_IMAGE" | awk '{print $1}')"
  if [[ "$actual_size" != "94993" || "$actual_sha" != "$expected_sha" ]]; then
    echo "perceive fixture mismatch; expected the reviewed code_01_p31_focus.png" >&2
    return 1
  fi
  printf '%s  %s\n' "$actual_sha" "$PERCEIVE_IMAGE" \
    >"$RESULTS_DIR/perceive-fixture-verified.txt"
}

start_brain() {
  local quant="$1" mtp="$2" model log
  capture_gpu_state "pre-brain-${quant}-mtp-${mtp}" || return 1
  assert_assigned_gpu_idle || return 1
  require_brain_headroom "$quant" || return 1
  assert_port_free "$BRAIN_PORT" || return 1
  BRAIN_KFD_PID=""
  model="$(brain_model_path "$quant")"
  log="$RESULTS_DIR/brain-${quant}-mtp-${mtp}-server.log"
  local -a args=(
    "$BIN"
    -m "$model"
    --mmproj "$MODELS_DIR/brain/mmproj-ThinkingCap-Qwen3.6-27B-f16.gguf"
    --alias brain-bench
    -ngl 99
    -c 16384
    -np 8
    --host 127.0.0.1
    --port "$BRAIN_PORT"
    --metrics
    --jinja
    --cache-ram 0
    --no-cache-idle-slots
    -lv "$SERVER_LOG_VERBOSITY"
  )
  if [[ "$mtp" == "on" ]]; then
    args+=(--spec-type draft-mtp --spec-draft-n-max 4)
  fi
  "${args[@]}" >"$log" 2>&1 &
  BRAIN_PID="$!"
  BRAIN_STARTTIME="$(capture_new_child_starttime "$BRAIN_PID")" || {
    echo "could not capture brain PID starttime; refusing to manage it" >&2
    if abort_unbound_new_child "$BRAIN_PID" "$BRAIN_PORT" &&
      wait_for_assigned_gpu_idle; then
      BRAIN_PID=""
      BRAIN_STARTTIME=""
    fi
    return 1
  }
  if ! wait_for_managed_identity \
    "$BRAIN_PID" "$BRAIN_STARTTIME" brain-bench; then
    echo "brain PID never acquired the expected executable identity" >&2
    if abort_unbound_new_child "$BRAIN_PID" "$BRAIN_PORT" &&
      wait_for_assigned_gpu_idle; then
      BRAIN_PID=""
      BRAIN_STARTTIME=""
    fi
    return 1
  fi
  if ! BRAIN_KFD_PID="$(
    bind_managed_kfd_pid "$BRAIN_PID" "$BRAIN_STARTTIME" brain-bench
  )"; then
    echo "brain could not be bound to one unambiguous assigned-GPU KFD PID" >&2
    stop_brain_managed || return 1
    return 1
  fi
  start_gpu_watchdog \
    "brain-${quant}-mtp-${mtp}" \
    "$BRAIN_PID" "$BRAIN_STARTTIME" brain-bench "$BRAIN_KFD_PID"
  if ! wait_for_port "$BRAIN_PORT" "$BRAIN_PID"; then
    echo "brain failed to start: quant=$quant mtp=$mtp; see $log" >&2
    finish_active_gpu_watchdog "$BRAIN_PID" || true
    stop_brain_managed || return 1
    return 1
  fi
  if ! assert_server_identity \
    "$BRAIN_PID" "$BRAIN_PORT" "$model" \
    "$MODELS_DIR/brain/mmproj-ThinkingCap-Qwen3.6-27B-f16.gguf" \
    8 "$mtp"; then
    echo "brain listener identity mismatch; refusing mislabeled evidence" >&2
    finish_active_gpu_watchdog "$BRAIN_PID" || true
    stop_brain_managed || return 1
    return 1
  fi
  if ! assert_server_rocm_execution \
    "$BRAIN_PID" "$BRAIN_STARTTIME" brain-bench "$log" \
    "$RESULTS_DIR/brain-${quant}-mtp-${mtp}-gpu-proof.txt" \
    "$BRAIN_KFD_PID" ||
    ! assert_no_foreign_gpu_processes ||
    ! require_post_load_reserve; then
    finish_active_gpu_watchdog "$BRAIN_PID" || true
    stop_brain_managed || return 1
    return 1
  fi
  if ! capture_gpu_state "brain-${quant}-mtp-${mtp}-resident"; then
    finish_active_gpu_watchdog "$BRAIN_PID" || true
    stop_brain_managed || return 1
    return 1
  fi
}

run_brain_matrix() {
  local quant mtp concurrency label draft_mode peak_raw cell_failed

  safe_stack_down perceive
  safe_stack_down brain
  assert_role_stopped perceive
  assert_role_stopped brain

  for quant in Q6_K Q4_K_M Q8_0; do
    for mtp in off on; do
      if ! start_brain "$quant" "$mtp"; then
        if [[ -n "$BRAIN_PID" ]]; then
          echo "brain start failed without safe process convergence; aborting matrix" >&2
          return 1
        fi
        FAILURES=$((FAILURES + 1))
        continue
      fi
      for concurrency in 1 4 8; do
        label="brain-${quant}-mtp-${mtp}-c${concurrency}"
        [[ "$mtp" == "on" ]] && draft_mode=require || draft_mode=forbid
        assert_no_foreign_gpu_processes
        require_post_load_reserve
        cell_failed=0
        peak_raw="$RESULTS_DIR/$label-vram-samples.tsv"
        : >"$peak_raw"
        sample_vram "$peak_raw" &
        VRAM_SAMPLER_PID="$!"
        if ! run_client_bench \
          "http://127.0.0.1:$BRAIN_PORT/v1/chat/completions" \
          brain-bench "$concurrency" "$label" "$BRAIN_MAX_TOKENS" \
          "$draft_mode"; then
          cell_failed=1
        fi
        if ! finish_vram_sample \
          "$VRAM_SAMPLER_PID" "$peak_raw" \
          "$RESULTS_DIR/$label-peak-vram-bytes.txt"; then
          cell_failed=1
        fi
        VRAM_SAMPLER_PID=""
        if ! check_gpu_watchdog \
          "$WATCHDOG_PID" "$WATCHDOG_FLAG" "$BRAIN_PID" \
          "$BRAIN_STARTTIME" brain-bench; then
          cell_failed=1
        fi
        if (( cell_failed == 1 )); then
          rm -f "$RESULTS_DIR/$label.json"
          FAILURES=$((FAILURES + 1))
        fi
        assert_no_foreign_gpu_processes
        require_post_load_reserve
        if ! managed_pid_identity_matches \
          "$BRAIN_PID" "$BRAIN_STARTTIME" brain-bench; then
          echo "brain server stopped during $label; skipping remaining cells for this load" >&2
          FAILURES=$((FAILURES + 1))
          break
        fi
      done
      curl --fail --silent --max-time 5 \
        "http://127.0.0.1:$BRAIN_PORT/metrics" \
        >"$RESULTS_DIR/brain-${quant}-mtp-${mtp}-metrics.prom" || true
      if ! finish_active_gpu_watchdog "$BRAIN_PID"; then
        FAILURES=$((FAILURES + 1))
      fi
      stop_brain_managed
      sleep 5
    done
  done
}

start_perceive() {
  local parallel="$1" log
  capture_gpu_state "pre-perceive-np${parallel}" || return 1
  assert_assigned_gpu_idle || return 1
  require_perceive_headroom || return 1
  assert_port_free "$PERCEIVE_PORT" || return 1
  PERCEIVE_KFD_PID=""
  log="$RESULTS_DIR/perceive-np${parallel}-server.log"
  "$BIN" \
    -m "$MODELS_DIR/perceive/gemma-4-E4B-it-Q8_0.gguf" \
    --mmproj "$MODELS_DIR/perceive/mmproj-gemma-4-E4B-it-BF16.gguf" \
    --alias perceive-bench \
    -ngl 99 \
    -c 16384 \
    -np "$parallel" \
    --host 127.0.0.1 \
    --port "$PERCEIVE_PORT" \
    --metrics \
    --jinja \
    --cache-ram 0 \
    --no-cache-idle-slots \
    -lv "$SERVER_LOG_VERBOSITY" \
    >"$log" 2>&1 &
  PERCEIVE_PID="$!"
  PERCEIVE_STARTTIME="$(capture_new_child_starttime "$PERCEIVE_PID")" || {
    echo "could not capture perceive PID starttime; refusing to manage it" >&2
    if abort_unbound_new_child "$PERCEIVE_PID" "$PERCEIVE_PORT" &&
      wait_for_assigned_gpu_idle; then
      PERCEIVE_PID=""
      PERCEIVE_STARTTIME=""
    fi
    return 1
  }
  if ! wait_for_managed_identity \
    "$PERCEIVE_PID" "$PERCEIVE_STARTTIME" perceive-bench; then
    echo "perceive PID never acquired the expected executable identity" >&2
    if abort_unbound_new_child "$PERCEIVE_PID" "$PERCEIVE_PORT" &&
      wait_for_assigned_gpu_idle; then
      PERCEIVE_PID=""
      PERCEIVE_STARTTIME=""
    fi
    return 1
  fi
  if ! PERCEIVE_KFD_PID="$(
    bind_managed_kfd_pid \
      "$PERCEIVE_PID" "$PERCEIVE_STARTTIME" perceive-bench
  )"; then
    echo "perceive could not be bound to one unambiguous assigned-GPU KFD PID" >&2
    stop_perceive_managed || return 1
    return 1
  fi
  start_gpu_watchdog \
    "perceive-np${parallel}" \
    "$PERCEIVE_PID" "$PERCEIVE_STARTTIME" perceive-bench "$PERCEIVE_KFD_PID"
  if ! wait_for_port "$PERCEIVE_PORT" "$PERCEIVE_PID"; then
    echo "perceive failed to start: -np $parallel; see $log" >&2
    finish_active_gpu_watchdog "$PERCEIVE_PID" || true
    stop_perceive_managed || return 1
    return 1
  fi
  if ! assert_server_identity \
    "$PERCEIVE_PID" "$PERCEIVE_PORT" \
    "$MODELS_DIR/perceive/gemma-4-E4B-it-Q8_0.gguf" \
    "$MODELS_DIR/perceive/mmproj-gemma-4-E4B-it-BF16.gguf" \
    "$parallel" off; then
    echo "perceive listener identity mismatch; refusing mislabeled evidence" >&2
    finish_active_gpu_watchdog "$PERCEIVE_PID" || true
    stop_perceive_managed || return 1
    return 1
  fi
  if ! assert_server_rocm_execution \
    "$PERCEIVE_PID" "$PERCEIVE_STARTTIME" perceive-bench "$log" \
    "$RESULTS_DIR/perceive-np${parallel}-gpu-proof.txt" \
    "$PERCEIVE_KFD_PID" ||
    ! assert_no_foreign_gpu_processes ||
    ! require_post_load_reserve; then
    finish_active_gpu_watchdog "$PERCEIVE_PID" || true
    stop_perceive_managed || return 1
    return 1
  fi
  if ! capture_gpu_state "perceive-np${parallel}-resident"; then
    finish_active_gpu_watchdog "$PERCEIVE_PID" || true
    stop_perceive_managed || return 1
    return 1
  fi
}

run_perceive_sweep() {
  local parallel label peak_raw cell_failed
  require_file "$PERCEIVE_IMAGE"

  safe_stack_down brain
  safe_stack_down perceive
  assert_role_stopped brain
  assert_role_stopped perceive
  for parallel in 1 2 4; do
    if ! start_perceive "$parallel"; then
      if [[ -n "$PERCEIVE_PID" ]]; then
        echo "perceive start failed without safe process convergence; aborting sweep" >&2
        return 1
      fi
      FAILURES=$((FAILURES + 1))
      continue
    fi
    label="perceive-Q8_0-np${parallel}-c${parallel}"
    assert_no_foreign_gpu_processes
    require_post_load_reserve
    cell_failed=0
    peak_raw="$RESULTS_DIR/$label-vram-samples.tsv"
    : >"$peak_raw"
    sample_vram "$peak_raw" &
    VRAM_SAMPLER_PID="$!"
    if ! run_client_bench \
      "http://127.0.0.1:$PERCEIVE_PORT/v1/chat/completions" \
      perceive-bench "$parallel" "$label" "$PERCEIVE_MAX_TOKENS" \
      forbid "$PERCEIVE_IMAGE" "$PERCEIVE_PROMPT"; then
      cell_failed=1
    fi
    if ! finish_vram_sample \
      "$VRAM_SAMPLER_PID" "$peak_raw" \
      "$RESULTS_DIR/$label-peak-vram-bytes.txt"; then
      cell_failed=1
    fi
    VRAM_SAMPLER_PID=""
    if ! check_gpu_watchdog \
      "$WATCHDOG_PID" "$WATCHDOG_FLAG" "$PERCEIVE_PID" \
      "$PERCEIVE_STARTTIME" perceive-bench; then
      cell_failed=1
    fi
    assert_no_foreign_gpu_processes
    require_post_load_reserve
    curl --fail --silent --max-time 5 \
      "http://127.0.0.1:$PERCEIVE_PORT/metrics" \
      >"$RESULTS_DIR/perceive-np${parallel}-metrics.prom" || true
    if ! finish_active_gpu_watchdog "$PERCEIVE_PID"; then
      cell_failed=1
    fi
    if (( cell_failed == 1 )); then
      rm -f "$RESULTS_DIR/$label.json"
      FAILURES=$((FAILURES + 1))
    fi
    stop_perceive_managed
    sleep 3
  done
}

main() {
  local mode="${1:-all}" status
  case "$mode" in
    all|brain|perceive) ;;
    *) echo "usage: $0 [all|brain|perceive]" >&2; exit 2 ;;
  esac
  require_file "$BIN"
  require_file "$BENCH_ROOT/openai_bench.py"
  require_file "$BENCH_ROOT/kfd_scope.py"
  require_file "$STACK_ROOT/server-stack.sh"
  command -v rocm-smi >/dev/null
  command -v rocminfo >/dev/null
  command -v curl >/dev/null
  command -v sha256sum >/dev/null
  command -v ss >/dev/null
  command -v hostname >/dev/null
  command -v nproc >/dev/null
  command -v lscpu >/dev/null
  command -v "$PYTHON" >/dev/null

  capture_gpu_state before
  status="$("$STACK_ROOT/server-stack.sh" status)"
  printf '%s\n' "$status" >"$RESULTS_DIR/stack-before.txt"
  if grep -Eq '^perceive[[:space:]]+up ' <<<"$status"; then
    ENTRY_PERCEIVE_WAS_UP=1
  fi
  {
    echo "entry_perceive_was_up=$ENTRY_PERCEIVE_WAS_UP"
    echo "exit_policy=leave brain and perceive stopped"
    echo "reason=avoid an unmonitored GPU restore load after the benchmark"
  } >"$RESULTS_DIR/service-state-policy.txt"
  safe_stack_down perceive
  safe_stack_down brain
  assert_role_stopped perceive
  assert_role_stopped brain
  wait_for_assigned_gpu_idle
  verify_llama_rocm_build
  : >"$RESULTS_DIR/weights-verified.txt"
  case "$mode" in
    brain) validate_brain_weights ;;
    perceive)
      validate_perceive_weights
      validate_perceive_fixture
      ;;
    all)
      validate_brain_weights
      validate_perceive_weights
      validate_perceive_fixture
      ;;
  esac
  capture_environment
  write_run_manifest "$mode"

  case "$mode" in
    brain)
      run_brain_matrix
      ;;
    perceive)
      run_perceive_sweep
      ;;
    all)
      run_brain_matrix
      run_perceive_sweep
      ;;
  esac

  capture_gpu_state after
  if (( FAILURES > 0 )); then
    "$PYTHON" "$BENCH_ROOT/summarize_p31.py" \
      --results "$RESULTS_DIR" \
      --output "$RESULTS_DIR/p31-summary-partial.md" \
      --allow-partial || true
    echo "P3.1 completed with $FAILURES failed matrix cells." >&2
    exit 1
  fi
  "$PYTHON" "$BENCH_ROOT/summarize_p31.py" \
    --results "$RESULTS_DIR" \
    --output "$RESULTS_DIR/p31-summary.md"
  echo "P3.1 raw evidence written to $RESULTS_DIR"
}

main "$@"
