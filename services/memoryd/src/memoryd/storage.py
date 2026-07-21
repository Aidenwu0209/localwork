"""Timeline + audit storage (Postgres on the data-sovereignty side).

Schema is defined by deploy/mac/timeline-init.sql. This module is the only place
that writes timeline_events / sentinel_audit, so the privacy invariant (blocked
frames write ONLY to sentinel_audit, never to timeline or disk) lives here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

from memoryd.models import (
    NoveltyVerdict,
    OcrResult,
    PerceiveEvent,
    SentinelVerdict,
)


def _screenshot_path(data_root: Path, device_id: str, ts: str) -> Path:
    """DATA_ROOT/screenshots/YYYY/MM/DD/<device>_<ts>.webp (handbook §6.2 step 5).

    The directory is created on demand; the file itself is written by the caller.
    """
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    day_dir = data_root / "screenshots" / f"{dt.year:04d}" / f"{dt.month:02d}" / f"{dt.day:02d}"
    day_dir.mkdir(parents=True, exist_ok=True)
    safe_ts = dt.strftime("%Y%m%dT%H%M%S")
    return day_dir / f"{device_id}_{safe_ts}.webp"


class TimelineStore:
    """Thin wrapper over psycopg. Connections are per-call (M3.2 simplicity);
    M3.3 will introduce a pool when ingest throughput matters.
    """

    def __init__(self, dsn: str, data_root: Path) -> None:
        self._dsn = dsn
        self._data_root = data_root

    def write_sentinel_audit(
        self, *, ts: str, device_id: str, verdict: SentinelVerdict
    ) -> int:
        """Record a sentinel decision. Called for BOTH allow and block."""
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sentinel_audit (ts, device_id, category, decision, confidence)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (ts, device_id, verdict.category, verdict.decision, verdict.confidence),
            )
            return int(cur.fetchone()[0])

    def insert_event(
        self,
        *,
        ts: str,
        device_id: str,
        kind: str,
        app: str | None,
        window_title: str | None,
        url: str | None,
        activity: str | None,
        topics: list[str],
        verbatim: dict[str, Any],
        ocr_text: str,
        ocr_blocks: list[dict[str, Any]],
        screenshot_path: str | None,
        embedding: list[float],
    ) -> int:
        """Persist a fully-processed frame as one timeline_events row."""
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO timeline_events
                   (ts, device_id, kind, app, window_title, url,
                    activity, topics, verbatim, ocr_text, ocr_blocks,
                    screenshot_path, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    ts, device_id, kind, app, window_title, url,
                    activity, topics, json.dumps(verbatim),
                    ocr_text, json.dumps(ocr_blocks),
                    screenshot_path,
                    embedding,
                ),
            )
            return int(cur.fetchone()[0])

    def merge_into_previous(self, *, event_id: int, ts: str) -> None:
        """Extend an existing event's end_ts when the novelty gate merges."""
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE timeline_events SET end_ts = %s WHERE id = %s",
                (ts, event_id),
            )

    def fetch_last_event_ocr(self, *, device_id: str, app: str | None) -> tuple[int | None, str | None]:
        """Used by the novelty gate: previous event id + OCR text in this window."""
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id, ocr_text FROM timeline_events
                   WHERE device_id = %s AND app IS NOT DISTINCT FROM %s
                     AND kind = 'frame'
                   ORDER BY ts DESC LIMIT 1""",
                (device_id, app),
            )
            row = cur.fetchone()
            if row is None:
                return None, None
            return int(row[0]) if row[0] is not None else None, row[1]

    @property
    def data_root(self) -> Path:
        return self._data_root

    def screenshot_target(self, *, device_id: str, ts: str) -> Path:
        return _screenshot_path(self._data_root, device_id, ts)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
