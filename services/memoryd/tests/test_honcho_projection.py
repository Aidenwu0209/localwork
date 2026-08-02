"""P3.15 idempotent, privacy-closed Honcho projection worker contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from memoryd.config import Settings
from memoryd.honcho_projection import HonchoOutboxRow, HonchoProjectionWorker


@dataclass
class FakeStore:
    rows: list[HonchoOutboxRow]
    enabled: bool = True

    def __post_init__(self) -> None:
        self.sent: list[int] = []
        self.retries: list[tuple[int, str, datetime]] = []
        self.lease_calls = 0

    def projection_enabled(self) -> bool:
        return self.enabled

    def lease_honcho_rows(
        self, *, batch_size: int, lease_seconds: int, now: datetime
    ) -> list[HonchoOutboxRow]:
        self.lease_calls += 1
        leased, self.rows = self.rows[:batch_size], self.rows[batch_size:]
        return leased

    def mark_honcho_sent(self, *, event_id: int, now: datetime) -> None:
        self.sent.append(event_id)

    def retry_honcho_row(
        self, *, event_id: int, error: str, next_attempt_at: datetime
    ) -> None:
        self.retries.append((event_id, error, next_attempt_at))

    def fail_honcho_row(self, *, event_id: int, error: str, now: datetime) -> None:
        self.retries.append((event_id, error, now))


def _settings() -> Settings:
    return Settings(
        gateway_url="http://127.0.0.1:4000/v1",
        ocr_url="http://127.0.0.1:8006",
        timeline_db_url="postgresql://synthetic",
        redis_url="redis://synthetic",
        data_root="/tmp/dejaview-p315",  # type: ignore[arg-type]
        honcho_flush_event_count=20,
        honcho_flush_seconds=300,
        honcho_url="http://honcho.synthetic",
        honcho_workspace="dejaview",
        honcho_peer="owner",
        honcho_poll_seconds=1,
        honcho_lease_seconds=30,
        honcho_retry_seconds=2,
        honcho_max_attempts=3,
        honcho_batch_size=10,
        honcho_timezone="Asia/Tokyo",
    )


def _row(event_id: int = 9, attempt_count: int = 1) -> HonchoOutboxRow:
    return HonchoOutboxRow(
        event_id=event_id,
        payload={
            "schema": 1,
            "event_id": event_id,
            "occurred_at": "2026-08-03T15:30:00+00:00",
            "app_context": "browser",
            "activity": "Reviewing an implementation plan",
            "topics": ["implementation"],
        },
        session_id="dejaview-2026-08-04",
        attempt_count=attempt_count,
    )


def _worker(store: FakeStore, handler: Any) -> HonchoProjectionWorker:
    transport = httpx.MockTransport(handler)
    return HonchoProjectionWorker(
        store=store, settings=_settings(), client_factory=lambda: httpx.Client(transport=transport)
    )


def test_delivers_one_closed_projection_and_replay_never_duplicates() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "ok"})

    store = FakeStore([_row()])
    worker = _worker(store, handler)
    assert worker.run_once(now=datetime(2026, 8, 3, tzinfo=timezone.utc)) == 1
    assert worker.run_once(now=datetime(2026, 8, 3, tzinfo=timezone.utc)) == 0
    assert store.sent == [9]

    messages_request = next(r for r in requests if r.url.path.endswith("/messages"))
    assert messages_request.headers["idempotency-key"] == "dejaview-event-9"
    body = json.loads(messages_request.content)
    message = body["messages"]
    assert len(message) == 1
    assert message[0]["peer_id"] == "owner"
    projection = json.loads(message[0]["content"])
    assert projection == _row().payload
    body_text = messages_request.content.decode()
    for forbidden in (
        "ocr", "url", "window_title", "verbatim", "screenshot", "bbox",
        "pixels", "metadata", "FORBIDDEN",
    ):
        assert forbidden not in body_text.lower()


def test_network_failure_schedules_sanitized_bounded_exponential_retry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("token=FORBIDDEN", request=request)

    store = FakeStore([_row(attempt_count=2)])
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    assert _worker(store, handler).run_once(now=now) == 0
    assert store.sent == []
    assert store.retries == [(9, "network_error", datetime(2026, 8, 3, 0, 0, 4, tzinfo=timezone.utc))]


def test_last_attempt_is_failed_without_exposing_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="FORBIDDEN upstream diagnostics")

    store = FakeStore([_row(attempt_count=3)])
    assert _worker(store, handler).run_once(now=datetime(2026, 8, 3, tzinfo=timezone.utc)) == 0
    assert store.retries == [(9, "upstream_5xx", datetime(2026, 8, 3, tzinfo=timezone.utc))]


def test_storage_leasing_sql_is_concurrent_and_recovers_expired_leases() -> None:
    from memoryd.storage import _HONCHO_LEASE_SQL

    sql = _HONCHO_LEASE_SQL.lower()
    assert "for update skip locked" in sql
    assert "state = 'sending'" in sql
    assert "lease_expires_at <=" in sql
    assert "attempt_count = attempt_count + 1" in sql
