"""The four agentd tools (handbook §6.5). Each is a plain Python callable that
the tool-calling loop (server.py) dispatches to, plus its OpenAI function spec.

  search_timeline   — semantic / exact / hybrid over timeline_events (the
                      everyday "what was I doing around X" tool). Query side
                      adds the Qwen3 instruction prefix; error codes / PR / URL
                      queries route to exact for direct hits.
  query_user_model  — Honcho dialectic ("based on what you know about me...").
  search_kb         — semantic over kb_chunks (documents the user fed in).
  fetch_screenshot  — returns the screenshot path for an event + highlights the
                      hit text's bbox (drawn from ocr_blocks) as evidence.

All tools return JSON-serialisable dicts; the brain formats them into the final
answer with [event#id HH:MM app] citations (handbook §6.5 answer discipline).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Optional

import httpx
import psycopg

from agentd.config import Settings
from agentd.embed import embed_query

SearchMode = Literal["hybrid", "semantic", "exact"]

# --- OpenAI function-calling schemas ----------------------------------------
# The brain sees these as `tools` in its /v1/chat/completions request.

SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_timeline",
            "description": (
                "Search the user's captured activity timeline. Use mode='exact' "
                "for error codes / PR numbers / URLs (direct substring hits), "
                "'semantic' for conceptual queries ('database work last week'), "
                "'hybrid' (default) to merge both. Optional time_from/time_to "
                "bound the search (ISO-8601). Returns ranked events with an "
                "excerpt of their OCR text and a relevance score."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "mode": {
                        "type": "string",
                        "enum": ["hybrid", "semantic", "exact"],
                        "default": "hybrid",
                    },
                    "k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
                    "time_from": {
                        "type": "string",
                        "description": "ISO-8601 lower bound (inclusive), optional",
                    },
                    "time_to": {
                        "type": "string",
                        "description": "ISO-8601 upper bound (inclusive), optional",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_user_model",
            "description": (
                "Ask the Honcho user-psychology model a question about the user "
                "('based on what you know about me, which approach would I prefer?'). "
                "Use this for preferences, habits, working style — not for factual "
                "event lookups (use search_timeline for those)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question about the user"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_kb",
            "description": (
                "Semantic search over the user's knowledge base (documents they "
                "fed in via /v1/ingest/doc). Use this when the question is about "
                "reference material, manuals, or imported repos rather than the "
                "user's own activity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_screenshot",
            "description": (
                "Fetch the screenshot evidence for a timeline event by id, with "
                "an optional highlight_text to outline (the matching OCR block's "
                "bbox). Returns the image path under DATA_ROOT and the bbox(es) "
                "of the highlighted text. Use this to ground a claim with visual "
                "evidence (the UI renders these as clickable citations)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "integer", "description": "timeline_events.id"},
                    "highlight_text": {
                        "type": "string",
                        "description": "optional substring to locate in ocr_blocks and outline",
                    },
                },
                "required": ["event_id"],
            },
        },
    },
]

NAMES = {spec["function"]["name"] for spec in SPECS}


# --- Implementations --------------------------------------------------------


def search_timeline(
    settings: Settings,
    *,
    query: str,
    mode: SearchMode = "hybrid",
    k: int = 5,
    time_from: Optional[str] = None,
    time_to: Optional[str] = None,
) -> dict[str, Any]:
    """Three-mode timeline search. Mirrors memoryd.search but standalone (agentd
    doesn't import memoryd — it's a separate service). Embeds the query with the
    Qwen3 instruction prefix on the semantic/hybrid paths."""
    k = max(1, min(k, 20))
    query_vec: Optional[list[float]] = None
    if mode in ("semantic", "hybrid"):
        query_vec = embed_query(settings.gateway_url, query)

    time_clause, time_params = _time_clause(time_from, time_to)
    hits: list[dict] = []

    if mode == "semantic":
        hits = _semantic(settings.timeline_db_url, query_vec, k, time_clause, time_params)
    elif mode == "exact":
        hits = _exact(settings.timeline_db_url, query, k, time_clause, time_params)
    else:  # hybrid
        sem = _semantic(settings.timeline_db_url, query_vec, k, time_clause, time_params) if query_vec else []
        ex = _exact(settings.timeline_db_url, query, k, time_clause, time_params)
        hits = _blend(sem, ex, k)

    return {"query": query, "mode": mode, "k": k, "count": len(hits), "hits": hits}


def query_user_model(settings: Settings, *, question: str) -> dict[str, Any]:
    """Honcho dialectic. Uses the default dejaview/owner peer; session is
    optional (Honcho will scope to all sessions if omitted)."""
    base = settings.honcho_url.rstrip("/")
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        # Ensure workspace/peer exist (idempotent; Honcho v3 wants client ids).
        for path, body in [
            ("/v3/workspaces", {"id": "dejaview", "name": "dejaview"}),
            ("/v3/workspaces/dejaview/peers", {"id": "owner", "name": "owner"}),
        ]:
            try:
                client.post(f"{base}{path}", json=body)
            except httpx.HTTPStatusError:
                pass  # 409 already exists is fine
        r = client.post(
            f"{base}/v3/workspaces/dejaview/peers/owner/chat",
            json={"query": question},
        )
        r.raise_for_status()
        d = r.json()
    answer = d.get("content") or d.get("message") or d.get("response") or ""
    return {"question": question, "answer": str(answer)[:2000]}


def search_kb(settings: Settings, *, query: str, k: int = 5) -> dict[str, Any]:
    """Semantic search over kb_chunks (imported documents)."""
    k = max(1, min(k, 20))
    vec = embed_query(settings.gateway_url, query)
    sql = """
        SELECT id, doc_id, source_path, chunk,
               (embedding <=> %s::vector) AS distance
        FROM kb_chunks
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    with psycopg.connect(settings.timeline_db_url) as conn, conn.cursor() as cur:
        cur.execute(sql, [vec, vec, k])
        rows = cur.fetchall()
    hits = [
        {
            "id": r[0],
            "doc_id": r[1],
            "source_path": r[2],
            "chunk_excerpt": (r[3] or "")[:300],
            "score": round(1.0 - (r[4] or 2.0) / 2.0, 4),
        }
        for r in rows
    ]
    return {"query": query, "k": k, "count": len(hits), "hits": hits}


