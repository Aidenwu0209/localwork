"""Storage and SQL contracts that run without a PostgreSQL connection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from memoryd.models import SentinelVerdict
from memoryd.storage import TimelineStore


TS = "2026-08-03T00:00:00+00:00"
ROOT = Path(__file__).resolve().parents[3]


class RecordingCursor:
    def __init__(self, executed: list[tuple[str, tuple[object, ...]]]) -> None:
        self._executed = executed

    def __enter__(self) -> "RecordingCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self._executed.append((sql, params))

    def fetchone(self) -> tuple[int]:
        return (1,)


class RecordingConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> "RecordingConnection":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self.executed)


def test_audit_insert_contains_only_closed_metadata(tmp_path: Path) -> None:
    connection = RecordingConnection()
    verdict = SentinelVerdict(
        decision="block", category="normal", confidence=0.0, reason="malformed_output"
    )
    with patch("memoryd.storage.psycopg.connect", return_value=connection):
        store = TimelineStore("postgresql://synthetic", tmp_path)
        store.write_sentinel_audit(ts=TS, device_id="synthetic-device", verdict=verdict)

    sql, params = connection.executed[0]
    assert "reason" in sql.lower()
    assert params == (
        TS,
        "synthetic-device",
        "normal",
        "block",
        0.0,
        "malformed_output",
    )


def test_clean_schema_and_idempotent_migration_close_audit_reasons() -> None:
    schema = (ROOT / "deploy/mac/timeline-init.sql").read_text()
    migration = (ROOT / "deploy/mac/migrations/20260802_p313_privacy_reason.sql").read_text()
    for sql in (schema, migration):
        lowered = sql.lower()
        assert "reason" in lowered
        assert "not null" in lowered
        assert "classified_normal" in lowered
        assert "sentinel_unavailable" in lowered
    assert "add column if not exists reason" in migration.lower()
    assert "pg_constraint" in migration.lower()
    assert "p313_sentinel_audit_reason_check" in migration
    assert "conrelid = 'sentinel_audit'::regclass" in migration
    assert "contype = 'c'" in migration
