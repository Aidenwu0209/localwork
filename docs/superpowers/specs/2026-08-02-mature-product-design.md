# DejaView Competition-Grade Mature Product Design

## Objective

Turn the accepted DejaView competition prototype into a trustworthy single-user
desktop product: the real capture path must be privacy-safe, the memory and
Honcho loops must grow from actual use, every answer must expose verifiable
evidence, Radeon failures must degrade to a real Local Metal request, and a
stranger must be able to reproduce the supported product from a clean clone.

This is a competition-grade mature MVP, not a commercial SaaS release. Accounts,
cross-device sync, billing, an installer/updater, team administration, and mobile
clients are outside this design. Audio and document ingestion are not advertised
as working until their processing pipelines exist.

## Non-negotiable constraints

- The product remains a **local digital memory**, not a generic RAG chatbot.
- The award story remains Recall/Rewind failure → privacy-safe resurrection on
  Radeon + Honcho + privacy sentinel.
- The logical model names and roles remain `brain`, `perceive`, `sentinel`,
  `fast`, and `embed`; physical routing remains outside application prompts.
- The AMD node is stateless compute. PostgreSQL, Honcho state, screenshots,
  audit rows, and the outbox remain on the user's Mac/Windows data plane.
- `brain` remains ThinkingCap-27B on Radeon; `perceive` remains Gemma 4 E4B with
  BF16 mmproj; `sentinel` remains MiniCPM-V 4.6; `fast` remains MiniCPM5-1B;
  `embed` remains Qwen3-Embedding-0.6B; OCR remains PP-OCRv6/rapidocr.
- Fast-track requests always pass
  `chat_template_kwargs.enable_thinking=false`.
- Accepted P3.1, P3.2, P3.4, and P3.11 evidence is immutable and is not rerun.
- No secret, real PII, raw private timeline, or unfiltered blocked image enters
  Git. Tests and screenshots use synthetic fixtures only.

## Definition of mature for this release

The release is mature only when all of the following are true:

1. A malformed, uncertain, unavailable, or low-confidence Sentinel result can
   never allow a frame into OCR, storage, embeddings, Honcho, or the timeline.
2. Capture distinguishes stored, merged, privacy-blocked, unsupported, and
   lost outcomes. A failed upload is observable and does not poison dedup state.
3. A normal event automatically contributes a minimal, idempotent projection
   to Honcho without exporting OCR, URLs, window titles, image paths, or pixels.
4. User questions and daily reports make a real Radeon request first, use Local
   Metal only after a classified failure, and never label an unverified path as
   Radeon or fallback.
5. The daily product UI supports timeline browsing, open questions, validated
   citations, controlled evidence images, privacy audit summaries, Honcho status,
   and honest runtime status at desktop and narrow widths.
6. Unsupported media returns a precise unsupported response instead of a fake
   success.
7. A clean clone has one documented setup route, a diagnostic command, truthful
   readiness exits, a first-party test command, and CI using the same command.
8. The final acceptance matrix contains fresh unit, integration, browser,
   accessibility, failure-injection, clean-clone, and live-service evidence.

## Architecture

```text
capture (memory-only image)
  │
  ├─ frame POST + heartbeat/loss counters
  ▼
memoryd on the sovereign device
  ├─ local Sentinel gateway: malformed/unknown/unavailable => BLOCK
  ├─ block => audit reason only; no pixels/OCR/timeline/Honcho
  └─ allow => OCR → novelty → perceive → embed → atomic timeline+outbox
                                      │
                                      └─ Honcho worker → local Honcho session

agentd on the sovereign device
  ├─ product API + static product UI
  ├─ evidence guard (DB id + DATA_ROOT containment)
  ├─ citation validator
  └─ inference router
       ├─ Radeon gateway first (:14000 → remote :4000)
       └─ Local Metal gateway on classified failure (:4000)
```

Topology A keeps the raw privacy gate on the data device. It adds
`SENTINEL_GATEWAY_URL=http://127.0.0.1:4000/v1`; this endpoint must serve the
same logical `sentinel` model locally and has no remote fallback. Only a frame
that this local gate allows may be sent through the authenticated SSH compute
path for `perceive`; durable data still stays local. Sentinel audit records and
blocked pixels never leave the data plane. The product describes the boundary
accurately: the Radeon node may see an allowed transient inference request but
stores no user data or prompt logs. Topology B runs the same logical Sentinel
on the one AMD machine because the data and compute planes are the same host.

