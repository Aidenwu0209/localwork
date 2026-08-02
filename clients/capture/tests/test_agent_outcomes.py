from __future__ import annotations

import asyncio
from unittest.mock import patch

from capture.agent import (
    CaptureCounters,
    _send_heartbeat,
    apply_upload_outcome,
    recovery_pending,
    run_agent,
    should_commit_frame,
)
from capture.config import CaptureConfig
from capture.permissions import PermissionCheck
from capture.uploader import UploadOutcome


def test_only_terminal_successes_commit_dedup_and_counters() -> None:
    counters = CaptureCounters()
    hashes: dict[str, object] = {}
    timestamp = 10.0
    for outcome, frame_hash, should_commit in (
        (UploadOutcome(state="stored", event_id=1), "stored-hash", True),
        (UploadOutcome(state="merged", merged_into=1), "merged-hash", True),
        (UploadOutcome(state="blocked"), "blocked-hash", True),
        (UploadOutcome(state="failed", error_code="transport_error"), "failed-hash", False),
    ):
        timestamp = apply_upload_outcome(
            outcome,
            counters=counters,
            window_hashes=hashes,
            window_key="Synthetic::Window",
            frame_hash=frame_hash,
            now=timestamp + 1,
            last_upload_ts=timestamp,
        )
        assert should_commit_frame(outcome) is should_commit
    assert hashes == {"Synthetic::Window": "blocked-hash"}
    assert hashes["Synthetic::Window"] != "failed-hash"
    assert timestamp == 13.0
    assert counters.as_dict() == {"stored": 1, "merged": 1, "blocked": 1, "failed": 1}
    payload = counters.heartbeat_payload("synthetic-device", "2026-08-03T00:00:00+00:00")
    assert payload == {
        "device_id": "synthetic-device", "client_ts": "2026-08-03T00:00:00+00:00",
        "stored": 1, "merged": 1, "blocked": 1, "failed": 1,
    }
    assert set(payload) == {"device_id", "client_ts", "stored", "merged", "blocked", "failed"}


class FixtureHeartbeatResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FixtureHeartbeatClient:
    def __init__(self, response: FixtureHeartbeatResponse) -> None:
        self._response = response

    async def post(self, *args: object, **kwargs: object) -> FixtureHeartbeatResponse:
        return self._response


def test_heartbeat_requires_success_status_and_explicit_acceptance() -> None:
    config = CaptureConfig(device_id="synthetic-device")
    counters = CaptureCounters(stored=1)
    cases = (
        (FixtureHeartbeatResponse(200, {"accepted": True}), True),
        (FixtureHeartbeatResponse(503, {"accepted": True}), False),
        (FixtureHeartbeatResponse(200, {"accepted": False}), False),
        (FixtureHeartbeatResponse(200, ValueError("SENSITIVE-HEARTBEAT")), False),
    )
    for response, expected in cases:
        accepted = asyncio.run(
            _send_heartbeat(config, counters, FixtureHeartbeatClient(response))  # type: ignore[arg-type]
        )
        assert accepted is expected


def test_failed_heartbeat_keeps_recovery_pending() -> None:
    config = CaptureConfig(device_id="synthetic-device")
    rejected = asyncio.run(
        _send_heartbeat(
            config,
            CaptureCounters(),
            FixtureHeartbeatClient(FixtureHeartbeatResponse(503, {"accepted": True})),  # type: ignore[arg-type]
        )
    )
    assert recovery_pending(had_upload_failure=True, heartbeat_accepted=rejected) is True
    assert recovery_pending(had_upload_failure=True, heartbeat_accepted=True) is False


class FixtureAsyncClient:
    async def __aenter__(self) -> "FixtureAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class FixtureClock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def monotonic(self) -> float:
        return next(self._values)


def test_locked_agent_sends_due_heartbeat_without_using_capture_apis() -> None:
    heartbeats: list[str] = []

    async def send_heartbeat(config: CaptureConfig, *args: object) -> bool:
        heartbeats.append(config.device_id)
        return True

    async def stop_after_locked_wait(*args: object) -> None:
        raise asyncio.CancelledError

    def lock_agent(state: object) -> bool:
        state.locked = True  # type: ignore[attr-defined]
        return True

    with (
        patch(
            "capture.agent.check_screen_recording_permission",
            return_value=PermissionCheck(True, "granted"),
        ),
        patch("capture.agent._install_lock_observer", side_effect=lock_agent),
        patch("capture.agent._pump_runloop"),
        patch("capture.agent.time", FixtureClock(0.0, 0.0, 31.0)),
        patch("capture.agent.httpx.AsyncClient", return_value=FixtureAsyncClient()),
        patch("capture.agent._send_heartbeat", side_effect=send_heartbeat),
        patch("capture.agent.get_active_window", side_effect=AssertionError("active window read")),
        patch("capture.agent.list_windows", side_effect=AssertionError("window list read")),
        patch("capture.agent.capture_window_png", side_effect=AssertionError("window capture")),
        patch("capture.agent.upload_frame", side_effect=AssertionError("frame upload")),
        patch("capture.agent.asyncio.sleep", side_effect=stop_after_locked_wait),
    ):
        assert asyncio.run(run_agent(CaptureConfig(device_id="synthetic-device"))) == 0
    assert heartbeats == ["synthetic-device"]
