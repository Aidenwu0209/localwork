# agentd

DejaView's brain出口 (handbook §6.5). Speaks OpenAI-compatible
`/v1/chat/completions` (model=`dejaview`) to Open WebUI and resolves user
questions via tool-calling against:

- the **timeline** (search_timeline: semantic / exact / hybrid + time bounds)
- the **Honcho user model** (query_user_model — preferences, habits)
- the **knowledge base** (search_kb — imported documents)
- **screenshot evidence** (fetch_screenshot — image path + highlighted bbox)

## Status

- **M7.1 (this commit):** the four tools + their OpenAI function-calling specs +
  a dispatch router. Each tool is a plain callable, unit-tested against the live
  timeline DB / Honcho / gateway. `embed.py` adds the Qwen3 instruction prefix
  on the query side (handbook §6.5).
- **M7.2 (next):** the `/v1/chat/completions` endpoint that runs the brain
  (logical name `brain` at the gateway) in a tool-calling loop and formats
  answers with `[event#id HH:MM app]` citations.

## Run

```bash
# gateway (server or Mac dev) + Honcho + DB must be up
uv run python -m agentd     # M7.2 serves 127.0.0.1:8101
```

Config from `.env`: `GATEWAY_URL`, `TIMELINE_DB_URL`, `HONCHO_URL`, `DATA_ROOT`.
The brain is reached via the gateway's logical `brain` name (server: real 27B
on :8001; Mac dev: E4B dual-mapped).
