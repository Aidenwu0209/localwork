# P3.13 Privacy Gate and Capture Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the production screen-ingest path fail closed, truthfully report every frame outcome, preserve zero-disk capture, reject unsupported media honestly, and expose capture loss/freshness without leaking content.

**Architecture:** `memoryd` owns the privacy state machine and converts every Sentinel uncertainty or exception into a block verdict before any downstream call. The capture client consumes a structured frame outcome, mutates dedup state only after terminal privacy/storage outcomes, and reports metadata-only heartbeat counters. A small additive database migration records privacy reason codes; no raw blocked content is stored.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, httpx, psycopg 3, PostgreSQL 15, `unittest`/pytest, existing macOS capture client.

## Global Constraints

- Preserve the DejaView digital-memory narrative and the logical model names `brain`, `perceive`, `sentinel`, `fast`, and `embed`.
- In Topology A, raw frames use `SENTINEL_GATEWAY_URL=http://127.0.0.1:4000/v1`; Sentinel never falls back to Radeon.
- `brain`, `perceive`, `fast`, and `embed` model choices and accepted P3.1/P3.2/P3.4/P3.11 evidence remain unchanged.
- Every fast-track request keeps `chat_template_kwargs.enable_thinking=false`.
- Blocked frames write only audit metadata; they never reach OCR, novelty, perceive, embed, screenshot storage, timeline, or Honcho.
- The capture client never writes frame bytes or a retry queue to disk.
- Audio and document ingestion return HTTP 501 until a real stored or traceable pipeline exists.
- Tests and runtime evidence use synthetic fixtures only; do not read `.env`, credentials, real timeline rows, or real screenshots.
- Do not modify or stage `third_party/honcho`; Honcho remains pinned at `340175ad` and changes stay under `deploy/mac/honcho-patches/`.
- Commit author is exactly `Aidenwu0209 <1418557225@qq.com>`; commit messages have no `Co-authored-by`, `Generated-with`, or AI/agent trailer.

---

### Task 1: Strict Sentinel verdict model and parser

**Files:**
- Modify: `services/memoryd/src/memoryd/models.py`
- Modify: `services/memoryd/src/memoryd/stages.py`
- Create: `services/memoryd/tests/test_sentinel_fail_closed.py`
- Modify: `services/memoryd/scripts/test_parse_offline.py`

**Interfaces:**
- Produces: `SentinelReason = Literal["classified_normal", "sensitive_category", "malformed_output", "unknown_category", "low_confidence", "sentinel_unavailable", "test_stub"]`.
- Produces: `SentinelVerdict.reason: SentinelReason`.
- Produces: `_parse_sentinel_json(content: str, *, confidence_threshold: float = 0.70) -> SentinelVerdict`.
- Existing `GatewaySentinel.classify(image_bytes)` still returns `SentinelVerdict` and keeps its current logical model request.

- [ ] **Step 1: Write failing parser tests**

```python
class SentinelFailClosedTest(unittest.TestCase):
    def test_malformed_output_blocks(self):
        verdict = _parse_sentinel_json("not-json")
        self.assertEqual((verdict.decision, verdict.category, verdict.reason),
                         ("block", "normal", "malformed_output"))

    def test_missing_or_unknown_category_blocks(self):
        for raw in ('{"decision":"allow"}', '{"category":"medical_record","confidence":0.9}'):
            with self.subTest(raw=raw):
                verdict = _parse_sentinel_json(raw)
                self.assertEqual(verdict.decision, "block")
                self.assertEqual(verdict.reason, "unknown_category")

    def test_low_confidence_normal_blocks(self):
        verdict = _parse_sentinel_json('{"category":"normal","confidence":0.69}')
        self.assertEqual(verdict.reason, "low_confidence")
        self.assertEqual(verdict.decision, "block")

    def test_high_confidence_normal_allows(self):
        verdict = _parse_sentinel_json('{"category":"normal","confidence":0.70}')
        self.assertEqual(verdict.reason, "classified_normal")
        self.assertEqual(verdict.decision, "allow")

    def test_sensitive_category_blocks_even_below_threshold(self):
        verdict = _parse_sentinel_json('{"category":"banking_finance","confidence":0.2}')
        self.assertEqual(verdict.reason, "sensitive_category")
        self.assertEqual(verdict.decision, "block")
```

