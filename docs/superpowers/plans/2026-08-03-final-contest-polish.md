# DejaView Final Contest Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the remaining avoidable contest risks while proving the current checkout behaves as a mature local digital-memory product.

**Architecture:** Preserve accepted live/ROCm evidence and add three bounded release units: product-owned local privacy inference, a pure-Python submission contract, and a rebuilt English-primary 3–5-minute cut around immutable six-act footage. Finish with a current browser/live audit and exact-commit CI evidence.

**Tech Stack:** Bash, Python 3.12 standard library, unittest, ffmpeg/ffprobe, macOS `say`, llama.cpp, LiteLLM, FastAPI, GitHub Actions, Playwright/browser audit.

## Global Constraints

- Preserve the local digital-memory narrative and logical roles `brain`, `perceive`, `sentinel`, `fast`, and `embed`.
- Do not modify accepted P3.1 evidence or the original six-act MP4.
- Do not read `.env`, expose real coordinates/PII, stage `third_party/honcho`, or claim human submission gates.
- Every behavior change follows red-green-refactor and every completion claim requires fresh full verification.
- Long-running test processes use isolated runtime/data paths and exact PID cleanup; never broad-kill.

---

### Task 1: Product-owned local privacy runtime

**Files:**
- Modify: `tests/release/test_product_stack.py`
- Modify: `deploy/mac/product-stack.sh`
- Modify: `deploy/mac/llama-launch/gateway.sh`
- Modify: `tests/release/test_release_contract.py`
- Modify: `Makefile`

**Interfaces:**
- Consumes: `deploy/mac/llama-launch/dev-stack.sh up sentinel|down|status` with `DEJAVIEW_RUNTIME_DIR`.
- Produces: `product-stack.sh up|status|down` ownership of a dedicated privacy runtime plus application services; `DEJAVIEW_SKIP_PRIVACY_STACK=1` for isolated contract tests.

- [ ] **Step 1: Write failing ownership and rollback tests**

  Add tests that install a fake `dev-stack.sh`, record `up sentinel` and `down`,
  require `product-up` to start it before service processes, require failure
  rollback to call `down`, require repeated `up` to be idempotent, and require
  `product-down` not to stop a pre-existing privacy stack. Add tests that
  `status` exits nonzero with `NOT_READY` for any unowned service, rejects an
  HTTP-200 legacy health payload, rejects a PID record whose source revision no
  longer matches, and reports `READY` only when all current contracts pass.

- [ ] **Step 2: Verify the tests fail for missing privacy lifecycle**

  Run: `python3 -m unittest tests.release.test_product_stack -v`

  Expected: the new tests fail because `product-stack.sh` only probes port 4000
  and never invokes the privacy stack.

- [ ] **Step 3: Implement minimal owned privacy lifecycle**

  Add a product-runtime marker containing no secret. Start the dev stack under
  `$RUNTIME_DIR/privacy` only when the gateway is unavailable; verify both
  `/v1/models` and role ownership; stop it only when the current product run
  created the marker. Roll back it after any later startup failure. An occupied
  unowned port remains a named error, never an adoption or signal target.
  Preflight the fixed service ports before starting infrastructure. Bind each
  PID record to the current service-tree revision and validate service-specific
  health JSON rather than accepting an arbitrary HTTP 200. Emit one strict
  `READY`/`NOT_READY` summary and a truthful status exit code.

- [ ] **Step 4: Remove request-debug logging by default**

  Delete `--detailed_debug` from the Mac gateway launcher. Add a release test
  asserting neither Mac nor server production gateway entry point enables
  detailed debug or a non-loopback listener.

- [ ] **Step 5: Verify runtime contracts**

  Run: `python3 -m unittest tests.release.test_product_stack tests.release.test_release_contract -v`

  Expected: PASS, including rollback, idempotency, stale PID refusal, loopback,
  and no detailed-debug assertions.

---

### Task 2: Machine-executable submission package contract

**Files:**
- Create: `scripts/submission_check.py`
- Create: `tests/release/test_submission_check.py`
- Modify: `Makefile`
- Modify: `.github/workflows/first-party.yml`
- Modify: `tests/release/test_release_contract.py`