## Workstream 1 — privacy and reliable capture (P3.13)

### Sentinel contract

`SentinelVerdict` gains a machine-readable reason and explicit confidence. The
closed outcomes are:

| Result | Decision | Reason examples |
| --- | --- | --- |
| Valid sensitive category | block | `sensitive_category` |
| Valid normal category at/above threshold | allow | `classified_normal` |
| Malformed or empty output | block | `malformed_output` |
| Unknown or missing category | block | `unknown_category` |
| Confidence below threshold | block | `low_confidence` |
| Timeout, connection, or model error | block | `sentinel_unavailable` |

`MEMORYD_REAL_PIPELINE=0` is allowed only in explicit test mode. Normal startup
must not silently install `StubSentinel`; it fails readiness or uses a
fail-closed sentinel that cannot accept frames. In Topology A, Sentinel must use
`SENTINEL_GATEWAY_URL` and must never follow the Radeon/Local compute router.

Every block writes only timestamp, device id, normalized category, decision,
confidence, and reason. It never writes image bytes, OCR, URL, title, verbatim,
embedding, or a timeline event. Tests assert that downstream stage call counts
remain zero.

### Frame outcome contract

The frame response retains `accepted` for compatibility and adds
`processing_state`:

- `stored`: `accepted=true`, with `event_id`.
- `merged`: `accepted=true`, with `merged_into`.
- `blocked`: `accepted=false`, with Sentinel metadata and no event id.
- `failed`: non-2xx with a stable error code.

Capture parses the body instead of treating every 2xx as storage success. It
updates the per-window hash only after `stored`, `merged`, or `blocked`; it does
not update the hash after transport or processing failure, so a later poll can
retry the current screen without caching pixels to disk. Counters track each
outcome and dropped frame. A metadata-only heartbeat reports cumulative counters
and the last successful upload to memoryd; the product status API labels a stale
heartbeat as capture unavailable.

Screen-recording permission failure exits nonzero after displaying the exact
macOS instruction instead of running an endless black-frame loop.

### Unsupported media

`/v1/ingest/audio` and `/v1/ingest/doc` return HTTP 501 with code
`unsupported_media`, `stored=false`, and `supported=["frame"]`. README copy calls
the current release screen-memory-first and lists voice/document ingestion as a
future capability. No endpoint may consume bytes and return `accepted=true`
without storage or a traceable job.

## Workstream 2 — real compute routing (P3.14)

### Shared routing semantics

Application settings expose two compute paths:

- `RADEON_GATEWAY_URL`, compatible with the existing `GATEWAY_URL`, normally
  `http://127.0.0.1:14000/v1`.
- `LOCAL_GATEWAY_URL`, normally `http://127.0.0.1:4000/v1`.

An inference result records `backend`, `physical_model`, `logical_model`,
`degraded`, latency, and a stable reason code. Registration in `/v1/models` is
not success; the router requires the requested inference call and validates its
response shape.

Classified retryable failures are connection error, timeout, 429, 502–504,
missing model, invalid JSON, or invalid product output. Authentication errors,
bad caller requests, and privacy-policy rejection are not retried on another
backend. A small per-role circuit breaker suppresses repeated remote attempts
during a cooldown and probes it again after the cooldown.

For the local `brain` alias, the UI and response metadata say that Local Metal
uses the Gemma `perceive` fallback; it is never reported as ThinkingCap-27B.
Embedding fallback is accepted only when the response is 1024-dimensional and
the same logical `embed` contract is present.

### Agent behavior and citations

Both ordinary questions and daily reports use the same router. The tool loop
collects the event ids returned by tools. Before returning a memory claim,
agentd parses every `[event#id HH:MM app]` marker and verifies that its id was
actually returned in this request. A first invalid answer receives one
correction attempt. A second invalid answer becomes a safe evidence-insufficient
response, never an invented citation.

The OpenAI-compatible response keeps `choices` and adds top-level `dejaview`:

