"""Production sentinel wiring and fail-closed pipeline behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from memoryd.config import Settings
from memoryd.models import (
    FrameMeta,
    NoveltyVerdict,
    OcrResult,
    PerceiveEvent,
    SentinelVerdict,
)
from memoryd.pipeline import Pipeline
from memoryd.server import _default_pipeline
from memoryd.stages import StubSentinel


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "gateway_url": "http://127.0.0.1:4000/v1",
        "sentinel_gateway_url": "http://127.0.0.1:4000/v1",
        "ocr_url": "http://127.0.0.1:8006",
        "timeline_db_url": "postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview",
        "redis_url": "redis://127.0.0.1:6380/0",
        "data_root": Path("/tmp/memoryd-p313-test").resolve(),
        "honcho_flush_event_count": 20,
        "honcho_flush_seconds": 300,
        "allow_stub_pipeline": False,
    }
    values.update(overrides)
    return Settings(**values)


def frame_meta() -> FrameMeta:
    return FrameMeta(
        device_id="test-device",
        ts="2026-08-03T00:00:00+00:00",
        app="Code",
        window_title="test.py",
    )


class ExplodingSentinel:
    async def classify(self, image_bytes: bytes) -> SentinelVerdict:
        raise RuntimeError("sentinel connection secret must not escape")


class ReturningSentinel:
    def __init__(self, verdict: SentinelVerdict) -> None:
        self._verdict = verdict

    async def classify(self, image_bytes: bytes) -> SentinelVerdict:
        return self._verdict


class RecordingOcr:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def recognize(self, image_bytes: bytes) -> OcrResult:
        self._calls.append("ocr")
        return OcrResult(full_text="safe text")


class RecordingNovelty:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def assess(self, **_: object) -> NoveltyVerdict:
        self._calls.append("novelty")
        return NoveltyVerdict(
            novelty=1.0, delta="new event", merge_into_previous=False, tier="jaccard"
        )


class RecordingPerceive:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def understand(self, **_: object) -> PerceiveEvent:
        self._calls.append("perceive")
        return PerceiveEvent(activity="safe activity", app_context="ide")


class RecordingEmbed:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def embed(self, text: str) -> list[float]:
        self._calls.append("embed")
        return [0.0]


class RecordingStore:
    def __init__(self, root: Path, calls: list[str]) -> None:
        self._root = root
        self._calls = calls
        self.audit_rows: list[tuple[str, str]] = []
        self.timeline_rows: list[dict[str, object]] = []

    def write_sentinel_audit(
        self, *, ts: str, device_id: str, verdict: SentinelVerdict
    ) -> int:
        self.audit_rows.append((verdict.decision, verdict.reason))
        return 1

    def fetch_last_event_ocr(
        self, *, device_id: str, app: str | None
    ) -> tuple[int | None, str | None]:
        self._calls.append("store.fetch")
        return None, None

    def merge_into_previous(self, *, event_id: int, ts: str) -> None:
        self._calls.append("store.merge")

    def screenshot_target(self, *, device_id: str, ts: str) -> Path:
        self._calls.append("store.screenshot")
        return self._root / "screenshots" / "frame.webp"

    def insert_event(self, **values: object) -> int:
        self._calls.append("store.insert")
        self.timeline_rows.append(values)
        return len(self.timeline_rows)


def recording_pipeline(tmp_path: Path, sentinel: object) -> SimpleNamespace:
    downstream_calls: list[str] = []
    store = RecordingStore(tmp_path, downstream_calls)
    pipeline = Pipeline(
        sentinel=sentinel,
        ocr=RecordingOcr(downstream_calls),
        novelty=RecordingNovelty(downstream_calls),
        perceive=RecordingPerceive(downstream_calls),
        embed=RecordingEmbed(downstream_calls),
        store=store,
    )
    return SimpleNamespace(
        pipeline=pipeline,
        audit_rows=store.audit_rows,
        downstream_calls=downstream_calls,
        timeline_rows=store.timeline_rows,
    )


def test_default_pipeline_never_uses_stub_sentinel() -> None:
    settings = make_settings(sentinel_gateway_url="http://127.0.0.1:4000/v1")
    with patch("memoryd.server.GatewaySentinel") as gateway:
        pipeline = _default_pipeline(settings)
    gateway.assert_called_once_with(settings.sentinel_gateway_url)
    assert not isinstance(pipeline.sentinel, StubSentinel)


def test_settings_parse_explicit_stub_flag_and_sentinel_gateway() -> None:
    with patch.dict(
        "os.environ",
        {
            "SENTINEL_GATEWAY_URL": "http://127.0.0.1:4010/v1",
            "MEMORYD_ALLOW_STUB_PIPELINE": "yes",
        },
        clear=False,
    ):
        settings = Settings.from_env()
    assert settings.sentinel_gateway_url == "http://127.0.0.1:4010/v1"
    assert settings.allow_stub_pipeline is True

    with patch.dict("os.environ", {"MEMORYD_ALLOW_STUB_PIPELINE": "maybe"}, clear=False):
        with pytest.raises(ValueError, match="MEMORYD_ALLOW_STUB_PIPELINE"):
            Settings.from_env()


def test_explicit_stub_pipeline_is_degraded_and_rejects_frames() -> None:
    from fastapi.testclient import TestClient
    from memoryd.server import create_app

    settings = make_settings(allow_stub_pipeline=True)
    pipeline = _default_pipeline(settings)
    pipeline.ingest_frame = AsyncMock()
    client = TestClient(create_app(settings=settings, pipeline=pipeline))

    health = client.get("/health")
    assert health.json()["status"] == "degraded"
    assert health.json()["pipeline"] == "stub"
    assert health.json()["accepting_frames"] is False

    with patch(
        "starlette.datastructures.UploadFile.read",
        new=AsyncMock(side_effect=AssertionError("stub ingress read the frame")),
    ) as read:
        response = client.post(
            "/v1/ingest/frame",
            files={"file": ("frame.webp", b"private pixels", "image/webp")},
            data={"meta": frame_meta().model_dump_json()},
        )
    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "pipeline_not_ready",
            "accepted": False,
            "reason": "stub_pipeline",
        }
    }
    read.assert_not_awaited()
    pipeline.ingest_frame.assert_not_awaited()

    invalid_meta = client.post(
        "/v1/ingest/frame",
        files={"file": ("frame.webp", b"private pixels", "image/webp")},
        data={"meta": "not-json"},
    )
    assert invalid_meta.status_code == 422
    pipeline.ingest_frame.assert_not_awaited()


def test_sentinel_exception_audits_block_and_calls_nothing_downstream(tmp_path: Path) -> None:
    stages = recording_pipeline(tmp_path, sentinel=ExplodingSentinel())
    ack = asyncio.run(stages.pipeline.ingest_frame(b"synthetic", frame_meta()))

    assert ack.processing_state == "blocked"
    assert ack.sentinel.reason == "sentinel_unavailable"
    assert stages.audit_rows == [("block", "sentinel_unavailable")]
    assert stages.downstream_calls == []
    assert stages.timeline_rows == []
    assert list(tmp_path.rglob("*.webp")) == []
    assert "secret" not in ack.note


def test_every_block_reason_audits_once_and_short_circuits(tmp_path: Path) -> None:
    cases = (
        SentinelVerdict(
            decision="block", category="banking_finance", confidence=1.0,
            reason="sensitive_category",
        ),
        SentinelVerdict(
            decision="block", category="normal", confidence=0.0,
            reason="malformed_output",
        ),
        SentinelVerdict(
            decision="block", category="normal", confidence=0.0,
            reason="unknown_category",
        ),
        SentinelVerdict(
            decision="block", category="normal", confidence=0.0,
            reason="low_confidence",
        ),
    )
    for verdict in cases:
        stages = recording_pipeline(tmp_path, sentinel=ReturningSentinel(verdict))
        ack = asyncio.run(stages.pipeline.ingest_frame(b"synthetic", frame_meta()))

        assert ack.processing_state == "blocked"
        assert ack.sentinel.reason == verdict.reason
        assert stages.audit_rows == [("block", verdict.reason)]
        assert stages.downstream_calls == []
        assert stages.timeline_rows == []
        assert list(tmp_path.rglob("*.webp")) == []


def test_allowed_frame_calls_all_downstream_stages_once(tmp_path: Path) -> None:
    stages = recording_pipeline(
        tmp_path,
        sentinel=ReturningSentinel(
            SentinelVerdict(
                decision="allow", category="normal", confidence=0.9,
                reason="classified_normal",
            )
        ),
    )
    ack = asyncio.run(stages.pipeline.ingest_frame(b"synthetic", frame_meta()))

    assert ack.accepted is True
    assert stages.audit_rows == [("allow", "classified_normal")]
    assert stages.downstream_calls == [
        "ocr",
        "store.fetch",
        "novelty",
        "perceive",
        "embed",
        "store.screenshot",
        "store.insert",
    ]
    assert len(stages.timeline_rows) == 1