**Interfaces:**
- Produces: `check_submission(root: Path, *, ffprobe: str = "ffprobe") -> list[CheckResult]` and CLI exit `0` only when every required check passes.
- `CheckResult` is a frozen dataclass with `name: str`, `ok: bool`, and `detail: str`.

- [ ] **Step 1: Write failing tests for manifest, duration, streams, SRT and privacy**

  Build tiny temporary release trees. Assert failures for a 179.9-second video,
  mismatched SHA, missing AAC stream, overlapping SRT cues, absent Act 6/fallback
  evidence, tracked `.env`, ephemeral host coordinates, and missing README links.
  Assert a fully synthetic 180-second fixture passes using a fake ffprobe JSON
  executable.

- [ ] **Step 2: Verify RED**

  Run: `python3 -m unittest tests.release.test_submission_check -v`

  Expected: import failure because `scripts/submission_check.py` does not exist.

- [ ] **Step 3: Implement the checker with standard-library parsers**

  Parse JSON manifest and SRT timestamps; use SHA-256 streaming reads; call
  ffprobe with a list argv and parse JSON; use `git ls-files` for tracked-file
  privacy checks; inspect ZIP members for valid DOCX/PPTX containers without
  extracting them. Print one `PASS|FAIL <name>: <detail>` row and a final count.

- [ ] **Step 4: Wire local and CI entry points**

  Add `make submission-check`; require the release contract to expose it; run
  it in CI after `make test` so the exact committed large artifacts are checked.

- [ ] **Step 5: Verify GREEN and the current expected video-duration failure**

  Run: `python3 -m unittest tests.release.test_submission_check -v`

  Expected: unit tests PASS.

  Run: `make submission-check`

  Expected before Task 3: FAIL only for the current 157.2-second submission
  video and any manifest fields that still reference it.

---

### Task 3: English-primary 3–5-minute video

**Files:**
- Create: `scripts/build-submission-video.sh`
- Create: `docs/assets/demo/dejaview-p34-six-act-20260802-en-3m.srt`
- Create: `docs/assets/demo/dejaview-p34-six-act-20260802-en-3m.mp4`
- Modify: `docs/assets/demo/p34-video-manifest.json`
- Modify: `README.md`
- Modify: `README.zh.md`
- Modify: `docs/submission/PROJECT_SPECIFICATION.md`

**Interfaces:**
- Consumes immutable `dejaview-p34-six-act-20260802.mp4`, accepted Grafana/ROCm
  screenshots, and an English cue TSV generated inside the script.
- Produces a 190–220-second 1920×1080 H.264/AAC video and monotonic full-length SRT.

- [ ] **Step 1: Record the RED submission check**

  Run `make submission-check` and retain the expected duration failure in the
  verification notes before changing the manifest.

- [ ] **Step 2: Build informative wrapper segments**

  Extract an authentic architecture frame from the source recording for a
  12-second opening. Add a 12-second pan/zoom over the accepted ROCm/Grafana
  evidence and a 10–15-second closing over the accepted self-check image. Use
  English title-safe overlays; do not duplicate live footage or fabricate data.

- [ ] **Step 3: Generate English-primary narration and captions**

  Use macOS `say` to create cue-level English AIFF clips, fit each clip inside
  its cue without truncating words, concatenate them with silence, and mix a
  quiet original track only when it cannot compete with English speech. Burn
  the same cue text into the output and retain the SRT.

- [ ] **Step 4: Render and update exact manifest values**

  Render H.264 `yuv420p` + AAC at 1920×1080/30 fps. Use ffprobe and `shasum -a
  256` output to update exact duration, size, codecs, source hash, output hash,
  caption hash, English language statement, and complete timeline.

- [ ] **Step 5: Verify video structure and visual quality**

  Run: `make submission-check`

  Expected: all video, caption, hash, duration and stream rows PASS.

  Extract frames at the opening, every act boundary, ROCm card and closing;
  create a contact sheet; inspect the original-resolution frames for readable
  captions, correct aspect, no black/loading/cropped frames, and truthful labels.

---

