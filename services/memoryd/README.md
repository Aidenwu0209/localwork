# memoryd

Ingestion orchestrator for DejaView (handbook §6.2). Runs on the Mac
(data-sovereignty side) and drives each captured frame through:

```
sentinel -> ocrd -> novelty gate -> perceive -> embed -> timeline store -> Honcho
```

## Frame safety and pipeline mode

The default pipeline is real: every frame first goes to the local privacy gate
at `SENTINEL_GATEWAY_URL`, then only a Sentinel-allowed frame reaches OCR,
storage, or the general `GATEWAY_URL` stages. A blocked frame writes audit
metadata only; its pixels never reach OCR or disk.

`MEMORYD_ALLOW_STUB_PIPELINE=false` is the default. Setting it to `true` is an
unsafe test-only switch: memoryd constructs stubs but rejects every
`/v1/ingest/frame` request with `503` and `pipeline_not_ready`, before reading
the uploaded image. Do not use it for capture or production-like runs.

## Run

```bash
# data layer must be up (make data-up from repo root)
uv run python -m memoryd            # serves 127.0.0.1:8090
```

Export configuration variables from the repository `.env.example` template
through your shell or process manager. The dedicated
`SENTINEL_GATEWAY_URL` must stay local to the data-sovereignty side;
`GATEWAY_URL` is used only after a frame is allowed. Application code uses
logical names only (`brain`/`perceive`/`sentinel`/`fast`/`embed`); physical
routing lives in `deploy/server/litellm.yaml`.

## Smoke test

```bash
curl -F "file=@some.png" \
     -F 'meta={"device_id":"dev","ts":"2026-07-21T10:00:00Z","app":"VS Code","window_title":"main.py","trigger":"change"}' \
     http://127.0.0.1:8090/v1/ingest/frame
# -> {"accepted":true,"event_id":1,"sentinel":{"decision":"allow",...}, ...}
```

## Response and heartbeat contracts

Frame responses have `processing_state` of `stored`, `merged`, or `blocked`.
`stored` returns a positive `event_id`; `merged` returns a positive
`merged_into`; `blocked` has `accepted: false` and means the image was
discarded before OCR and storage.

Capture clients send `POST /v1/capture/heartbeat` every 30 seconds, including
while their screen is locked. The body is metadata only: device ID,
timezone-aware client timestamp, and nonnegative stored/merged/blocked/failed
counters. No pixels, window titles, app names, or URLs are accepted.

`/v1/ingest/audio` and `/v1/ingest/doc` are intentionally unsupported today:
both return `501` with `unsupported_media` and list `frame` as the only
supported ingest type.

## Automatic Honcho projection

Every stored timeline event and its Honcho outbox record are committed in one
Postgres transaction. A background worker then projects a strict six-field
summary (`schema`, `event_id`, `occurred_at`, `app_context`, `activity`,
`topics`) into the local Honcho daily session. OCR text, window titles, URLs,
screenshots, and blocked frames are never part of the request.

Configure `HONCHO_URL`, `HONCHO_WORKSPACE`, `HONCHO_PEER`, and
`HONCHO_TIMEZONE` from `.env.example`. Delivery is leased, retried with bounded
backoff, and deduplicated using the exact `dejaview_event_id` metadata marker.
Operators can pause, resume, and inspect the queue without exposing payloads:

```bash
curl http://127.0.0.1:8090/v1/honcho/projection/status
curl -X POST http://127.0.0.1:8090/v1/honcho/projection/pause
curl -X POST http://127.0.0.1:8090/v1/honcho/projection/resume
```
