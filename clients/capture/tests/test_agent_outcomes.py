from __future__ import annotations

from capture.agent import CaptureCounters, apply_upload_outcome, should_commit_frame
from capture.uploader import UploadOutcome


def test_only_terminal_successes_commit_dedup_and_counters() -> None:
    counters = CaptureCounters()
    hashes: dict[str, object] = {}
    timestamp = 10.0
    for outcome, should_commit in (
        (UploadOutcome(state="stored", event_id=1), True),
        (UploadOutcome(state="merged", merged_into=1), True),
        (UploadOutcome(state="blocked"), True),
        (UploadOutcome(state="failed", error_code="transport_error"), False),
    ):
        timestamp = apply_upload_outcome(
            outcome,
            counters=counters,
            window_hashes=hashes,
            window_key="Synthetic::Window",
            frame_hash="hash",
            now=timestamp + 1,
            last_upload_ts=timestamp,
        )
        assert should_commit_frame(outcome) is should_commit
    assert hashes == {"Synthetic::Window": "hash"}
    assert timestamp == 13.0
    assert counters.as_dict() == {"stored": 1, "merged": 1, "blocked": 1, "failed": 1}
    payload = counters.heartbeat_payload("synthetic-device", "2026-08-03T00:00:00+00:00")
    assert payload == {
        "device_id": "synthetic-device", "client_ts": "2026-08-03T00:00:00+00:00",
        "stored": 1, "merged": 1, "blocked": 1, "failed": 1,
    }
    assert set(payload) == {"device_id", "client_ts", "stored", "merged", "blocked", "failed"}