### Task 4: Current daily-product live and UX audit

**Files:**
- Modify only if a current evidence-backed blocker is found: `services/agentd/src/agentd/web/index.html`, `services/agentd/src/agentd/web/product.css`, `services/agentd/src/agentd/web/product.js`, and their existing tests.
- Create QA screenshots outside Git tracking under `.superpowers/p319-ui-audit/`.
- Append: `docs/verification-log.md`.

**Interfaces:**
- Consumes managed product stack from Task 1 and synthetic `dejaview_demo` data.
- Produces current desktop/mobile screenshots, step health, axe results, and a live Radeon citation/evidence proof.

- [ ] **Step 1: Remove only the exact stale repo-owned manual listeners**

  Resolve listeners on 8090/8101, verify their executable and checkout CWD, send
  TERM only to those exact stale PIDs, and confirm both ports close. Do not stop
  Docker, SSH, Honcho, or any unknown process.

- [ ] **Step 2: Start and prove managed current services**

  Run `make product-up`, then `make product-status`. Require current safe health
  schemas and `ocrd`, `memoryd`, `agentd` to report `managed and ready`; require
  local Sentinel and Radeon model discovery.

- [ ] **Step 3: Run synthetic live product flow**

  Use an isolated database/data root. Ask for synthetic PR #1842, assert one
  valid citation, `backend=radeon`, evidence metadata without path/OCR, and a
  decodable capability-gated image. Audit privacy and Honcho/profile endpoints.

- [ ] **Step 4: Capture and inspect desktop/mobile UX**

  Capture 1440×900 and 390×844: home/status, timeline, answer with citation,
  evidence drawer, privacy summary, and profile state. Inspect saved screenshots,
  run axe and keyboard/focus tests, and log any evidence limit.

- [ ] **Step 5: Fix only proven blockers through TDD**

  For each blocker, add the smallest failing Python/Node/browser test, observe
  RED, implement the minimal UI/API change, observe GREEN, then rerun all product
  tests. If no blocker exists, make no cosmetic code change.

- [ ] **Step 6: Clean exact synthetic/runtime state**

  Stop only the product-owned processes, remove only the isolated demo database
  and temporary data created in this task, and confirm default user data remains.

---

### Task 5: Exact-release closure

**Files:**
- Modify: `STATUS.md`
- Modify: `TASKBOARD.json`
- Modify: `docs/EXECUTION_HANDBOOK.md`
- Modify: `docs/licenses.md`
- Modify: `docs/verification-log.md`
- Modify: `docs/AGENT_KICKOFF_PROMPT.md` only if it contains stale P3.19 state.

**Interfaces:**
- Produces P3.19 `accept` only after every acceptance command below succeeds.

- [ ] **Step 1: Upgrade GitHub Actions only when primary-source verified**

  If official releases confirm Node-24-native replacements, update checkout and
  setup-uv versions. Otherwise retain current pinned major versions and document
  the upstream-only warning rather than guessing.

- [ ] **Step 2: Run full local gates**

  Run `make test`, `make submission-check`, JSON parsing, shell syntax,
  coordinate/secret scans, `git diff --check`, author/trailer checks, and visual
  artifact hashes. Require zero failure; deprecation warnings must be explicitly
  classified as upstream or fixed.

- [ ] **Step 3: Run a clean staged-tree checkout**

  Build a temporary commit from only intended staged files, clone it with the
  Honcho submodule, run `make setup` twice, `make doctor`, `make test`, and
  `make submission-check`, then move the temporary checkout to Trash.

- [ ] **Step 4: Reconcile documentation and state**

  Append fresh `[VERIFY]` evidence, set P3.19 `doing → accept`, set totals to
  `49/49`, link the new video everywhere, and keep registration/Rules/official
  fork/PR/upload/server-demo-data checks visibly human-only.

- [ ] **Step 5: Commit, verify identity, push and wait for exact CI**

  Commit only intended files as `Aidenwu0209 <1418557225@qq.com>`, verify no AI
  trailer, push, and wait for both Linux and macOS jobs plus submission-check to
  pass on the exact pushed SHA before claiming completion.