def fetch_screenshot(
    settings: Settings, *, event_id: int, highlight_text: Optional[str] = None
) -> dict[str, Any]:
    """Look up an event's screenshot path + locate highlight_text in its
    ocr_blocks (returns the bbox(es) so the UI can outline them)."""
    with psycopg.connect(settings.timeline_db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, ts, app, window_title, screenshot_path, ocr_blocks "
            "FROM timeline_events WHERE id = %s",
            (event_id,),
        )
        row = cur.fetchone()
    if row is None:
        return {"event_id": event_id, "found": False}
    eid, ts, app, title, screenshot_path, ocr_blocks = row
    blocks = ocr_blocks or []
    highlights: list[dict] = []
    if highlight_text:
        needle = highlight_text.lower()
        for b in blocks:
            txt = (b.get("text") or "")
            if needle and needle in txt.lower():
                highlights.append({"text": txt, "bbox": b.get("bbox", [])})
    return {
        "event_id": eid,
        "found": True,
        "ts": ts.isoformat() if ts else None,
        "app": app,
        "window_title": title,
        "screenshot_path": screenshot_path,
        "screenshot_exists": bool(screenshot_path and Path(screenshot_path).exists()),
        "highlights": highlights,
    }


# --- SQL helpers (mirror memoryd.search) -----------------------------------


def _time_clause(time_from: Optional[str], time_to: Optional[str]) -> tuple[str, list]:
    parts: list[str] = []
    params: list = []
    if time_from:
        parts.append("ts >= %s")
        params.append(time_from)
    if time_to:
        parts.append("ts <= %s")
        params.append(time_to)
    if not parts:
        return "", []
    return "AND " + " AND ".join(parts), params


def _semantic(dsn, vec, k, time_clause, time_params) -> list[dict]:
    sql = f"""
        SELECT id, ts, app, window_title, activity, ocr_text,
               (embedding <=> %s::vector) AS distance
        FROM timeline_events
        WHERE embedding IS NOT NULL {time_clause}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, [vec, *time_params, vec, k])
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "ts": r[1].isoformat() if r[1] else None,
            "app": r[2],
            "window_title": r[3],
            "activity": r[4],
            "ocr_text_excerpt": (r[5] or "")[:240],
            "score": round(1.0 - (r[6] or 2.0) / 2.0, 4),
        }
        for r in rows
    ]


def _exact(dsn, query, k, time_clause, time_params) -> list[dict]:
    sql = f"""
        SELECT id, ts, app, window_title, activity, ocr_text,
               similarity(ocr_text, %s) AS sim
        FROM timeline_events
        WHERE ocr_text %% %s {time_clause}
        ORDER BY sim DESC
        LIMIT %s
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SET pg_trgm.similarity_threshold = 0.05")
        cur.execute(sql, [query, query, *time_params, k])
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "ts": r[1].isoformat() if r[1] else None,
            "app": r[2],
            "window_title": r[3],
            "activity": r[4],
            "ocr_text_excerpt": (r[5] or "")[:240],
            "score": round(float(r[6] or 0.0), 4),
        }
        for r in rows
    ]


def _blend(sem: list[dict], ex: list[dict], k: int) -> list[dict]:
    by_id: dict[int, dict] = {}
    scores: dict[int, list[float]] = {}
    for h in sem:
        by_id[h["id"]] = h
        scores.setdefault(h["id"], []).append(h["score"])
    for h in ex:
        if h["id"] not in by_id:
            by_id[h["id"]] = h
        scores.setdefault(h["id"], []).append(h["score"])
    merged = []
    for hid, hit in by_id.items():
        hit = dict(hit)
        hit["score"] = round(sum(scores[hid]) / len(scores[hid]), 4)
        merged.append(hit)
    merged.sort(key=lambda h: h["score"], reverse=True)
    return merged[:k]


# --- Dispatch ---------------------------------------------------------------


def dispatch(settings: Settings, name: str, arguments: dict) -> dict:
    """Route a tool call from the brain to the right implementation. Raises
    ValueError for unknown tool names so the caller can surface a clean error."""
    if name not in NAMES:
        raise ValueError(f"unknown tool: {name}")
    if name == "search_timeline":
        return search_timeline(settings, **arguments)
    if name == "query_user_model":
        return query_user_model(settings, **arguments)
    if name == "search_kb":
        return search_kb(settings, **arguments)
    if name == "fetch_screenshot":
        return fetch_screenshot(settings, **arguments)
    raise ValueError(f"unhandled tool: {name}")  # unreachable
