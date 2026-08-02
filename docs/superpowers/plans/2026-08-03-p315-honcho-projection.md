# P3.15 Timeline to Honcho Projection Implementation Plan

**Goal:** Automatically turn each newly stored, privacy-allowed timeline event into a minimal, idempotent Honcho memory projection without coupling capture success to Honcho availability.

**Architecture:** `TimelineStore.insert_event` writes the timeline row and an outbox row in one Postgres transaction. A memoryd-owned worker leases pending rows, creates the daily Honcho namespace/session, sends a strictly allowlisted projection, and marks delivery state with bounded retry. Pause/resume and status APIs manipulate local outbox control only; they never delete timeline data.

**Privacy invariant:** The serialized Honcho request may contain only schema, event id, occurred-at, app context, activity, and topics. OCR text, URL, window title, verbatim, screenshot paths, boxes, pixels, arbitrary metadata, database credentials, and raw upstream errors are forbidden.

---

## Task 1: Define schema and atomic storage contract with failing tests

**Files:**
- Modify: `deploy/mac/timeline-init.sql`
- Create: `deploy/mac/migrations/20260803_p315_honcho_outbox.sql`
- Modify: `services/memoryd/src/memoryd/storage.py`
- Create: `services/memoryd/tests/test_honcho_outbox_storage.py`

1. Add an idempotent `honcho_outbox` schema with unique event id, minimized JSON payload, daily session id, delivery state, attempt/lease/retry fields, sanitized error, and timestamps.
2. Add projection-control storage with enabled/paused state.
3. Add RED tests proving a stored event and outbox row share one transaction and rollback together.
4. Add tests proving merge and block paths do not enqueue.
5. Build projection payload from explicit fields only and assert forbidden values are absent.
6. Implement storage methods and run tests to GREEN.

## Task 2: Implement idempotent worker and failure isolation

**Files:**
- Modify: `services/memoryd/src/memoryd/config.py`
- Create: `services/memoryd/src/memoryd/honcho_projection.py`
- Create: `services/memoryd/tests/test_honcho_projection.py`

1. Add sanitized Honcho URL, polling, lease, retry, batch, timezone, workspace, and peer settings.
2. Lease rows with concurrency-safe Postgres semantics and recover expired `sending` leases.
3. Idempotently ensure workspace, peer, and local-date session.
4. Send exactly one minimized message per event with stable event idempotency metadata.
5. Mark success once; replay never duplicates a sent event.
6. On network/upstream failure, preserve the timeline row, sanitize the error, and schedule bounded exponential retry.
7. Test single delivery, replay, concurrency lease, retry, lease recovery, and request-body privacy.

## Task 3: Wire lifecycle, pause/resume, and status

**Files:**
- Modify: `services/memoryd/src/memoryd/server.py`
- Modify: `services/memoryd/src/memoryd/metrics.py`
- Create: `services/memoryd/tests/test_honcho_projection_api.py`

1. Start/stop the worker using FastAPI lifespan without leaking tasks.
2. Add `GET /v1/profile/status` with enabled/paused, pending/failed, last success, and covered session range.
3. Add explicit `POST /v1/profile/pause` and `/resume`; make them idempotent.
4. Report queue observability in metrics without payload content.
5. Prove pause stops delivery but not ingestion or enqueue; resume reuses existing rows.
6. Run API and memoryd regressions to GREEN.

## Task 4: Live synthetic verification and acceptance

**Files:**
- Modify: `README.md`
- Modify: `STATUS.md`
- Modify: `TASKBOARD.json`
- Modify: `docs/verification-log.md`

1. Apply the migration twice and prove idempotency.
2. Ingest one synthetic allowed event and observe one correct daily-session delivery.
3. Replay the worker and prove no duplicate.
4. Simulate Honcho failure and prove the timeline remains stored while outbox retries.
5. Inspect only the synthetic outgoing body and prove forbidden fields are absent.
6. Delete synthetic test rows/artifacts, run full first-party tests, append `[VERIFY] P3.15`, accept the task, commit with the required author, verify no forbidden trailers, and push.

