"""Embedding helper for the query side (handbook §6.5).

Qwen3-Embedding is instruction-aware: queries must add the
`Instruct: 检索用户活动时间线\nQuery: ` prefix; ingest embeds plain text. agentd
only ever embeds queries (the user's question), so this always applies the prefix.
"""

from __future__ import annotations

import httpx

# Handbook §6.5 / §6.2 step 5: instruction prefix on the query side.
_INSTRUCT = "Instruct: 检索用户活动时间线\nQuery: "


def embed_query(gateway_url: str, query: str, *, timeout: float = 90.0) -> list[float]:
    """Embed a user query (with instruction prefix) via the gateway.

    Generous timeout + retry: in dev the gateway is often reached via an SSH
    tunnel (Mac -> AMD server) which can add latency jitter on top of the ~30ms
    GPU inference."""
    base = gateway_url.rstrip("/")
    if not base.endswith("/v1"):
        base = base.rstrip("/") + "/v1" if not base.endswith(":4000") else base + "/v1"
    last_exc = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(
                    f"{base}/embeddings",
                    json={"model": "embed", "input": f"{_INSTRUCT}{query}"},
                )
                r.raise_for_status()
                return r.json()["data"][0]["embedding"]
        except (httpx.ReadTimeout, httpx.ConnectError) as exc:
            last_exc = exc
            if attempt == 2:
                raise
            continue
    raise last_exc  # unreachable