```json
{
  "backend": "radeon|local_metal",
  "degraded": false,
  "reason": "primary_ok|remote_timeout|remote_invalid_output",
  "citations": [{"event_id": 142, "label": "14:32 Terminal"}]
}
```

If both paths fail, agentd returns 503 with both sanitized failure reasons. It
does not claim a local fallback merely because a health probe once succeeded.

## Workstream 3 — automatic Honcho projection (P3.15)

### Atomic outbox

The timeline database gains `honcho_outbox` with a unique `event_id`, minimized
JSON payload, daily `session_id`, state (`pending`, `sending`, `sent`, `failed`),
attempt count, next-attempt timestamp, sanitized last error, and timestamps.
Creating a new timeline event and its outbox row occurs in one database
transaction. Merge and block outcomes do not enqueue another projection.

The only allowed projection fields are:

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

OCR text, URL, window title, verbatim, screenshot path, highlight boxes, raw
pixels, and arbitrary metadata are prohibited. Tests inspect the actual HTTP
request body and assert these values cannot appear.

### Worker and controls

A memoryd lifespan worker polls pending rows, idempotently ensures the
`dejaview` workspace, `owner` peer, and local-date session, then posts one
message whose peer is `owner`. Success marks the row sent. Failure schedules
bounded exponential retry and never rolls back or blocks the timeline event.
Rows stuck in `sending` are returned to pending after a lease timeout.

The product API exposes projection enabled/paused state, pending/failed counts,
last success, and the covered session range. Pause stops delivery without
deleting timeline or outbox data. Resume reuses the same idempotency keys.

## Workstream 4 — daily product UI (P3.16)

### Product shell

The accepted six-act files remain intact as competition evidence. The daily UI
is a new agentd-served static application under `agentd/web`, implemented with
semantic HTML, modern CSS, and small ES modules. No React toolchain or new
identity system is introduced.

Desktop uses three coordinated regions:

1. Timeline and filters on the left.
2. Ask/answer workspace in the center.
3. Evidence drawer on the right.

At narrow widths the regions become one navigable stack. The top bar always
shows data-sovereignty state, compute path, capture freshness, and last check.
Secondary views expose privacy audit, Honcho profile status, and full component
health. Demo-only mutation and tunnel-disconnect actions remain in the separate
six-act stage and require explicit operator confirmation.

### Product API

| Endpoint | Contract |
| --- | --- |
| `GET /api/status` | local components, capture freshness, current compute path, reason, last success |
| `GET /api/timeline` | cursor pagination plus date/app/query filters; display-safe fields only |
| `POST /api/ask` | structured answer, validated citations, backend metadata |
| `GET /api/evidence/{id}` | event metadata, highlight boxes, controlled image URL |
| `GET /api/evidence/{id}/image` | image only after DB lookup and DATA_ROOT containment |
| `GET /api/privacy/summary` | counts, categories, decisions, zero-pixel guarantee; never blocked images |
| `GET /api/profile/status` | enabled/paused, queue counts, session range, last success |
| `POST /api/profile/query` | Honcho question with namespace and provenance metadata |
| `POST /api/profile/pause|resume` | explicit local control action |

All database lists are bounded and cursor-paginated. Evidence paths never leave
the API. A symlink or path outside `DATA_ROOT/screenshots` is rejected. An id
that belongs to no visible event returns 404. The frontend never receives a raw
filesystem path.

### Interaction and accessibility

- A user can filter the timeline, ask a question, open a citation, inspect OCR
  highlighting, and return without losing the query.
- Dynamic answers and status changes use `aria-live`; errors use `role=alert`.
- Every control has a visible label, `:focus-visible`, and keyboard operation;
  Escape closes the evidence drawer and returns focus to the citation.
- `prefers-reduced-motion` disables decorative transitions.
- Body text is at least 14px, status is never communicated by color alone, and
  no-data is gray/red rather than green.
- Layout tests cover 1440px, 1024px, 390px, and browser zoom at 200%.

## Workstream 5 — clean reproduction and release (P3.17)

The root workflow becomes:

```text
git clone --recurse-submodules ...
make setup
make doctor
make data-up
make product-up
make test
```

