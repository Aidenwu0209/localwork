# P3.1 ROCm benchmark harness

This directory contains the reproducible harness for the Phase 3 ROCm
ablation. It sends synthetic prompts only and writes one raw JSON record per
matrix cell.

## Safety gate

Run this only after SSH is reachable. The runner's first live-host evidence is
`rocm-smi`; it then inventories KFD GPU PIDs and refuses any unrecognized
co-tenant before changing model state. Exact file sizes and SHA256 values are
required for Q8/Q6/Q4 and the two multimodal projectors, so a partial or
silently substituted quant cannot run. Benchmark ports must be empty and the
new listener PID, model path, and MTP command line must match before a cell is
accepted. The pinned binary revision must be `76f46ad29`; every loaded server
must appear in KFD, use the same executable SHA256, identify the gfx1100 ROCm
host, and log full `N/N` layer offload. During every measured cell a 200 ms
watchdog aborts only the harness-owned server if an unfamiliar KFD PID appears
or free VRAM falls below 6 GB.

Benchmark servers use llama.cpp trace verbosity (`-lv 4`) so the raw log
records the selected ROCm device, projected device-memory use, and the exact
`N/N` layer-offload line required by the proof gate.

Prompt caching is disabled twice: each request sets `cache_prompt=false`, and
each benchmark server starts with `--cache-ram 0 --no-cache-idle-slots`.
Every response must report `timings.cache_n=0`, preventing warm-up or earlier
slots from inflating later prefill measurements.

The runner stops `perceive` before the brain sweep and leaves both `brain` and
`perceive` stopped at exit. It never performs an unmonitored GPU restore load;
restore services explicitly only after reviewing the cleanup evidence. Other
DejaView small roles are left untouched.

## Server run

From a repository checkout on the AMD host:

```bash
cd deploy/server/bench
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
nohup env P31_RUN_ID="$run_id" bash run-p31-rocm.sh all \
  >"/tmp/dejaview-p31-$run_id.log" 2>&1 </dev/null &
echo "$!" >"/tmp/dejaview-p31-$run_id.pid"
tail -f "/tmp/dejaview-p31-$run_id.log"
```

`nohup` keeps the matrix alive through an SSH disconnect. A HUP received
without `nohup` still triggers targeted cleanup. A result directory must be
empty at start; never reuse a failed `P31_RUN_ID`. Start a fresh run instead of
mixing old and new cells.

Optional environment variables:

| Variable | Default | Purpose |
|---|---:|---|
| `P31_RESULTS_DIR` | `/tmp/dejaview-p31/<UTC run id>` | Raw evidence directory; copy only reviewed evidence into git |
| `P31_RUN_ID` | current UTC timestamp | Stable run-directory name |
| `P31_RUNS` | `3` | Measured batches per matrix cell; must stay at least 3 |
| `P31_WARMUP` | `1` | Warm-up batches before measurements |
| `P31_BRAIN_MAX_TOKENS` | `256` | Fixed output budget; enough for the exact `1..80` gate |
| `P31_PERCEIVE_MAX_TOKENS` | `96` | Fixed output budget for perceive |
| `P31_PERCEIVE_IMAGE` | `tests/assets/screenshots/code_01_p31_focus.png` | Fixed synthetic read-screen fixture with a legible active-tab label |
| `LLAMA_BIN` | `/root/llama.cpp/build/bin/llama-server` | Exact HIP binary |
| `DEV_MODELS_DIR` | `/root/dejaview-models` | Model root |

The complete brain sweep is a full factorial:

- quant: Q6_K / Q4_K_M / Q8_0 (Q8 runs last);
- MTP: off / `--spec-type draft-mtp --spec-draft-n-max 4`;
- client concurrency: 1 / 4 / 8;
- server slots: `-np 8` for all brain cells.

The perceive sweep pairs server `-np` and client concurrency at 1/2/4 and sends
the same synthetic PNG through the real multimodal `image_url` path. It runs
only the exact `(np, concurrency)` pairs `(1,1)`, `(2,2)`, and `(4,4)`. The
fixture is a deterministic top-left focus crop of synthetic `code_01.png`,
pinned by SHA256; the enlarged active tab makes `parse.py` legible after VLM
resizing. Every response must identify `parse.py`; an empty, unrelated, or
text-only answer fails the cell. Every cell runs one
warm-up plus at least three measured batches. Summaries include
the llama-server per-request prefill/decode rates, aggregate end-to-end output
throughput, request P50/P95, batch P50/P95, and the correct numeric-sequence
prefix as a small cross-quant quality/compliance sample. Full response timings
remain in each JSON file for audit. MTP-on cells must report nonzero drafted
tokens; zero accepted drafts remain a valid negative result. MTP-off cells
must report zero. At temperature zero,
on/off response hashes are compared in request order. A mismatch remains
visible as a negative ablation result and marks MTP unsafe instead of deleting
the raw measurements.

At the end of a complete run, `summarize_p31.py` derives
`p31-summary.md` directly from the raw JSON and rocm-smi snapshots. It fails
closed when any of the 18 brain cells or three perceive cells is missing,
mislabeled, short of three trials, missing timings, missing visual input, or
missing MTP activation evidence. A checksummed run manifest binds every cell to
one run id, llama.cpp commit/binary, weight list, prompt, image, and measurement
configuration; the summarizer recomputes critical medians from raw samples.
Resident and sampled peak VRAM are separate.

Do not promote P3.1 to `accept` until all cells are measured, VRAM evidence is
rendered into `docs/assets/`, the medians are transcribed into
`docs/benchmarks.md`, and the MTP conclusion is appended to
`docs/verification-log.md`.
