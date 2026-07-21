# memoryd

Ingestion orchestrator for DejaView (handbook §6.2). Runs on the Mac
(data-sovereignty side) and drives each captured frame through:

```
sentinel -> ocrd -> novelty gate -> perceive -> embed -> timeline store -> Honcho
```

## Status

- **M3.2 (this commit):** FastAPI skeleton with the three ingest endpoints and a
  pluggable pipeline. Every stage is a Protocol backed by an obvious stub, so
  the ingest path runs end to end against the real Postgres store with zero GPU.
  A blocked sentinel decision writes only to `sentinel_audit` — the image never
  reaches OCR or disk (privacy invariant from handbook §6.2.1).
- **M3.3:** real `embed` + search endpoints (semantic / pg_trgm / time).
- **M3.4 / M5.1:** swap stubs for gateway-backed sentinel/perceive/embed and the
  ocrd microservice. No orchestrator changes — construct `Pipeline` explicitly.

## Run

```bash
# data layer must be up (make data-up from repo root)
uv run python -m memoryd            # serves 127.0.0.1:8090
```

Config comes from `.env` (copy `.env.example` from repo root). Logical model
names only (`brain`/`perceive`/`sentinel`/`fast`/`embed`); physical routing
lives in `deploy/server/litellm.yaml`.

## Smoke test

```bash
curl -F "file=@some.png" \
     -F 'meta={"device_id":"dev","ts":"2026-07-21T10:00:00Z","app":"VS Code","window_title":"main.py","trigger":"change"}' \
     http://127.0.0.1:8090/v1/ingest/frame
# -> {"accepted":true,"event_id":1,"sentinel":{"decision":"allow",...}, ...}
```
