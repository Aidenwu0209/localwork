# DejaView Project Specification

**AMD AI DevMaster Hackathon — Track 2: Agentic AI**
**Release:** P3.17 submission package
**Date:** 3 August 2026
**Status:** Competition-grade, single-user mature MVP

## 1. Executive summary

DejaView is a local digital memory that continuously turns permitted screen activity into a searchable, evidence-backed timeline. It can answer questions about past work, open the exact supporting screenshot, compile a grounded daily report, and grow a privacy-minimized Honcho user model. A device-local Privacy Sentinel decides what must never be remembered before OCR, storage, embeddings, Honcho, or remote compute can see the frame.

The project is aligned to Track 2 because it is an agentic product, not a standalone model demo. DejaView perceives events, applies policy, creates and merges memories, retrieves evidence with tools, validates citations, updates a user model, and selects a verified compute path. AMD Radeon and ROCm are part of the implemented inference system: the accepted P3.1 campaign ran checksummed llama.cpp/HIP workloads on a Radeon PRO W7900D (`gfx1100`) and recorded quantization, speculative-decoding, concurrency, visual-path, and VRAM evidence.

This document distinguishes logical model roles from physical residency. DejaView defines five logical roles, but it does **not** claim that all five weight sets are simultaneously resident in every topology or operating state. It also does not generalize benchmark results beyond the measured cells.

## 2. Track 2 alignment

| Track 2 dimension | DejaView implementation | Evidence boundary |
|---|---|---|
| Functional completeness and application value | Per-window capture → privacy decision → OCR → novelty/merge → visual understanding → embedding → atomic timeline/outbox → Honcho → evidence-backed agent answer | Current service is screen-memory-first; audio and document ingestion return `501 unsupported_media` |
| Agentic behavior | Tool-calling retrieval, verified citations, daily-report stages, user-model query, classified Radeon-to-Local fallback, and safe evidence-insufficient responses | A model response is not accepted as evidence unless its event identifiers were returned by tools in the same request |
| AMD Radeon and ROCm | llama.cpp built with `GGML_HIP=ON` for `gfx1100`; full GPU offload was proven for the tested brain and perceive cells | Only the P3.1 matrix and historical small-role measurements are claimed; no unmeasured end-to-end performance benefit is asserted |
| Privacy and sovereignty | Raw frames are evaluated by a local Sentinel; durable memory and Honcho state remain on the sovereign data plane | An allowed transient inference request may reach the stateless Radeon compute plane; blocked pixels never do |

Track 2 is therefore expressed as a working agent loop with policy, memory, evidence, and recovery semantics. The optional cloud-model optimisation bonus is not claimed.

## 3. The problem — and why this is not ordinary RAG

People lose operational context across terminals, browsers, chats, documents, and application windows. Ordinary retrieval-augmented generation assumes that a safe, curated corpus already exists. Screen memory has a harder problem: it must decide whether an observation is allowed to become a document at all, preserve time and application provenance, merge near-duplicate frames, retain controlled visual evidence, and distinguish unavailable evidence from a plausible answer.

DejaView adds four system responsibilities that ordinary RAG does not provide by itself:

1. **Pre-ingest privacy policy.** The system can reject a frame before OCR, disk, embeddings, the timeline, Honcho, or remote inference.
2. **Temporal event construction.** A novelty gate merges repeated frames and creates traceable events rather than an undifferentiated chunk collection.
3. **Evidence-constrained agency.** The agent calls retrieval and profile tools, then validates every event citation against the exact tool results from that request.
4. **Longitudinal user understanding.** A strict, minimized outbox projects permitted activity metadata into Honcho without exporting OCR, URLs, window titles, screenshots, or blocked frames.

The product promise is therefore: *remember permitted digital activity, explain it with inspectable evidence, and refuse to invent or retain what policy did not allow.*

## 4. Sovereignty architecture

### 4.1 Daily split topology

```text
capture client (memory-only pixels)
        |
        v
sovereign data plane
  memoryd
    1. local Sentinel: malformed / uncertain / unavailable => BLOCK
    2. allow only: OCR -> novelty -> perceive -> embed
    3. atomic timeline event + minimized Honcho outbox
  PostgreSQL + pgvector | screenshots | audit | Honcho | agentd
        |
        | allowed transient inference requests only
        v
AMD compute plane (stateless)
  loopback gateway -> selected llama.cpp/HIP model process
```

