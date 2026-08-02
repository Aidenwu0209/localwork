"""Asynchronous-safe, idempotent projection of local timeline rows to Honcho."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

import httpx

from memoryd.config import Settings
from memoryd.storage import HonchoOutboxRow


class ProjectionStore(Protocol):
    def projection_enabled(self) -> bool: ...
    def lease_honcho_rows(self, *, batch_size: int, lease_seconds: int, now: datetime) -> list[HonchoOutboxRow]: ...
    def mark_honcho_sent(self, *, event_id: int, now: datetime) -> None: ...
    def retry_honcho_row(self, *, event_id: int, error: str, next_attempt_at: datetime) -> None: ...
    def fail_honcho_row(self, *, event_id: int, error: str, now: datetime) -> None: ...


class HonchoProjectionWorker:
    """Projects each outbox row once; timeline storage never depends on success."""

    def __init__(
        self,
        *,
        store: ProjectionStore,
        settings: Settings,
        client_factory: Callable[[], httpx.Client] = httpx.Client,
    ) -> None:
        self._store = store
        self._settings = settings
        self._client_factory = client_factory

    def run_once(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        if not self._store.projection_enabled():
            return 0
        rows = self._store.lease_honcho_rows(
            batch_size=self._settings.honcho_batch_size,
            lease_seconds=self._settings.honcho_lease_seconds,
            now=now,
        )
        delivered = 0
        for row in rows:
            try:
                self._deliver(row)
            except Exception as exc:
                error = _error_code(exc)
                if row.attempt_count >= self._settings.honcho_max_attempts:
                    self._store.fail_honcho_row(event_id=row.event_id, error=error, now=now)
                else:
                    self._store.retry_honcho_row(
                        event_id=row.event_id,
                        error=error,
                        next_attempt_at=now + timedelta(seconds=self._retry_delay(row.attempt_count)),
                    )
            else:
                self._store.mark_honcho_sent(event_id=row.event_id, now=now)
                delivered += 1
        return delivered

    def _retry_delay(self, attempt_count: int) -> int:
        return min(
            self._settings.honcho_max_retry_seconds,
            self._settings.honcho_retry_seconds * (2 ** max(0, attempt_count - 1)),
        )

    def _deliver(self, row: HonchoOutboxRow) -> None:
        base = self._settings.honcho_url.rstrip("/")
        workspace = self._settings.honcho_workspace
        peer = self._settings.honcho_peer
        with self._client_factory() as client:
            self._ensure(client, f"{base}/v3/workspaces", {"id": workspace, "name": workspace})
            self._ensure(
                client,
                f"{base}/v3/workspaces/{workspace}/peers",
                {"id": peer, "name": peer},
            )
            self._ensure(
                client,
                f"{base}/v3/workspaces/{workspace}/sessions",
                {"id": row.session_id},
            )
            response = client.post(
                f"{base}/v3/workspaces/{workspace}/sessions/{row.session_id}/messages",
                json={
                    "messages": [
                        {
                            "content": json.dumps(row.payload, separators=(",", ":")),
                            "peer_id": peer,
                        }
                    ]
                },
                headers={"Idempotency-Key": f"dejaview-event-{row.event_id}"},
            )
            response.raise_for_status()

    @staticmethod
    def _ensure(client: httpx.Client, url: str, body: dict[str, str]) -> None:
        response = client.post(url, json=body)
        if response.status_code != 409:
            response.raise_for_status()


def _error_code(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return "upstream_4xx" if 400 <= status < 500 else "upstream_5xx"
    if isinstance(exc, httpx.RequestError):
        return "network_error"
    return "unexpected_error"
