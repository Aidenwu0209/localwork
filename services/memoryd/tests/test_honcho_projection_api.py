"""P3.15 lifecycle, pause/resume and payload-free queue observability."""

from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient

from memoryd.config import Settings
from memoryd.server import create_app


class FakeProjectionStore:
    def __init__(self) -> None:
        self.enabled = True
        self.outbox_rows = 0

    def set_projection_enabled(self, *, enabled: bool) -> bool:
        self.enabled = enabled
        return enabled

    def projection_status(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "paused": not self.enabled,
            "pending": self.outbox_rows,
            "failed": 1,
            "last_success": "2026-08-03T00:00:00+00:00",
            "covered_session_start": "dejaview-2026-08-03",
            "covered_session_end": "dejaview-2026-08-04",
        }


class FakeWorker:
    def __init__(self, store: FakeProjectionStore) -> None:
        self.store = store
        self.calls = 0
        self.deliveries = 0

    def run_once(self) -> int:
        self.calls += 1
        if not self.store.enabled:
            return 0
        self.deliveries += self.store.outbox_rows
        self.store.outbox_rows = 0
        return self.deliveries


def _settings() -> Settings:
    return Settings(
        gateway_url="http://127.0.0.1:4000/v1",
        ocr_url="http://127.0.0.1:8006",
        timeline_db_url="postgresql://synthetic",
        redis_url="redis://synthetic",
        data_root="/tmp/dejaview-p315",  # type: ignore[arg-type]
        honcho_flush_event_count=20,
        honcho_flush_seconds=300,
        honcho_poll_seconds=60,
    )


def test_status_pause_resume_are_idempotent_and_never_expose_payloads() -> None:
    store = FakeProjectionStore()
    worker = FakeWorker(store)
    with TestClient(
        create_app(
            settings=_settings(),
            pipeline=object(),
            projection_store=store,
            projection_worker=worker,
        )
    ) as client:
        initial = client.get("/v1/profile/status")
        assert initial.status_code == 200
        assert initial.json() == {
            "enabled": True,
            "paused": False,
            "pending": 0,
            "failed": 1,
            "last_success": "2026-08-03T00:00:00+00:00",
            "covered_session_start": "dejaview-2026-08-03",
            "covered_session_end": "dejaview-2026-08-04",
        }
        assert client.post("/v1/profile/pause").json() == {"enabled": False, "paused": True}
        assert client.post("/v1/profile/pause").json() == {"enabled": False, "paused": True}
        # Ingestion/outbox creation remains local; only delivery is stopped.
        store.outbox_rows = 1
        assert worker.run_once() == 0
        assert store.outbox_rows == 1
        assert client.post("/v1/profile/resume").json() == {"enabled": True, "paused": False}
        assert client.post("/v1/profile/resume").json() == {"enabled": True, "paused": False}
        assert worker.run_once() == 1
        assert store.outbox_rows == 0
        # A status read is also the explicitly supported no-payload metrics
        # refresh boundary when a worker was driven synchronously in this test.
        assert client.get("/v1/profile/status").status_code == 200
        metrics = client.get("/metrics").text
        assert "dejaview_honcho_projection_pending 0" in metrics
        assert "dejaview_honcho_projection_failed 1" in metrics
        assert "ocr" not in metrics.lower()
        assert "payload" not in metrics.lower()


def test_lifespan_starts_worker_without_leaking_after_client_closes() -> None:
    store = FakeProjectionStore()
    worker = FakeWorker(store)
    app = create_app(
        settings=_settings(), pipeline=object(), projection_store=store, projection_worker=worker
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        time.sleep(0.03)
        assert worker.calls >= 1
    after_close = worker.calls
    time.sleep(0.03)
    assert worker.calls == after_close