In the implemented daily topology, durable data stays on the user's Mac: PostgreSQL, Redis, screenshots, audit records, the outbox, and Honcho state. A Windows sovereign data plane is an architecture target, not a shipped capture client in this release. The Radeon node is an inference plane, not a memory store. The public release does not include ephemeral host coordinates, SSH endpoints, private keys, credentials, or user data.

### 4.2 Single-box judge topology

The same logical services can run on one AMD host for judge reproduction. In that topology the data and compute planes share a physical machine, but the ordering and policy remain unchanged: Sentinel runs before OCR or durable storage, blocked frames terminate the pipeline, and gateways remain loopback-bound.

### 4.3 Compute routing

Agent requests use a Radeon-first router. Only classified retryable failures—such as connection failure, timeout, rate limiting, selected gateway errors, missing model, invalid JSON, or invalid product output—may cross to the verified Local Metal path. Authentication failures, bad caller requests, privacy-policy rejection, and unclassified errors do not cross backends. Both paths failing returns a sanitized failure, not a false success.

## 5. Five logical model roles

| Logical role | Responsibility | Physical family in the release | Residency statement |
|---|---|---|---|
| `brain` | Deep reasoning, planning, tool use, synthesis, and writing | ThinkingCap-Qwen3.6-27B GGUF | Started on demand; accepted P3.1 production policy uses Q6_K with MTP only when headroom is positively checked |
| `perceive` | Screen understanding and baseline Honcho derivation | Gemma 4 E4B GGUF with BF16 multimodal projector | May run on Radeon; also serves as the truthful physical model for Local Metal `brain` fallback |
| `sentinel` | Fast visual privacy classification | MiniCPM-V 4.6 GGUF with vision projector | Stays on the sovereign data plane in the daily split topology; it has no remote fallback for unfiltered pixels |
| `fast` | Low-cost text decisions such as novelty, merging, and tags | MiniCPM5-1B GGUF | Fast-track requests set `enable_thinking=false` |
| `embed` | 1024-dimensional event and query embeddings | Qwen3-Embedding-0.6B GGUF | Switching the physical embedding model requires a full index rebuild |

`ocrd` is deliberately outside the five-model hierarchy. It performs deterministic verbatim OCR using PP-OCRv6 or the RapidOCR development backend; it is not an LLM role.

The five rows above are a routing contract. They are **not** a claim of universal simultaneous residency. The accepted P3.1 brain/perceive campaign ran exclusive cells with other DejaView roles stopped. A separate historical capture measured four small roles together. Those measurements must not be added into an unmeasured co-tenant claim.

## 6. Privacy Sentinel and fail-closed ingestion

The Privacy Sentinel receives the raw frame first. Its output is accepted only when it matches the strict schema and passes the configured confidence threshold. The following outcomes all close the gate:

| Sentinel outcome | Decision | Downstream effect |
|---|---|---|
| Sensitive category | Block | Audit metadata only; no OCR, image, timeline event, embedding, Honcho payload, or remote request |
| Malformed or empty output | Block | Reason `malformed_output` |
| Unknown or missing category | Block | Reason `unknown_category` |
| Below confidence threshold | Block | Reason `low_confidence` |
| Timeout, connection error, or model failure | Block | Reason `sentinel_unavailable` |
| Valid normal category at or above threshold | Allow | Continue to OCR and subsequent stages |

Capture uses in-memory pixels, posts the frame, and discards its buffer. It advances deduplication state only after a terminal `stored`, `merged`, or `blocked` response. Transport or processing failure remains retryable and observable; it does not poison the deduplication state. Metadata-only heartbeats report freshness and cumulative outcomes without transmitting pixels.

## 7. Honcho integration

A stored timeline event and its Honcho outbox row are created in one database transaction. Honcho delivery is asynchronous: an outage changes the outbox state but cannot roll back the local memory.

The allowed projection has exactly six top-level fields:

```json
{
  "schema": 1,
  "event_id": 142,
  "occurred_at": "2026-08-02T14:32:00+08:00",
  "app_context": "Terminal",
  "activity": "Investigating a ROCm allocation failure",
  "topics": ["ROCm", "debugging"]
}
```

