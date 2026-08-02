"""Public ingest API outcome and unsupported-media contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from memoryd.config import Settings
from memoryd.models import IngestAck
from memoryd.server import create_app


class ScriptedPipeline:
    def __init__(self, acknowledgements: list[IngestAck]) -> None:
        self._acknowledgements = acknowledgements

    async def ingest_frame(self, image_bytes: bytes, meta: object) -> IngestAck:
        return self._acknowledgements.pop(0)


def client_for(pipeline: object) -> TestClient:
    settings = Settings(
        gateway_url="http://127.0.0.1:4000/v1",
        ocr_url="http://127.0.0.1:8006",
        timeline_db_url="postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview",
        redis_url="redis://127.0.0.1:6380/0",
        data_root="/tmp/memoryd-p313-contract",  # type: ignore[arg-type]
        honcho_flush_event_count=20,
        honcho_flush_seconds=300,
    )
    return TestClient(create_app(settings=settings, pipeline=pipeline))


def test_frame_bodies_report_exact_final_states_and_ids() -> None:
    pipeline = ScriptedPipeline(
        [
            IngestAck(accepted=True, event_id=11, processing_state="stored"),
            IngestAck(accepted=True, merged_into=7, processing_state="merged"),
            IngestAck(accepted=False, processing_state="blocked"),
        ]
    )
    client = client_for(pipeline)
    meta = '{"device_id":"synthetic-device","ts":"2026-08-03T00:00:00+00:00"}'
    expected = (
        ("stored", 11, None, True),
        ("merged", None, 7, True),
        ("blocked", None, None, False),
    )
    for state, event_id, merged_into, accepted in expected:
        response = client.post(
            "/v1/ingest/frame",
            files={"file": ("synthetic.png", b"frame", "image/png")},
            data={"meta": meta},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["processing_state"] == state
        assert body["event_id"] == event_id
        assert body["merged_into"] == merged_into
        assert body["accepted"] is accepted


def test_audio_and_doc_are_honestly_unsupported_without_reading_body() -> None:
    client = client_for(object())
    cases = (
        ("audio", '{"device_id":"synthetic-device","ts_start":"2026-08-03T00:00:00+00:00","ts_end":"2026-08-03T00:01:00+00:00"}'),
        ("doc", '{"source_path":"synthetic://document"}'),
    )
    with patch(
        "starlette.datastructures.UploadFile.read",
        new=AsyncMock(side_effect=AssertionError("unsupported media was read")),
    ) as read:
        for kind, meta in cases:
            response = client.post(
                f"/v1/ingest/{kind}",
                files={"file": ("synthetic.bin", b"not-consumed", "application/octet-stream")},
                data={"meta": meta},
            )
            assert response.status_code == 501
            assert response.json()["detail"] == {
                "code": "unsupported_media",
                "stored": False,
                "supported": ["frame"],
            }
    read.assert_not_awaited()

    for kind in ("audio", "doc"):
        response = client.post(
            f"/v1/ingest/{kind}",
            files={"file": ("synthetic.bin", b"not-consumed", "application/octet-stream")},
            data={"meta": "not-json"},
        )
        assert response.status_code == 422
