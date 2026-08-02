from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import httpx
import pytest

from capture.config import CaptureConfig
from capture.uploader import UploadOutcome, upload_frame


class FixtureResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body
        self.text = "SENSITIVE-SERVER-BODY"

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FixtureClient:
    def __init__(self, response: FixtureResponse | Exception) -> None:
        self._response = response

    async def post(self, *args: object, **kwargs: object) -> FixtureResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


@pytest.mark.parametrize(
    ("body", "state"),
    [
        ({"accepted": True, "processing_state": "stored", "event_id": 7, "merged_into": None}, "stored"),
        ({"accepted": True, "processing_state": "merged", "event_id": None, "merged_into": 7}, "merged"),
        ({"accepted": False, "processing_state": "blocked", "event_id": None, "merged_into": None}, "blocked"),
    ],
)
def test_upload_parses_terminal_outcome(body: dict[str, object], state: str) -> None:
    outcome = asyncio.run(
        upload_frame(
            CaptureConfig(device_id="synthetic-device"),
            webp_bytes=b"synthetic",
            app="Demo",
            window_title="Synthetic",
            url=None,
            trigger="change",
            client=FixtureClient(FixtureResponse(202, body)),  # type: ignore[arg-type]
        )
    )
    assert outcome.state == state


def test_upload_failures_are_stable_and_body_free() -> None:
    config = CaptureConfig(device_id="synthetic-device")
    cases = (
        (FixtureClient(FixtureResponse(202, {"accepted": True})), "invalid_response"),
        (FixtureClient(FixtureResponse(500, {"error": "SENSITIVE-SERVER-BODY"})), "http_error"),
        (FixtureClient(FixtureResponse(202, ValueError("SENSITIVE-SERVER-BODY"))), "invalid_response"),
        (FixtureClient(httpx.ConnectError("https://user:secret@example.test")), "transport_error"),
    )
    for client, error_code in cases:
        outcome = asyncio.run(
            upload_frame(
                config,
                webp_bytes=b"synthetic",
                app=None,
                window_title=None,
                url=None,
                trigger="change",
                client=client,  # type: ignore[arg-type]
            )
        )
        assert (outcome.state, outcome.error_code) == ("failed", error_code)
        assert "SENSITIVE" not in repr(outcome)
        assert "secret" not in repr(outcome)


@pytest.mark.parametrize(
    "body",
    [
        {"accepted": True, "processing_state": "stored", "event_id": 0, "merged_into": None},
        {"accepted": True, "processing_state": "stored", "event_id": -1, "merged_into": None},
        {"accepted": True, "processing_state": "stored", "event_id": True, "merged_into": None},
        {"accepted": True, "processing_state": "merged", "event_id": None, "merged_into": 0},
        {"accepted": True, "processing_state": "merged", "event_id": None, "merged_into": -1},
        {"accepted": True, "processing_state": "merged", "event_id": None, "merged_into": True},
    ],
)
def test_upload_rejects_nonpositive_or_boolean_terminal_ids(body: dict[str, object]) -> None:
    outcome = asyncio.run(
        upload_frame(
            CaptureConfig(device_id="synthetic-device"),
            webp_bytes=b"synthetic",
            app=None,
            window_title=None,
            url=None,
            trigger="change",
            client=FixtureClient(FixtureResponse(202, body)),  # type: ignore[arg-type]
        )
    )
    assert (outcome.state, outcome.error_code) == ("failed", "invalid_response")


def test_upload_outcome_is_frozen() -> None:
    outcome = UploadOutcome(state="stored", event_id=1)
    with pytest.raises(FrozenInstanceError):
        outcome.state = "failed"  # type: ignore[misc]
