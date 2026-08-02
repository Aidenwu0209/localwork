"""Timeline + audit storage (Postgres on the data-sovereignty side).

Schema is defined by deploy/mac/timeline-init.sql. This module is the only place
that writes timeline_events / sentinel_audit, so the privacy invariant (blocked
frames write ONLY to sentinel_audit, never to timeline or disk) lives here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from memoryd.models import (
    NoveltyVerdict,
    OcrResult,
    PerceiveEvent,
    SentinelVerdict,
)


@dataclass(frozen=True)
class HonchoOutboxRow:
    event_id: int
    payload: dict[str, object]
    session_id: str
    attempt_count: int


_HONCHO_LEASE_SQL = """
WITH candidates AS (
  SELECT event_id
  FROM honcho_outbox
  WHERE (state = 'pending' AND next_attempt_at <= %s)
     OR (state = 'sending' AND lease_expires_at <= %s)
  ORDER BY next_attempt_at, event_id
  FOR UPDATE SKIP LOCKED
  LIMIT %s
)
UPDATE honcho_outbox AS outbox
SET state = 'sending',
    attempt_count = attempt_count + 1,
    lease_expires_at = %s,
    updated_at = %s
FROM candidates
WHERE outbox.event_id = candidates.event_id
RETURNING outbox.event_id, outbox.payload, outbox.session_id, outbox.attempt_count
"""


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
                """INSERT INTO sentinel_audit
                   (ts, device_id, category, decision, confidence, reason)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (
                    ts,
                    device_id,
                    verdict.category,
                    verdict.decision,
                    verdict.confidence,
                    verdict.reason,
                ),
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
        app_context: str | None = None,
    ) -> int:
        """Persist an allowed frame and its closed Honcho projection atomically.

        The payload deliberately has no access to OCR, verbatim, browser, or
        screenshot fields.  A failed outbox insert aborts the surrounding
        Postgres transaction, so there is never an unprojectable stored row.
        """
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
            event_id = int(cur.fetchone()[0])
            payload = _projection_payload(
                event_id=event_id,
                ts=ts,
                app_context=app_context or "other",
                activity=activity or "",
                topics=topics,
            )
            cur.execute(
                """INSERT INTO honcho_outbox (event_id, payload, session_id)
                   VALUES (%s, %s, %s)""",
                (
                    event_id,
                    json.dumps(payload, separators=(",", ":")),
                    _honcho_session_id(ts),
                ),
            )
            return event_id

    def merge_into_previous(self, *, event_id: int, ts: str) -> None:
        """Extend an existing event's end_ts when the novelty gate merges."""
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE timeline_events SET end_ts = %s WHERE id = %s",
                (ts, event_id),
            )

    def projection_enabled(self) -> bool:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT enabled FROM honcho_projection_control WHERE singleton = true",
                (),
            )
            row = cur.fetchone()
            return bool(row[0]) if row is not None else True

    def set_projection_enabled(self, *, enabled: bool) -> bool:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO honcho_projection_control (singleton, enabled)
                   VALUES (true, %s)
                   ON CONFLICT (singleton) DO UPDATE
                   SET enabled = EXCLUDED.enabled, updated_at = now()
                   RETURNING enabled""",
                (enabled,),
            )
            return bool(cur.fetchone()[0])

    def lease_honcho_rows(
        self, *, batch_size: int, lease_seconds: int, now: datetime
    ) -> list[HonchoOutboxRow]:
        lease_until = now + timedelta(seconds=lease_seconds)
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                _HONCHO_LEASE_SQL,
                (now, now, batch_size, lease_until, now),
            )
            rows = cur.fetchall()
        return [_row_to_honcho_outbox(row) for row in rows]

    def mark_honcho_sent(self, *, event_id: int, now: datetime) -> None:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE honcho_outbox
                   SET state = 'sent', sent_at = %s, updated_at = %s,
                       lease_expires_at = NULL, last_error = NULL
                   WHERE event_id = %s AND state = 'sending'""",
                (now, now, event_id),
            )

    def retry_honcho_row(
        self, *, event_id: int, error: str, next_attempt_at: datetime
    ) -> None:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE honcho_outbox
                   SET state = 'pending', next_attempt_at = %s, last_error = %s,
                       lease_expires_at = NULL, updated_at = now()
                   WHERE event_id = %s AND state = 'sending'""",
                (next_attempt_at, _safe_projection_error(error), event_id),
            )

    def fail_honcho_row(self, *, event_id: int, error: str, now: datetime) -> None:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE honcho_outbox
                   SET state = 'failed', last_error = %s, lease_expires_at = NULL,
                       updated_at = %s
                   WHERE event_id = %s AND state = 'sending'""",
                (_safe_projection_error(error), now, event_id),
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


def _honcho_session_id(ts: str) -> str:
    """Stable daily session name using the required Japan local calendar."""
    occurred_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return f"dejaview-{occurred_at.astimezone(ZoneInfo('Asia/Tokyo')).date().isoformat()}"


def _projection_payload(
    *, event_id: int, ts: str, app_context: str, activity: str, topics: list[str]
) -> dict[str, object]:
    """Return the *complete* public projection schema, never a filtered copy."""
    return {
        "schema": "dejaview.honcho_projection.v1",
        "event_id": event_id,
        "occurred_at": ts,
        "app_context": app_context,
        "activity": activity,
        "topics": list(topics),
    }


def _row_to_honcho_outbox(row: tuple[object, ...]) -> HonchoOutboxRow:
    event_id, payload, session_id, attempt_count = row
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("honcho outbox payload is malformed")
    return HonchoOutboxRow(
        event_id=int(event_id),
        payload=payload,
        session_id=str(session_id),
        attempt_count=int(attempt_count),
    )


def _safe_projection_error(error: str) -> str:
    """Persist only fixed public error categories, never an upstream body."""
    allowed = {"network_error", "upstream_4xx", "upstream_5xx", "unexpected_error"}
    return error if error in allowed else "unexpected_error"
