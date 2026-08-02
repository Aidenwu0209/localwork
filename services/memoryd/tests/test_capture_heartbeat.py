from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from memoryd.config import Settings
from memoryd.metrics import MemoryMetrics
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
    before_receipt = time.time()
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
    after_receipt = time.time()
    for state, count in (("stored", 1), ("merged", 2), ("blocked", 3), ("failed", 4)):
        assert f'dejaview_capture_frames_total{{outcome="{state}"}} {count}' in metrics
    heartbeat_line = next(
        line
        for line in metrics.splitlines()
        if line.startswith("dejaview_capture_last_heartbeat_unixtime ")
    )
    last_receipt = float(heartbeat_line.rsplit(" ", 1)[1])
    assert before_receipt <= last_receipt <= after_receipt


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
    assert response.json() == {"detail": {"code": "invalid_heartbeat"}}
    assert "SENSITIVE" not in response.text


@pytest.mark.parametrize(
    "client_ts",
    [0, 1.5, True, None, "2026-08-03T00:00:00"],
)
def test_heartbeat_rejects_non_iso_or_naive_client_timestamps(client_ts: object) -> None:
    app = client()
    payload = {
        "device_id": "synthetic-device",
        "client_ts": client_ts,
        "stored": 0, "merged": 0, "blocked": 0, "failed": 0,
    }
    response = app.post("/v1/capture/heartbeat", json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_heartbeat"}}


def test_heartbeat_accepts_timezone_aware_iso_client_timestamp() -> None:
    app = client()
    response = app.post(
        "/v1/capture/heartbeat",
        json={
            "device_id": "synthetic-device",
            "client_ts": "2026-08-03T00:00:00+00:00",
            "stored": 0, "merged": 0, "blocked": 0, "failed": 0,
        },
    )
    assert response.status_code == 200
    assert response.json() == {"accepted": True}


@pytest.mark.parametrize("counter", [True, False, "1"])
def test_heartbeat_rejects_coerced_counter_values(counter: object) -> None:
    app = client()
    payload = {
        "device_id": "synthetic-device",
        "client_ts": "2026-08-03T00:00:00+00:00",
        "stored": counter, "merged": 0, "blocked": 0, "failed": 0,
    }
    response = app.post("/v1/capture/heartbeat", json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_heartbeat"}}


def test_heartbeat_rejects_malformed_json_without_reflecting_input() -> None:
    app = client()
    response = app.post(
        "/v1/capture/heartbeat",
        content=b'{"window_title":"SENSITIVE-HEARTBEAT"',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_heartbeat"}}
    assert "SENSITIVE-HEARTBEAT" not in response.text


def test_heartbeat_totals_are_monotonic_across_client_restart_and_devices() -> None:
    app = client()
    initial = {
        "device_id": "synthetic-device",
        "client_ts": "2026-08-03T00:00:00+00:00",
        "stored": 10, "merged": 0, "blocked": 0, "failed": 0,
    }
    restarted = {**initial, "client_ts": "2026-08-03T00:01:00+00:00", "stored": 1}
    duplicate = dict(restarted)
    older = {**initial, "stored": 999}
    second_device = {
        **restarted,
        "device_id": "synthetic-device-2",
        "stored": 2,
    }
    assert app.post("/v1/capture/heartbeat", json=initial).json() == {"accepted": True}
    assert app.post("/v1/capture/heartbeat", json=restarted).json() == {"accepted": True}
    assert app.post("/v1/capture/heartbeat", json=duplicate).json() == {"accepted": False}
    assert app.post("/v1/capture/heartbeat", json=older).json() == {"accepted": False}
    assert app.post("/v1/capture/heartbeat", json=second_device).json() == {"accepted": True}
    metrics = app.get("/metrics").text
    assert 'dejaview_capture_frames_total{outcome="stored"} 13' in metrics


def test_heartbeat_freshness_uses_server_receipt_not_future_client_clock() -> None:
    received_at = [1_800_000_000.25]
    metrics = MemoryMetrics(clock=lambda: received_at[0])
    future_client_ts = datetime(2099, 1, 1, tzinfo=timezone.utc)
    counters = {"stored": 1, "merged": 0, "blocked": 0, "failed": 0}

    assert metrics.observe_capture_heartbeat(
        device_id="synthetic-device",
        client_ts=future_client_ts,
        counters=counters,
    ) is True
    assert "dejaview_capture_last_heartbeat_unixtime 1800000000.25" in (
        metrics.render_prometheus()
    )

    # Even an out-of-order but otherwise valid heartbeat proves the capture
    # process reached memoryd now; it must not mutate cumulative counters.
    received_at[0] = 1_800_000_010.5
    assert metrics.observe_capture_heartbeat(
        device_id="synthetic-device",
        client_ts=datetime(2026, 8, 3, tzinfo=timezone.utc),
        counters={"stored": 99, "merged": 0, "blocked": 0, "failed": 0},
    ) is False
    rendered = metrics.render_prometheus()
    assert "dejaview_capture_last_heartbeat_unixtime 1800000010.5" in rendered
    assert 'dejaview_capture_frames_total{outcome="stored"} 1' in rendered
