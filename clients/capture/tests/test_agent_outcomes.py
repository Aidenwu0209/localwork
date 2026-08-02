from __future__ import annotations

import asyncio

from capture.agent import (
    CaptureCounters,
    _send_heartbeat,
    apply_upload_outcome,
    recovery_pending,
    should_commit_frame,
)
from capture.config import CaptureConfig
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
