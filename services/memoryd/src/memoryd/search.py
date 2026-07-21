"""Three-mode timeline search (handbook §6.5).

  search_timeline(query, mode=hybrid|semantic|exact, time_from?, time_to?, k)

  - semantic: HNSW cosine over the 1024-dim embedding. Query side adds the
    Qwen3-Embedding instruction prefix (Instruct: 检索用户活动时间线\nQuery: …)
    via GatewayEmbed.embed_query. Good for "what was I doing around databases?".
  - exact: pg_trgm trigram similarity on ocr_text — direct hits on error codes,
    PR numbers, URLs. Good for "ROCM-4042" or "github.com/foo/bar".
  - hybrid: union of both, deduped by id, sorted by a normalised blend score.

All three honour optional time_from / time_to bounds (inclusive, ISO-8601).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

import psycopg

SearchMode = Literal["hybrid", "semantic", "exact"]


@dataclass
class SearchHit:
    id: int
    ts: str
    app: Optional[str]
    window_title: Optional[str]
    activity: Optional[str]
    ocr_text: Optional[str]
    score: float  # higher is better; semantic=1-cosine_distance, exact=trgm sim, hybrid=blend

    def to_dict(self) -> dict:
        # Truncate ocr_text for the API response; full text stays in the DB.
        ocr = self.ocr_text
        if ocr and len(ocr) > 240:
            ocr = ocr[:240] + "…"
        return {
            "id": self.id,
            "ts": self.ts,
            "app": self.app,
            "window_title": self.window_title,
            "activity": self.activity,
            "ocr_text_excerpt": ocr,
            "score": round(self.score, 4),
        }


def _time_clause(time_from: Optional[str], time_to: Optional[str]) -> tuple[str, list]:
    """Build an optional `AND ts BETWEEN %s AND %s` clause + params."""
    parts: list[str] = []
    params: list = []
    if time_from:
        parts.append("ts >= %s")
        params.append(_parse_ts(time_from))
    if time_to:
        parts.append("ts <= %s")
        params.append(_parse_ts(time_to))
    if not parts:
        return "", []
    return "AND " + " AND ".join(parts), params


def _parse_ts(s: str) -> datetime:
    """Accept ISO-8601 (with or without Z) and date-only."""
    cleaned = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(f"time value {s!r} is not ISO-8601: {exc}") from exc


def _semantic(
    dsn: str, query_vec: list[float], k: int,
    time_from: Optional[str], time_to: Optional[str],
) -> list[SearchHit]:
    time_clause, time_params = _time_clause(time_from, time_to)
    sql = f"""
        SELECT id, ts, app, window_title, activity, ocr_text,
               (embedding <=> %s::vector) AS distance
        FROM timeline_events
        WHERE embedding IS NOT NULL {time_clause}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, [query_vec, *time_params, query_vec, k])
        rows = cur.fetchall()
    # cosine distance <=> : 0=identical, 2=opposite. Score = 1 - distance/2 so
    # identical -> 1.0, orthogonal -> 0.5.
    return [
        SearchHit(r[0], r[1].isoformat() if r[1] else None, r[2], r[3], r[4], r[5],
                  1.0 - (r[6] or 2.0) / 2.0)
        for r in rows
    ]


def _exact(
    dsn: str, query: str, k: int,
    time_from: Optional[str], time_to: Optional[str],
) -> list[SearchHit]:
    # pg_trgm similarity: 1 = identical, 0 = no overlap. We sort by similarity
    # desc. The GIN index supports the % operator for fast threshold filtering;
    # we ask for rows with non-trivial similarity and rank the top-k.
    time_clause, time_params = _time_clause(time_from, time_to)
    sql = f"""
        SELECT id, ts, app, window_title, activity, ocr_text,
               similarity(ocr_text, %s) AS sim
        FROM timeline_events
        WHERE ocr_text %% %s {time_clause}
        ORDER BY sim DESC
        LIMIT %s
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # set a low similarity threshold so short queries (error codes) still hit
        cur.execute("SET pg_trgm.similarity_threshold = 0.05")
        cur.execute(sql, [query, query, *time_params, k])
        rows = cur.fetchall()
    return [
        SearchHit(r[0], r[1].isoformat() if r[1] else None, r[2], r[3], r[4], r[5],
                  float(r[6] or 0.0))
        for r in rows
    ]


def _blend(semantic_hits: list[SearchHit], exact_hits: list[SearchHit], k: int) -> list[SearchHit]:
    """Merge two ranked lists, deduping by id, averaging their (already
    normalised to [0,1]) scores. Both inputs are already sorted best-first."""
    by_id: dict[int, SearchHit] = {}
    scores: dict[int, list[float]] = {}
    for h in semantic_hits:
        by_id[h.id] = h
        scores.setdefault(h.id, []).append(h.score)
    for h in exact_hits:
        if h.id in by_id:
            # keep the richer ocr_text if either is empty
            if not by_id[h.id].ocr_text and h.ocr_text:
                by_id[h.id].ocr_text = h.ocr_text
        else:
            by_id[h.id] = h
        scores.setdefault(h.id, []).append(h.score)
    merged = []
    for hid, hit in by_id.items():
        avg = sum(scores[hid]) / len(scores[hid])
        merged.append(SearchHit(hit.id, hit.ts, hit.app, hit.window_title,
                                hit.activity, hit.ocr_text, avg))
    merged.sort(key=lambda h: h.score, reverse=True)
    return merged[:k]


def search_timeline(
    *,
    dsn: str,
    query: str,
    mode: SearchMode = "hybrid",
    k: int = 5,
    time_from: Optional[str] = None,
    time_to: Optional[str] = None,
    query_vec: Optional[list[float]] = None,
) -> list[SearchHit]:
    """Run a timeline search. `query_vec` is the instruction-prefixed embedding
    of `query` (callers that already embedded pass it to avoid re-embedding);
    if None and mode needs semantic, the caller must supply it."""
    k = max(1, min(k, 50))
    if mode == "exact":
        return _exact(dsn, query, k, time_from, time_to)
    if mode == "semantic":
        if query_vec is None:
            raise ValueError("semantic mode requires query_vec (use GatewayEmbed.embed_query)")
        return _semantic(dsn, query_vec, k, time_from, time_to)
    # hybrid
    sem = _semantic(dsn, query_vec, k, time_from, time_to) if query_vec is not None else []
    ex = _exact(dsn, query, k, time_from, time_to)
    return _blend(sem, ex, k)
