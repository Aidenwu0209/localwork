from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from memoryd.config import Settings
from memoryd.server import create_app


def client() -> TestClient:
    settings = Settings(
        gateway_url="http://127.0.0.1:4000/v1",
        ocr_url="http://127.0.0.1:8006",
        timeline_db_url="postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview",
        redis_url="redis://127.0.0.1:6380/0",
        data_root=Path("/tmp/memoryd-heartbeat").resolve(),
        honcho_flush_event_count=20,
        honcho_flush_seconds=300,
    )
    return TestClient(create_app(settings=settings, pipeline=object()))


def test_heartbeat_keeps_only_newest_metadata_and_exports_aggregate() -> None:
    app = client()
    payload = {
        "device_id": "synthetic-device",
        "client_ts": "2026-08-03T00:00:00+00:00",
        "stored": 1, "merged": 2, "blocked": 3, "failed": 4,
    }
    assert app.post("/v1/capture/heartbeat", json=payload).status_code == 200
    assert app.post("/v1/capture/heartbeat", json=payload).json() == {"accepted": False}
    old = {**payload, "client_ts": "2026-08-02T00:00:00+00:00", "stored": 99}
    assert app.post("/v1/capture/heartbeat", json=old).json() == {"accepted": False}
    metrics = app.get("/metrics").text
    for state, count in (("stored", 1), ("merged", 2), ("blocked", 3), ("failed", 4)):
        assert f'dejaview_capture_frames_total{{outcome="{state}"}} {count}' in metrics
    assert "dejaview_capture_last_heartbeat_unixtime 1785715200.0" in metrics


def test_heartbeat_rejects_nonmetadata_invalid_values() -> None:
    app = client()
    invalid = {
        "device_id": "synthetic-device",
        "client_ts": "2026-08-03T00:00:00",
        "stored": -1, "merged": 0, "blocked": 0, "failed": 0,
        "window_title": "SENSITIVE",
    }
    response = app.post("/v1/capture/heartbeat", json=invalid)
    assert response.status_code == 422
