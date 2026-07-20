#!/bin/bash
# Reconstruct the patched Honcho working tree (tasks M2.1-M2.3).
# The submodule pins upstream plastic-labs/honcho at 340175ad (pristine);
# our modifications live as ordered diffs in deploy/mac/honcho-patches/.
# Idempotent: safe to re-run; refuses to double-apply.
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HONCHO="$ROOT/third_party/honcho"
PATCHES="$ROOT/deploy/mac/honcho-patches"

if [ ! -f "$HONCHO/pyproject.toml" ]; then
  git -C "$ROOT" submodule update --init third_party/honcho
fi

cd "$HONCHO"
git checkout -q 340175ad

if git diff --quiet; then
  git apply "$PATCHES/01-local-patches.diff"
  git apply "$PATCHES/02-sanitize-prompts.diff"
  echo "patches applied on pristine 340175ad"
else
  echo "working tree already modified; skipping patch apply (run 'git -C third_party/honcho checkout .' to reset first)"
fi

git -C "$HONCHO" status --short
