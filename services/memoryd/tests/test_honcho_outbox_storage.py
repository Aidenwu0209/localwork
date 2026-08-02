"""P3.15 atomic timeline-to-Honcho outbox storage contract."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from memoryd.storage import TimelineStore


ROOT = Path(__file__).resolve().parents[3]
TS = "2026-08-03T15:30:00+00:00"  # 00:30 in the required Asia/Tokyo session.


class Cursor:
    def __init__(self, connection: "Connection") -> None:
        self.connection = connection

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.connection.executed.append((sql, params))
        if self.connection.fail_outbox and "honcho_outbox" in sql:
            raise RuntimeError("synthetic outbox failure")

    def fetchone(self) -> tuple[int]:
        return (41,)


class Connection:
    def __init__(self, *, fail_outbox: bool = False) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.fail_outbox = fail_outbox
        self.rolled_back = False

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type: object, *args: object) -> None:
        self.rolled_back = exc_type is not None

    def cursor(self) -> Cursor:
        return Cursor(self)


def _insert(store: TimelineStore) -> int:
    return store.insert_event(
        ts=TS,
        device_id="synthetic-device",
        kind="frame",
        app="Browser with private window title",
        window_title="FORBIDDEN-WINDOW-TITLE",
        url="https://FORBIDDEN-URL.example/private?token=forbidden",
        activity="Reviewing an implementation plan",
        topics=["implementation", "review"],
        verbatim={"quotes": ["FORBIDDEN-VERBATIM"]},
        ocr_text="FORBIDDEN-OCR-TEXT",
        ocr_blocks=[{"text": "FORBIDDEN-BBOX", "bbox": [1, 2, 3, 4]}],
        screenshot_path="/FORBIDDEN-SCREENSHOT.webp",
        embedding=[0.0],
        app_context="browser",
    )


def test_stored_event_and_outbox_are_one_transaction(tmp_path: Path) -> None:
    connection = Connection(fail_outbox=True)
    with patch("memoryd.storage.psycopg.connect", return_value=connection):
        with pytest.raises(RuntimeError, match="synthetic outbox failure"):
            _insert(TimelineStore("postgresql://synthetic", tmp_path))

    assert len(connection.executed) == 2
    assert "INSERT INTO timeline_events" in connection.executed[0][0]
    assert "INSERT INTO honcho_outbox" in connection.executed[1][0]
    assert connection.rolled_back is True


def test_projection_payload_has_only_allowlisted_semantic_fields(tmp_path: Path) -> None:
    connection = Connection()
    with patch("memoryd.storage.psycopg.connect", return_value=connection):
        assert _insert(TimelineStore("postgresql://synthetic", tmp_path)) == 41

    outbox_sql, outbox_params = connection.executed[1]
    assert "honcho_outbox" in outbox_sql
    assert outbox_params[0] == 41
    payload = json.loads(outbox_params[1])
    assert payload == {
        "schema": 1,
        "event_id": 41,
        "occurred_at": TS,
        "app_context": "browser",
        "activity": "Reviewing an implementation plan",
        "topics": ["implementation", "review"],
    }
    serialized = json.dumps(payload)
    for forbidden in (
        "FORBIDDEN-WINDOW-TITLE",
        "FORBIDDEN-URL",
        "FORBIDDEN-VERBATIM",
        "FORBIDDEN-OCR",
        "FORBIDDEN-BBOX",
        "FORBIDDEN-SCREENSHOT",
        "metadata",
    ):
        assert forbidden not in serialized
    # The constructor default follows the project's UTC+8 local time, rather
    # than a hard-coded foreign timezone: 15:30 UTC is still Aug 3 in Shanghai.
    assert outbox_params[2] == "dejaview-2026-08-03"


def test_projection_session_uses_configured_user_timezone(tmp_path: Path) -> None:
    connection = Connection()
    with patch("memoryd.storage.psycopg.connect", return_value=connection):
        _insert(
            TimelineStore(
                "postgresql://synthetic", tmp_path, honcho_timezone="Asia/Tokyo"
            )
        )
    assert connection.executed[1][1][2] == "dejaview-2026-08-04"


def test_clean_schema_and_migration_define_idempotent_projection_outbox() -> None:
    schema = (ROOT / "deploy/mac/timeline-init.sql").read_text().lower()
    migration = (
        ROOT / "deploy/mac/migrations/20260803_p315_honcho_outbox.sql"
    ).read_text().lower()
    for sql in (schema, migration):
        assert "honcho_outbox" in sql
        assert "event_id" in sql
        assert "primary key" in sql
        assert "honcho_projection_control" in sql
    assert "create table if not exists" in migration
    assert "on conflict" in migration
    assert "lease_expires_at" in migration
    assert "next_attempt_at" in migration
