#!/bin/bash
# Rebuild the pinned llama.cpp HIP server on a fresh Radeon Cloud overlay.
set -euo pipefail

COMMIT=76f46ad29d61fd8c1401e8221842934bf62a6064
SOURCE_DIR="${LLAMA_SOURCE_DIR:-/root/llama.cpp}"
BUILD_JOBS="${LLAMA_BUILD_JOBS:-32}"

if [[ ! -d "$SOURCE_DIR/.git" ]]; then
  if [[ -e "$SOURCE_DIR" ]]; then
    echo "refusing to replace non-git path: $SOURCE_DIR" >&2
    exit 2
  fi
  GIT_SSL_NO_VERIFY=true git clone --filter=blob:none --no-checkout \
    https://github.com/ggml-org/llama.cpp "$SOURCE_DIR"
fi

GIT_SSL_NO_VERIFY=true git -C "$SOURCE_DIR" fetch --depth 1 origin "$COMMIT"
GIT_SSL_NO_VERIFY=true git -C "$SOURCE_DIR" checkout --detach "$COMMIT"
[[ "$(git -C "$SOURCE_DIR" rev-parse HEAD)" == "$COMMIT" ]]
if [[ -n "$(
  GIT_SSL_NO_VERIFY=true git -C "$SOURCE_DIR" \
    status --porcelain=v1 --untracked-files=all
)" ]]; then
  echo "refusing to build from a dirty llama.cpp worktree or index" >&2
  exit 2
fi

cmake --fresh -S "$SOURCE_DIR" -B "$SOURCE_DIR/build" \
  -DGGML_HIP=ON \
  -DAMDGPU_TARGETS=gfx1100 \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=ON \
  -DLLAMA_CURL=OFF
cmake --build "$SOURCE_DIR/build" --config Release --clean-first -j"$BUILD_JOBS"

"$SOURCE_DIR/build/bin/llama-server" --version
ldd "$SOURCE_DIR/build/bin/llama-server"