`make setup` initializes the pinned Honcho submodule and applies only
`deploy/mac/honcho-patches/` idempotently. It never stages the dirty submodule.
`make doctor` is read-only and reports Docker, `uv`, Python, submodule commit,
patch state, required ports, model files, database services, gateways, and
screen-recording permission without reading `.env` values.

Launch commands resolve the repository root before changing directories, put
long-running services in managed PID files, reject duplicate listeners, and
return nonzero unless readiness endpoints pass. `server-stack.sh up` checks
both process identity and real inference readiness rather than returning zero
after a failed role.

README and README.zh describe the same supported feature set and current
endpoint discovery; they do not retain historical SSH ports as current. Root
first-party code uses an Apache-2.0 `LICENSE` plus a `NOTICE` that keeps Gemma
and other third-party terms separate. A GitHub Actions workflow is included. CI
runs the same first-party unit and contract command as `make test`;
hardware/live tests are clearly separated and never faked in CI.

## Verification strategy (P3.18)

### Automated gates

1. Memoryd unit tests: malformed/unknown/low-confidence/unavailable Sentinel,
   block short-circuit, response states, unsupported media, atomic outbox, retry
   and privacy-minimized payload.
2. Capture unit tests: body parsing, dedup mutation only on terminal outcomes,
   transport loss counters, heartbeat, and permission failure.
3. Router tests: primary success, remote failure/local success, both failure,
   non-retryable failure, invalid output, circuit cooldown/recovery, physical
   backend metadata, thinking-disabled fast path, and embedding dimension.
4. Agent tests: tool loop, citation allowlist, corrective retry, safe second
   failure, evidence path containment, pagination, and privacy summary.
5. Browser tests: timeline→ask→citation→evidence at the required viewports,
   offline/degraded states, keyboard flow, reduced motion, and automated axe
   checks with no serious/critical violations.
6. Release tests: clean temporary clone, submodule/patch idempotency, compose
   config, doctor exit codes, launcher failure propagation, README commands,
   root license/notice, and CI/test-command parity.

### Live synthetic gates

- With synthetic data only, run one allow frame and prove an event, screenshot,
  embedding, and exactly one Honcho outbox projection exist.
- Run one sensitive frame and prove one audit row, zero event, zero screenshot,
  zero outbox row, and zero downstream stage calls.
- Ask a factual question and prove every citation id exists, its controlled
  image endpoint loads, and the answer reports the actual compute backend.
- Break the verified Radeon tunnel without disabling Wi-Fi; prove the ordinary
  product question (not only the demo script) completes on Local Metal and is
  labeled degraded. With both gateways stopped, prove the UI reports offline.
- Resume Radeon, wait for circuit recovery, and prove the next request is
  labeled Radeon.
- Re-run all first-party tests, `git diff --check`, secret/PII scans, commit
  author/trailer checks, and the current submission checklist.

ROCm performance claims continue to use the accepted checksummed P3.1 evidence.
If the current AMD instance is unavailable, only the live routing gate remains
unverified; no historical benchmark is rerun or relabeled as current health.

## Requirement-to-task matrix

| Requirement | Task | Completion evidence |
| --- | --- | --- |
| Design and acceptance boundary | P3.12 | this design, self-review, taskboard |
| Privacy fail-closed + capture truth | P3.13 | unit/integration tests and synthetic DB/filesystem proof |
| Real compute fallback + citations | P3.14 | router/agent fault-injection tests and product response metadata |
| Timeline grows Honcho automatically | P3.15 | outbox tests and live synthetic session proof |
| Usable daily product page | P3.16 | API contract, browser, responsive and accessibility evidence |
| Reproducible release | P3.17 | clean-clone/doctor/launcher/CI evidence |
| Whole-product acceptance | P3.18 | fresh matrix in `docs/verification-log.md` |

## Explicitly excluded from completion claims

This release does not claim production-ready voice transcription, document
indexing, Windows capture, account authentication, cloud sync, an app-store
installer, or enterprise multi-tenancy. Unsupported paths are visible and
honest; they do not return success. These exclusions do not weaken the core
screen-memory product because every advertised daily workflow is implemented
and verified end to end.