The production mutation these tests catch is any fallback that turns malformed,
unknown, missing, or low-confidence output into `allow`.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
uv run --with pytest --project services/memoryd pytest -q services/memoryd/tests/test_sentinel_fail_closed.py
```

Expected: import/field/assertion failures because `SentinelReason` and strict
fallback behavior do not exist.

- [ ] **Step 3: Implement the closed parser contract**

Add the `reason` field with no permissive default. `_parse_sentinel_json` must:

```python
if parsed is None:
    return SentinelVerdict(decision="block", category="normal",
                           confidence=0.0, reason="malformed_output")
if "category" not in parsed or normalized_category_is_unknown:
    return SentinelVerdict(decision="block", category="normal",
                           confidence=parsed_confidence_or_zero,
                           reason="unknown_category")
if category in _SENTINEL_SENSITIVE:
    return SentinelVerdict(decision="block", category=category,
                           confidence=confidence, reason="sensitive_category")
if confidence < confidence_threshold:
    return SentinelVerdict(decision="block", category="normal",
                           confidence=confidence, reason="low_confidence")
return SentinelVerdict(decision="allow", category="normal",
                       confidence=confidence, reason="classified_normal")
```

Keep only existing documented aliases (`banking` → `banking_finance`, etc.).
The normalizer must return an unknown signal instead of silently returning
`normal` for arbitrary text. Update every stub/test constructor with an explicit
reason; `StubSentinel` uses `test_stub`.

- [ ] **Step 4: Update the offline regression expectations**

Change the partial-JSON case from fail-open to `block/unknown_category`; add
malformed, unknown, and `normal@0.69` assertions. Keep category-to-decision
consistency tests for valid sensitive and valid normal categories.

- [ ] **Step 5: Verify GREEN and regression coverage**

Run:

```bash
uv run --with pytest --project services/memoryd pytest -q services/memoryd/tests/test_sentinel_fail_closed.py services/memoryd/scripts/test_parse_offline.py
```

Expected: all strict parser and prior perceive parsing tests pass.

---

### Task 2: Fail-closed production wiring and downstream short circuit

**Files:**
- Modify: `services/memoryd/src/memoryd/config.py`
- Modify: `services/memoryd/src/memoryd/server.py`
- Modify: `services/memoryd/src/memoryd/pipeline.py`
- Create: `services/memoryd/tests/test_pipeline_privacy.py`
- Modify: `services/memoryd/tests/test_metrics.py`

**Interfaces:**
- `Settings.sentinel_gateway_url: str` is loaded from `SENTINEL_GATEWAY_URL`, default `http://127.0.0.1:4000/v1`.
- `Settings.allow_stub_pipeline: bool` is loaded from `MEMORYD_ALLOW_STUB_PIPELINE`, default false.
- `FailClosedSentinel(reason="sentinel_unavailable")` replaces the production default stub.
- `Pipeline.ingest_frame()` converts exceptions from `sentinel.classify` into a `blocked` acknowledgement and audit reason; it never calls downstream stages.

- [ ] **Step 1: Write failing production-wiring tests**

```python
def test_default_pipeline_never_uses_stub_sentinel():
    settings = make_settings(sentinel_gateway_url="http://127.0.0.1:4000/v1")
    with patch("memoryd.server.GatewaySentinel") as gateway:
        pipeline = _default_pipeline(settings)
    gateway.assert_called_once_with(settings.sentinel_gateway_url)
    assert not isinstance(pipeline.sentinel, StubSentinel)

@pytest.mark.asyncio
async def test_sentinel_exception_audits_block_and_calls_nothing_downstream(tmp_path):
    stages = recording_pipeline(tmp_path, sentinel=ExplodingSentinel())
    ack = await stages.pipeline.ingest_frame(b"synthetic", frame_meta())
    assert ack.processing_state == "blocked"
    assert ack.sentinel.reason == "sentinel_unavailable"
    assert stages.audit_rows == [("block", "sentinel_unavailable")]
    assert stages.downstream_calls == []
    assert list(tmp_path.rglob("*.webp")) == []
```

