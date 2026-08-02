# agentd

DejaView's brain出口 (handbook §6.5). Speaks OpenAI-compatible
`/v1/chat/completions` (model=`dejaview`) to Open WebUI and resolves user
questions via tool-calling against:

- the **timeline** (search_timeline: semantic / exact / hybrid + time bounds)
- the **Honcho user model** (query_user_model — preferences, habits)
- the **knowledge base** (search_kb — imported documents)
- **screenshot evidence** (fetch_screenshot — opaque event reference + text-free bbox)

## Runtime contract

`/v1/chat/completions` runs the brain tool loop and returns OpenAI-compatible
`choices` plus a top-level `dejaview` object with the backend that actually
completed the answer. Every memory citation is checked against event ids,
timestamps, and apps returned by this request's tools. One invalid answer gets
one correction attempt; a second invalid answer fails closed with an
evidence-insufficient response.

The same compute router is used by ordinary questions, semantic embeddings,
and the daily-report agents. It tries Radeon first and uses Local Metal only
for classified availability or invalid-product failures. Local `brain`
physically uses the `perceive` model and is reported as such. Both paths failing
returns a sanitized 503; a model registration or health probe alone never
counts as success.

## Run

```bash
# Radeon tunnel + Local Metal gateway + Honcho + DB
RADEON_GATEWAY_URL=http://127.0.0.1:14000/v1 \
LOCAL_GATEWAY_URL=http://127.0.0.1:4000/v1 \
uv run python -m agentd     # serves 127.0.0.1:8101
```

Open `http://127.0.0.1:8101/` for the default daily product experience. It
shows the bounded timeline, grounded answers, capability-protected evidence,
privacy decisions, profile projection, and truthful system state. The six-act
competition stage remains isolated at its existing demo entry point.

Browser mutations require same-origin JSON plus the local CSRF session. Evidence
images use short-lived event capabilities and descriptor-relative, no-follow
file access; raw screenshot paths, OCR text, window titles, and local filesystem
coordinates are never returned by the product or health APIs.

Config from `.env`: `RADEON_GATEWAY_URL`, `LOCAL_GATEWAY_URL`, legacy
`GATEWAY_URL`, `TIMELINE_DB_URL`, `HONCHO_URL`, and `DATA_ROOT`. The Radeon
`brain` is ThinkingCap-27B; the Local Metal fallback is the physical
`perceive` model and is never labeled as ThinkingCap.