OCR text, URLs, window titles, verbatim passages, screenshot paths, highlight boxes, pixels, and arbitrary metadata are prohibited. The worker reparses persisted payloads as an untrusted boundary before network delivery. It uses local-date sessions, leases, bounded exponential retry, a maximum attempt count, and exact event-marker checks to avoid duplicates. Projection can be paused and resumed without deleting the timeline or outbox.

## 8. ROCm implementation and measured evidence

### 8.1 Implementation

The accepted compute build uses llama.cpp with `GGML_HIP=ON` and `AMDGPU_TARGETS=gfx1100`. The formal P3.1 campaign identified a Radeon PRO W7900D with 47.98 GiB VRAM and ROCm 7.2.1. The benchmark harness bound every server load to the assigned GPU, recorded ROCm/HIP logs, and proved full layer offload for the tested brain (`66/66`) and perceive (`43/43`) cells.

### 8.2 Formal P3.1 campaign

The authoritative run is `p31-w7900d-20260728T075653Z`:

- Brain: 3 quantizations (`Q8_0`, `Q6_K`, `Q4_K_M`) × MTP off/on × client concurrency 1/4/8 = **18 cells**.
- Perceive: server slots and client concurrency at 1/1, 2/2, and 4/4 = **3 cells**.
- Each cell used one excluded warm-up followed by three measured batches.
- Prompts and images were synthetic; temperature was zero; prompt cache was disabled; fast-style thinking was disabled.
- The narrow brain compliance gate and perceive visual-text gate passed every formal cell. These gates are not general reasoning or VLM-accuracy benchmarks.

Selected measured results:

| Cell | Aggregate output throughput | Request P95 | Resident / sampled peak VRAM | Interpretation |
|---|---:|---:|---:|---|
| Brain Q6_K, MTP off, c1 | 23.9 t/s | 9,654.7 ms | 23.49 / 23.50 GiB | Baseline for the fixed production quant |
| Brain Q6_K, MTP on, c1 | 41.6 t/s | 5,571.6 ms | 28.44 / 28.46 GiB | Measured 1.738× aggregate ratio in this cell |
| Brain Q6_K, MTP on, c8 | 77.1 t/s | 23,979.2 ms | 28.44 / 29.23 GiB | Measured batch-throughput cell; not an interactive-latency claim |
| Perceive Q8_0, slots/c4 | 50.0 t/s | 1,861.6 ms | 6.48 / 6.62 GiB | Maximum tested aggregate throughput; production default remains slots 2 |

MTP added 4.95 GiB of resident VRAM for every tested brain quant in this build. Production therefore enables Q6_K MTP only for an exclusive or positively headroom-checked brain session. Co-tenant performance, full Mac-to-tunnel latency, Sentinel compression, novelty routing cost, and EPYC OCR latency remain unmeasured or outside P3.1; no benefit is claimed for them.

## 9. Evidence, tests, and honest failure states

DejaView treats UI state, model registration, and process exit as leads—not completion proof. Readiness requires the intended request to succeed and the response shape to validate. The release exposes explicit failure states and avoids green status when evidence is unavailable.

Accepted verification includes:

- **P3.13 privacy hardening:** 127 first-party tests; an isolated synthetic normal frame created one timeline row and one screenshot, while a synthetic banking frame created zero timeline rows and zero new screenshots.
- **P3.14 routing:** 63 agentd/P3.4 tests; live synthetic probes completed once on Radeon, then on Local Metal after the exact verified compute link was stopped, and returned sanitized errors when both paths were unavailable.
- **P3.15 Honcho projection:** 59 memoryd tests plus six subtests; strict six-field payload, idempotent replay, pause, retry, and no timeline rollback on Honcho failure.
- **P3.16 product boundary:** agentd 101, memoryd 82 plus six subtests, capture 22, and product-focus 9; browser review at four viewport sizes, minimum 44 px interaction targets, and zero axe-core violations.
- **P3.17 release contract:** one offline first-party entry point, loopback-only gateways, ownership-aware lifecycle scripts, fail-closed readiness, pinned-and-patched Honcho setup, and public-document coordinate checks.

The synthetic test corpus contains generated application screens and fictional data. Release materials must not include real screenshots, live account identifiers, cloud coordinates, private keys, or credentials.