The second test uses in-memory recording stage/store fakes that implement the
real protocols; it asserts real pipeline behavior, not mock call existence.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
uv run --with pytest --project services/memoryd pytest -q services/memoryd/tests/test_pipeline_privacy.py services/memoryd/tests/test_metrics.py
```

Expected: missing settings/FailClosedSentinel/processing_state and the current
default `StubSentinel` violates the wiring assertion.

- [ ] **Step 3: Add explicit settings and fail-closed wiring**

Add typed boolean parsing that accepts only `1,true,yes,on` and
`0,false,no,off`; invalid values raise `ValueError`. Normal production wiring
always uses `GatewaySentinel(settings.sentinel_gateway_url)`. An explicitly
enabled stub pipeline is labeled `test_stub`, and `/health` reports
`status=degraded`, `pipeline=stub`, and `accepting_frames=false` for it.

If the local Sentinel is unreachable at request time, `Pipeline` creates:

```python
SentinelVerdict(
    decision="block", category="normal", confidence=0.0,
    reason="sentinel_unavailable",
)
```

It writes the audit and returns normally with `processing_state="blocked"`.
It never includes exception text in the response or audit.

- [ ] **Step 4: Extend pipeline tests for every block class**

Use table cases for `sensitive_category`, `malformed_output`,
`unknown_category`, `low_confidence`, and `sentinel_unavailable`. For each,
assert one audit, zero downstream calls, zero timeline rows, and zero files.
Add an allow control proving OCR→novelty→perceive→embed→store still runs once.

- [ ] **Step 5: Verify GREEN**

Run the command from Step 2. Expected: all privacy/wiring/health tests pass.

---

### Task 3: Auditable reason migration and truthful ingest API

**Files:**
- Modify: `deploy/mac/timeline-init.sql`
- Create: `deploy/mac/migrations/20260802_p313_privacy_reason.sql`
- Modify: `services/memoryd/src/memoryd/storage.py`
- Modify: `services/memoryd/src/memoryd/models.py`
- Modify: `services/memoryd/src/memoryd/metrics.py`
- Modify: `services/memoryd/src/memoryd/server.py`
- Create: `services/memoryd/tests/test_ingest_contract.py`
- Create: `services/memoryd/tests/test_storage_contract.py`

**Interfaces:**
- `sentinel_audit.reason text NOT NULL` is constrained to the seven `SentinelReason` values.
- `ProcessingState = Literal["stored", "merged", "blocked"]`.
- `IngestAck.processing_state: ProcessingState` is required.
- Unsupported endpoints return `501` body `{"detail":{"code":"unsupported_media","stored":false,"supported":["frame"]}}` after validating metadata and without reading the uploaded body.
- Prometheus outcomes become `stored`, `merged`, and `blocked`; no ambiguous unclassified accept path remains.

- [ ] **Step 1: Write failing API and storage tests**

```python
def test_audio_and_doc_are_honestly_unsupported(client):
    cases = [("audio", valid_audio_meta()), ("doc", valid_doc_meta())]
    for kind, meta in cases:
        response = client.post(f"/v1/ingest/{kind}",
            files={"file": ("synthetic.bin", b"not-consumed", "application/octet-stream")},
            data={"meta": json.dumps(meta)})
        assert response.status_code == 501
        assert response.json()["detail"] == {
            "code": "unsupported_media", "stored": False, "supported": ["frame"]
        }

def test_audit_insert_contains_only_closed_metadata(recording_connection):
    store.write_sentinel_audit(ts=TS, device_id="synthetic-device", verdict=verdict)
    sql, params = recording_connection.executed[0]
    assert "reason" in sql
    assert params == (TS, "synthetic-device", "normal", "block", 0.0,
                      "malformed_output")
```

Add frame API cases that assert stored, merged, and blocked response bodies have
exactly the correct ids and processing state.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
uv run --with pytest --project services/memoryd pytest -q services/memoryd/tests/test_ingest_contract.py services/memoryd/tests/test_storage_contract.py services/memoryd/tests/test_metrics.py
```

Expected: current 202 fake successes, absent reason column, and absent
processing state fail.

- [ ] **Step 3: Add the idempotent SQL migration**

The migration is executable repeatedly:

```sql
ALTER TABLE sentinel_audit ADD COLUMN IF NOT EXISTS reason text;
UPDATE sentinel_audit SET reason = CASE
  WHEN decision = 'allow' THEN 'classified_normal'
  ELSE 'sensitive_category'
END WHERE reason IS NULL;
ALTER TABLE sentinel_audit ALTER COLUMN reason SET NOT NULL;
```

Add a named check constraint only if `pg_constraint` does not already contain
it. Mirror the final column and constraint in `timeline-init.sql` for clean
databases.

- [ ] **Step 4: Implement the API and metrics contract**

Set `processing_state` at every pipeline return. Return 501 before reading the
audio/doc `UploadFile`; validate metadata first so malformed metadata remains
422. `MemoryMetrics.observe_frame` switches only on `processing_state` and
exports exactly the three outcomes.

