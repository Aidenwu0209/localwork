#!/bin/bash
# Mac-side dev model downloads (task M2.5 prerequisite).
# Dev stack on Apple M5 / 16GB: small trio at full quant + E4B at Q4_0 (serves both
# `perceive` and dev `brain`). Downloads via hf-mirror resolve URLs (curl resumable).
# Target dir is outside the repo; *.gguf is gitignored anyway.
#
# NOTE on quant names: the ggml-org/gemma-4-E4B-it-GGUF repo uses llama.cpp's newer
# naming (Q4_0 / Q8_0), NOT Q4_K_M — the latter does not exist there and the server
# returns a 15-byte "Entry not found" body. We use Q4_0 (~4.6 GB) for the 16GB budget
# (matches the handbook's ≈5.5 GB estimate with the BF16 mmproj).
set -ex
M=https://hf-mirror.com
DIR="${1:-$HOME/Projects/Aidenwu0209/models/dejaview}"
mkdir -p "$DIR"/{sentinel,fast,embed,perceive}
cd "$DIR"

# dl <local_path> <url>: verify against the remote Content-Length so a truncated or
# 404-body file (e.g. a 15-byte "Entry not found") is re-fetched instead of skipped.
dl() {
  local path="$1" url="$2"
  local remote_size
  remote_size=$(curl -sIL "$url" | awk 'BEGIN{IGNORECASE=1}/^content-length:/{v=$2} END{gsub(/\r/,"",v); print v}')
  if [ -s "$path" ] && [ -n "$remote_size" ] && [ "$(stat -f%z "$path" 2>/dev/null || stat -c%s "$path" 2>/dev/null)" = "$remote_size" ]; then
    echo "SKIP $path (size ok: $remote_size)"
    return
  fi
  # Delete a wrong-size stub (e.g. truncated download or 404 body) before resuming.
  rm -f "$path"
  curl -L --retry 3 -C - -o "$path" "$url"
  echo "OK $path ($(stat -f%z "$path" 2>/dev/null || stat -c%s "$path" 2>/dev/null) bytes)"
}

dl sentinel/MiniCPM-V-4_6-Q4_K_M.gguf       "$M/openbmb/MiniCPM-V-4.6-gguf/resolve/main/MiniCPM-V-4_6-Q4_K_M.gguf"
dl sentinel/mmproj-model-f16.gguf           "$M/openbmb/MiniCPM-V-4.6-gguf/resolve/main/mmproj-model-f16.gguf"
dl fast/MiniCPM5-1B-Q8_0.gguf               "$M/openbmb/MiniCPM5-1B-GGUF/resolve/main/MiniCPM5-1B-Q8_0.gguf"
dl embed/Qwen3-Embedding-0.6B-Q8_0.gguf     "$M/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf"
dl perceive/gemma-4-E4B-it-Q4_0.gguf        "$M/ggml-org/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_0.gguf"
dl perceive/mmproj-gemma-4-E4B-it-BF16.gguf "$M/ggml-org/gemma-4-E4B-it-GGUF/resolve/main/mmproj-gemma-4-E4B-it-BF16.gguf"

find "$DIR" -name "*.gguf" -exec shasum -a 256 {} \; > "$DIR/sha256-mac.txt"
echo "=== MAC DEV MODELS COMPLETE ==="