## 10. Reproduction

The supported release route is intentionally operator-oriented. Run from a clean clone on an authorized machine; do not expose inference gateways publicly.

```bash
git submodule update --init --recursive
make setup
make doctor
make test
```

For the local product stack:

```bash
make product-up
make product-status
make capture
```

Stop only DejaView-managed local services with:

```bash
make product-down
```

Downloaded model weights are not stored in Git. Use the repository bootstrap scripts and pinned checksums described in `docs/model-manifest.md`. The release examples use loopback endpoints and symbolic authorized-host placeholders; operators supply their own authorized transport configuration outside source control.

## 11. License and third-party notices

Project-owned application code and documentation are released under the root Apache License 2.0 file. The root `NOTICE` identifies the project and points to third-party notices. Model weights are not vendored and retain their upstream licenses.

The principal third-party components are documented in `docs/licenses.md`: ThinkingCap, MiniCPM, Qwen3-Embedding, Honcho, PaddleOCR, and Gemma 4 are listed under their applicable Apache-2.0 notices; llama.cpp and LiteLLM use MIT; PostgreSQL/pgvector and Redis retain their respective licenses. Gemma 4 is called out separately. OpenRecall is reference-only; AGPL code is not copied into DejaView.

Honcho is pinned to revision `340175ad` and receives two repository-owned patches through the release setup path. Unexpected submodule state fails closed rather than being silently accepted.

## 12. Limitations

- The current product ingests screen frames only. Audio and document endpoints intentionally return `501 unsupported_media`.
- The implemented capture client is macOS-only. Windows support is an architecture target and is not claimed as shipped.
- DejaView is a competition-grade, single-user mature MVP, not a commercial SaaS. Accounts, cross-device synchronization, billing, installer/updater, team administration, and mobile clients are out of scope.
- Five logical roles do not imply simultaneous residency. Operators must use current VRAM telemetry and the documented launch policy.
- P3.1 measures isolated cells and narrow compliance gates. It does not establish general model quality, energy efficiency, full-pipeline latency, or co-tenant speedups.
- OCR accuracy varies by backend and screen conditions. The synthetic corpus does not represent every real application, font, language boundary, or compression artifact.
- The daily split topology permits allowed transient inference payloads to reach an authorized stateless compute plane. It does not claim that all inference occurs on the data device.
- Hardware/live-cloud verification is separate from the offline test suite and must never be simulated as a passing live check.
- The official submission guidance recommends a **3–5 minute** demo. The current accepted video is **2:37** (157.2 seconds). The team will submit the current video and will **not** re-record it; the duration difference is disclosed rather than hidden.

## 13. Delivery checklist

- [x] English project specification in editable Markdown and Word formats.
- [x] Editable seven-slide Track 2 presentation with per-slide `[Sources]` notes.
- [x] Track 2 functional and ROCm alignment stated without optional cloud bonus claims.
- [x] Five logical roles documented without a simultaneous-residency claim.
- [x] Sentinel, fail-closed ingestion, citation validation, and Honcho minimization documented.
- [x] Formal ROCm evidence identified by checksummed run; unmeasured benefits excluded.
- [x] Safe clean-clone commands use loopback/symbolic configuration only.
- [x] Root license, notice, third-party licenses, and model manifest referenced.
- [x] Current 2:37 video retained; official 3–5 minute recommendation disclosed; no re-record planned.
- [ ] Human submitter confirms current contest portal, membership/eligibility, repository visibility, and final upload fields immediately before submission.
- [ ] Human operator runs the hardware/live-service acceptance checklist on the authorized final environment; offline CI does not substitute for it.

## 14. Source map

The specification is grounded in the following repository artifacts:

- Product and release contract: `README.md`, `STATUS.md`, `TASKBOARD.json`, and `docs/superpowers/specs/2026-08-02-mature-product-design.md`.
- Model and legal identity: `docs/model-manifest.md`, `docs/licenses.md`, root `LICENSE`, and root `NOTICE`.
- ROCm method and raw evidence: `docs/benchmarks.md` and `docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/`.
- Accepted implementation and demo proof: `docs/verification-log.md`; the memoryd pipeline and Honcho projection modules; `tests/release/test_release_contract.py`; and `docs/assets/demo/p34-video-manifest.json` with the accepted MP4.