- [ ] **Step 5: Verify GREEN and SQL syntax**

Run the Step 2 tests. Then run:

```bash
docker compose -f deploy/mac/compose.data.yml config --quiet
```

Expected: tests pass and compose config exits zero. The live migration is
deferred to Task 5 after an explicit synthetic database target is confirmed.

---

### Task 4: Structured capture outcomes, heartbeat, and permission exit

**Files:**
- Modify: `clients/capture/src/capture/uploader.py`
- Modify: `clients/capture/src/capture/agent.py`
- Modify: `clients/capture/src/capture/config.py`
- Modify: `clients/capture/src/capture/__init__.py`
- Create: `clients/capture/tests/test_uploader.py`
- Create: `clients/capture/tests/test_agent_outcomes.py`
- Create: `clients/capture/tests/test_permission_exit.py`
- Modify: `services/memoryd/src/memoryd/server.py`
- Modify: `services/memoryd/src/memoryd/metrics.py`
- Create: `services/memoryd/tests/test_capture_heartbeat.py`

**Interfaces:**
- `UploadState = Literal["stored", "merged", "blocked", "failed"]`.
- `UploadOutcome(state, event_id=None, merged_into=None, error_code=None)` replaces the boolean return.
- `CaptureCounters` records stored/merged/blocked/failed and serializes metadata only.
- `CaptureConfig.heartbeat_endpoint` is `/v1/capture/heartbeat`.
- `POST /v1/capture/heartbeat` accepts device id, counters, and client timestamp; memoryd keeps only the latest per device in process memory and exports aggregate counters/freshness.
- `run_agent()` returns exit code `2` immediately when screen recording permission is absent; `capture.main()` raises `SystemExit(2)`.

- [ ] **Step 1: Write failing uploader outcome tests**

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("body,want", [
    ({"accepted": True, "processing_state": "stored", "event_id": 7}, "stored"),
    ({"accepted": True, "processing_state": "merged", "merged_into": 7}, "merged"),
    ({"accepted": False, "processing_state": "blocked"}, "blocked"),
])
async def test_upload_parses_terminal_outcome(body, want):
    outcome = await upload_frame(config, webp_bytes=b"synthetic", app="Demo",
        window_title="Synthetic", url=None, trigger="change",
        client=fixture_client(202, body))
    assert outcome.state == want

@pytest.mark.asyncio
async def test_invalid_2xx_body_is_failed():
    outcome = await upload_frame(..., client=fixture_client(202, {"accepted": True}))
    assert (outcome.state, outcome.error_code) == ("failed", "invalid_response")
