#!/bin/bash
# Reconstruct the patched Honcho working tree (tasks M2.1-M2.3).
# The submodule pins upstream plastic-labs/honcho at 340175ad (pristine);
# our modifications live as ordered diffs in deploy/mac/honcho-patches/.
# Idempotent: safe to re-run; refuses partial patches or unrelated edits.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HONCHO="$ROOT/third_party/honcho"
PATCHES="$ROOT/deploy/mac/honcho-patches"
PIN="340175ad5f8b49b73007481eef1885ffe99ac768"
MODE="${1:-setup}"

if [[ "$MODE" != "setup" && "$MODE" != "--check" ]]; then
  echo "usage: $0 [--check]" >&2
  exit 2
fi

if [ ! -f "$HONCHO/pyproject.toml" ]; then
  if [[ "$MODE" == "--check" ]]; then
    echo "error: Honcho submodule is not initialized; run make setup" >&2
    exit 1
  fi
  git -C "$ROOT" submodule update --init third_party/honcho
fi

if [[ "$(git -C "$HONCHO" rev-parse HEAD)" != "$PIN" ]]; then
  if [[ -n "$(git -C "$HONCHO" status --porcelain --untracked-files=all)" ]]; then
    echo "error: Honcho is dirty at the wrong revision; refusing to overwrite it" >&2
    exit 1
  fi
  git -C "$HONCHO" checkout --detach -q "$PIN"
fi

if git -C "$HONCHO" status --porcelain --untracked-files=all | grep -q '^??'; then
  echo "error: unexpected Honcho changes (untracked files); clean them explicitly" >&2
  exit 1
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/dejaview-honcho-check.XXXXXX")"
trap 'rm -rf "$tmp_dir"' EXIT
expected="$tmp_dir/expected.diff"
actual="$tmp_dir/actual.diff"
index="$tmp_dir/index"

GIT_INDEX_FILE="$index" git -C "$HONCHO" read-tree HEAD
GIT_INDEX_FILE="$index" git -C "$HONCHO" apply --cached "$PATCHES/01-local-patches.diff"
GIT_INDEX_FILE="$index" git -C "$HONCHO" apply --cached "$PATCHES/02-sanitize-prompts.diff"
GIT_INDEX_FILE="$index" git -C "$HONCHO" diff --cached --binary --no-ext-diff > "$expected"
git -C "$HONCHO" diff HEAD --binary --no-ext-diff > "$actual"

if [[ ! -s "$actual" && "$MODE" == "setup" ]]; then
  git -C "$HONCHO" apply "$PATCHES/01-local-patches.diff"
  git -C "$HONCHO" apply "$PATCHES/02-sanitize-prompts.diff"
  git -C "$HONCHO" diff HEAD --binary --no-ext-diff > "$actual"
fi

if ! cmp -s "$expected" "$actual"; then
  echo "error: unexpected Honcho changes; expected exactly the two repository patches" >&2
  echo "inspect with: git -C third_party/honcho status --short" >&2
  exit 1
fi

echo "Honcho $PIN exact patch stack verified"