```

Add transport, non-2xx, and invalid JSON cases. Assert returned errors contain
stable codes and no server response body.

- [ ] **Step 2: Write failing dedup and permission tests**

Extract a pure `should_commit_frame(outcome)` function. Assert true only for
stored/merged/blocked and false for failed. Test the loop-level outcome handler
updates hashes/timestamps/counters accordingly. Patch the permission check to
return denied and assert no client/session/capture function is created and exit
code is 2.

- [ ] **Step 3: Run and verify RED**

Run:

```bash
uv run --with pytest --project clients/capture pytest -q clients/capture/tests
```

Expected: tests cannot import `UploadOutcome`, counters, handler, or exit code.

- [ ] **Step 4: Implement structured outcomes and metadata-only heartbeat**

Use a frozen dataclass for outcomes. The agent keeps counters in memory. Every
30 seconds, and immediately after a recovered upload, POST:

```json
{
  "device_id": "synthetic-device",
  "client_ts": "2026-08-02T12:00:00+00:00",
  "stored": 1,
  "merged": 2,
  "blocked": 3,
  "failed": 4
}
```

The heartbeat contains no app, title, URL, OCR, filename, or pixels. Heartbeat
failure is debug logged and never changes frame dedup state. Memoryd validates
nonnegative counters and a bounded device id, tracks only the newest timestamp,
and exposes `dejaview_capture_frames_total{outcome=...}` plus
`dejaview_capture_last_heartbeat_unixtime`.

- [ ] **Step 5: Implement permission fail-fast**

Change the guidance text from “capture will keep running” to “capture will exit
without capturing; grant permission and relaunch.” Return before installing
observers or creating an HTTP client. Preserve a clean Ctrl-C exit code 0.

- [ ] **Step 6: Verify GREEN**

Run capture tests and:

```bash
uv run --with pytest --project services/memoryd pytest -q services/memoryd/tests/test_capture_heartbeat.py services/memoryd/tests/test_metrics.py
```

Expected: all outcome, dedup, heartbeat, and permission cases pass.

---

### Task 5: Integration, documentation, verification, and P3.13 acceptance

**Files:**
- Modify: `.env.example`
- Modify: `clients/capture/capture.yaml.example`
- Modify: `clients/capture/README.md`
- Modify: `services/memoryd/README.md`
- Modify: `README.md`
- Modify: `README.zh.md`
- Modify: `docs/verification-log.md`
- Modify: `STATUS.md`
- Modify: `TASKBOARD.json`

**Interfaces:**
- Documents `SENTINEL_GATEWAY_URL`, explicit test-stub opt-in, processing states,
  heartbeat, permission exit, and unsupported media.
- Produces the final P3.13 verify record and moves only P3.13 to `accept`.

- [ ] **Step 1: Run all focused suites**

```bash
uv run --with pytest --project services/memoryd pytest -q services/memoryd/tests services/memoryd/scripts/test_parse_offline.py
uv run --with pytest --project clients/capture pytest -q clients/capture/tests
```

Expected: zero failures. Record exact counts.

- [ ] **Step 2: Run the full first-party regression command set**

```bash
uv run --with pytest --project services/agentd pytest -q services/agentd/tests services/agentd/scripts/test_demo_p34.py
python3 -m unittest discover -s deploy/mac/monitoring -p 'test_*.py'
python3 -m unittest discover -s deploy/mac/llama-launch -p 'test_gateway_launcher.py'
python3 -m unittest discover -s deploy/server/llama-launch -p 'test_gateway_launcher.py'
python3 -m unittest discover -s deploy/server/monitoring -p 'test_rocm_smi_exporter.py'
python3 -m unittest deploy/mac/test_honcho_demo_compose.py
uv run --with pytest --project deploy/server/bench pytest -q deploy/server/bench/test_p31_bench.py
```

Expected: the prior 80 tests plus new P3.13 tests all pass.

- [ ] **Step 3: Apply the migration only to the synthetic/local DejaView database**

Resolve the running data compose container without reading `.env`, then pipe
`deploy/mac/migrations/20260802_p313_privacy_reason.sql` into `psql -d dejaview`.
Verify `sentinel_audit.reason` exists and the named check constraint is present.
Do not query existing row contents.

- [ ] **Step 4: Run synthetic privacy integration**

Start the local `sentinel` role on `:4000`, OCR, and memoryd with
`MEMORYD_REAL_PIPELINE=1`, `SENTINEL_GATEWAY_URL=http://127.0.0.1:4000/v1`,
and the normal configured compute gateway. Use only files under
`tests/assets/sentinel/`.

For one normal fixture, verify a `stored` or `merged` response. For one banking
fixture, verify `processing_state=blocked`, one audit row by the synthetic
device id, zero timeline row by that id, and zero screenshot file for that id.
Clean only the synthetic device rows/files created by this step.

- [ ] **Step 5: Verify unsupported and permission behavior manually**

POST synthetic audio/doc bytes and verify 501 bodies. Run the permission unit
fixture, not the user's real permission state, and verify exit 2 before capture.

- [ ] **Step 6: Update documentation and verification evidence**

Remove claims that current voice/doc ingestion works. Explain that Topology A
uses local Sentinel before any allowed Radeon request. Append a `[VERIFY]
P3.13` section with test counts, migration schema proof, synthetic allow/block
proof, metrics names, and exact commands.

- [ ] **Step 7: Accept the task and verify repository hygiene**

Set only P3.13 to `accept`; leave P3.14 false. Run:

```bash
git diff --check
git status --short
```

Expected: only intended first-party files plus the pre-existing dirty Honcho
submodule; the submodule is not staged.

- [ ] **Step 8: Commit, inspect, and push**

```bash
git add .env.example TASKBOARD.json STATUS.md README.md README.zh.md \
  clients/capture deploy/mac/migrations deploy/mac/timeline-init.sql \
  services/memoryd docs/verification-log.md \
  docs/superpowers/plans/2026-08-02-p313-privacy-capture-hardening.md
git commit -m "P3.13: harden privacy gate and capture outcomes"
git log -1 --format='%an <%ae>%n%B'
git push origin main
```

Expected author is exactly `Aidenwu0209 <1418557225@qq.com>` and the message
contains no trailer.
