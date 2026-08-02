"""FastAPI app exposing the three ingest endpoints (handbook §5.3).

  POST /v1/ingest/frame   multipart/form-data: file=webp, meta={...}
  POST /v1/ingest/audio   wav bytes + meta
  POST /v1/ingest/doc     file + meta

All return 202 with an IngestAck (or 202 with accepted=false when the sentinel
blocks a frame — the request succeeded, the frame was just refused on privacy
grounds). Health check at /health for docker/orchestration.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from typing import Annotated, TypeVar
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator

from memoryd.config import Settings
from memoryd.honcho_projection import HonchoProjectionWorker
from memoryd.metrics import MemoryMetrics
from memoryd.models import AudioMeta, DocMeta, FrameMeta, IngestAck
from memoryd.pipeline import Pipeline
from memoryd.search import search_timeline
from memoryd.stages import (
    GatewayEmbed,
    GatewayPerceive,
    GatewaySentinel,
    OcrdClient,
    RealNovelty,
    StubEmbed,
    StubNovelty,
    StubOcr,
    StubPerceive,
    StubSentinel,
)
from memoryd.storage import TimelineStore


MetaT = TypeVar("MetaT", bound=BaseModel)


class CaptureHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=128)
    client_ts: datetime
    stored: Annotated[int, Field(strict=True, ge=0)]
    merged: Annotated[int, Field(strict=True, ge=0)]
    blocked: Annotated[int, Field(strict=True, ge=0)]
    failed: Annotated[int, Field(strict=True, ge=0)]

    @field_validator("client_ts", mode="before")
    @classmethod
    def _require_iso_string(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("client_ts must be an ISO-8601 string")
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("client_ts must be an ISO-8601 string") from exc
        return value

    @field_validator("client_ts")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("client_ts must include timezone")
        return value


def _validated_meta(meta: str, model: type[MetaT]) -> MetaT:
    """Parse request metadata without reflecting malformed input to callers."""
    try:
        return model.model_validate_json(meta)
    except Exception as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_meta"}) from exc


def _safe_url_origin(value: str) -> str:
    """Return only scheme/host/port, never URL credentials or query data."""
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        return "invalid"
    try:
        port = parsed.port
    except ValueError:
        return "invalid"
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port_suffix = f":{port}" if port is not None else ""
    return f"{parsed.scheme.lower()}://{host}{port_suffix}"


def _pipeline_identity(pipeline: object) -> str:
    """Describe the pipeline that is actually wired into this app."""
    if not isinstance(pipeline, Pipeline):
        return "custom"
    real_stage_types = (
        GatewaySentinel,
        OcrdClient,
        RealNovelty,
        GatewayPerceive,
        GatewayEmbed,
    )
    stages = (
        pipeline.sentinel,
        pipeline.ocr,
        pipeline.novelty,
        pipeline.perceive,
        pipeline.embed,
    )
    if all(
        isinstance(stage, expected)
        for stage, expected in zip(stages, real_stage_types, strict=True)
    ) and isinstance(pipeline.store, TimelineStore):
        return "real"
    if any(
        isinstance(
            stage,
            (StubSentinel, StubOcr, StubNovelty, StubPerceive, StubEmbed),
        )
        for stage in stages
    ):
        return "stub"
    return "custom"


def _accepting_frames(pipeline: object) -> bool:
    """Whether the active pipeline is safe to receive frame pixels."""
    return _pipeline_identity(pipeline) != "stub"


def _default_pipeline(settings: Settings) -> Pipeline:
    """Wire real stages by default; stubs require an explicit unsafe opt-in."""
    if not settings.allow_stub_pipeline:
        return Pipeline(
            sentinel=GatewaySentinel(settings.sentinel_gateway_url),
            ocr=OcrdClient(settings.ocr_url),
            novelty=RealNovelty(settings.gateway_url),
            perceive=GatewayPerceive(settings.gateway_url),
            embed=GatewayEmbed(settings.gateway_url),
            store=TimelineStore(
                dsn=settings.timeline_db_url, data_root=settings.data_root
            ),
        )
    return Pipeline(
        sentinel=StubSentinel(),
        ocr=StubOcr(),
        novelty=StubNovelty(),
        perceive=StubPerceive(),
        embed=StubEmbed(),
        store=TimelineStore(dsn=settings.timeline_db_url, data_root=settings.data_root),
    )


def create_app(
    *,
    settings: Settings | None = None,
    pipeline: Pipeline | None = None,
    projection_store: object | None = None,
    projection_worker: object | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    pipeline = pipeline or _default_pipeline(settings)
    metrics = MemoryMetrics()

    if projection_store is None and isinstance(pipeline, Pipeline) and isinstance(
        pipeline.store, TimelineStore
    ):
        projection_store = pipeline.store
    if projection_worker is None and isinstance(projection_store, TimelineStore):
        projection_worker = HonchoProjectionWorker(store=projection_store, settings=settings)

    def _projection_status() -> dict[str, object]:
        if projection_store is None or not hasattr(projection_store, "projection_status"):
            raise HTTPException(status_code=503, detail={"code": "projection_not_ready"})
        try:
            status = projection_store.projection_status()  # type: ignore[union-attr]
        except Exception as exc:
            raise HTTPException(status_code=503, detail={"code": "projection_not_ready"}) from exc
        if not isinstance(status, dict):
            raise HTTPException(status_code=503, detail={"code": "projection_not_ready"})
        metrics.observe_honcho_projection(status)
        return status

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task: asyncio.Task[None] | None = None
        if projection_worker is not None:
            async def _run_projection() -> None:
                while True:
                    try:
                        await asyncio.to_thread(projection_worker.run_once)  # type: ignore[union-attr]
                        _projection_status()
                    except asyncio.CancelledError:
                        raise
                    except HTTPException:
                        pass
                    except Exception:
                        # Worker/storage failures are represented in the local
                        # outbox; do not turn an upstream outage into an app crash.
                        pass
                    await asyncio.sleep(settings.honcho_poll_seconds)
            task = asyncio.create_task(_run_projection(), name="honcho-projection")
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(
        title="DejaView memoryd",
        version="0.1.0",
        description="Ingestion orchestrator (handbook §6.2).",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str | bool]:
        pipeline_identity = _pipeline_identity(pipeline)
        accepting_frames = _accepting_frames(pipeline)
        return {
            "status": "ok" if accepting_frames else "degraded",
            "pipeline": pipeline_identity,
            "accepting_frames": accepting_frames,
            "gateway_origin": _safe_url_origin(settings.gateway_url),
            "database": urlsplit(settings.timeline_db_url).path.removeprefix("/"),
            "data_root": str(settings.data_root),
        }

    @app.get("/metrics")
    async def prometheus_metrics() -> Response:
        return Response(
            content=metrics.render_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.post("/v1/capture/heartbeat")
    async def capture_heartbeat(request: Request) -> dict[str, bool]:
        try:
            body = CaptureHeartbeat.model_validate(await request.json())
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail={"code": "invalid_heartbeat"}
            ) from exc
        accepted = metrics.observe_capture_heartbeat(
            device_id=body.device_id,
            client_ts=body.client_ts,
            counters={
                "stored": body.stored,
                "merged": body.merged,
                "blocked": body.blocked,
                "failed": body.failed,
            },
        )
        return {"accepted": accepted}

    @app.get("/v1/profile/status")
    async def profile_status() -> dict[str, object]:
        return _projection_status()

    @app.post("/v1/profile/pause")
    async def pause_profile_projection() -> dict[str, bool]:
        if projection_store is None or not hasattr(projection_store, "set_projection_enabled"):
            raise HTTPException(status_code=503, detail={"code": "projection_not_ready"})
        try:
            projection_store.set_projection_enabled(enabled=False)  # type: ignore[union-attr]
        except Exception as exc:
            raise HTTPException(status_code=503, detail={"code": "projection_not_ready"}) from exc
        _projection_status()
        return {"enabled": False, "paused": True}

    @app.post("/v1/profile/resume")
    async def resume_profile_projection() -> dict[str, bool]:
        if projection_store is None or not hasattr(projection_store, "set_projection_enabled"):
            raise HTTPException(status_code=503, detail={"code": "projection_not_ready"})
        try:
            projection_store.set_projection_enabled(enabled=True)  # type: ignore[union-attr]
        except Exception as exc:
            raise HTTPException(status_code=503, detail={"code": "projection_not_ready"}) from exc
        _projection_status()
        return {"enabled": True, "paused": False}

    @app.post("/v1/ingest/frame", response_model=IngestAck, status_code=202)
    async def ingest_frame(
        file: Annotated[UploadFile, File(description="webp/png/jpeg frame image")],
        meta: Annotated[str, Form(description="JSON FrameMeta")],
    ) -> IngestAck:
        meta_obj = _validated_meta(meta, FrameMeta)
        if not _accepting_frames(pipeline):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "pipeline_not_ready",
                    "accepted": False,
                    "reason": "stub_pipeline",
                },
            )
        image_bytes = await file.read()
        ack = await pipeline.ingest_frame(image_bytes, meta_obj)
        metrics.observe_frame(ack)
        # Keep 202 even on sentinel-block: the ingest call itself succeeded.
        return ack

    @app.post("/v1/ingest/audio", response_model=IngestAck, status_code=202)
    async def ingest_audio(
        file: Annotated[UploadFile, File(description="wav (16k mono) segment")],
        meta: Annotated[str, Form(description="JSON AudioMeta")],
    ) -> IngestAck:
        # Skeleton: parse + accept. Real wiring (perceive/whisper.cpp transcript
        # -> transcript event) lands with T1.7 once the audio path is decided.
        _validated_meta(meta, AudioMeta)
        raise HTTPException(
            status_code=501,
            detail={
                "code": "unsupported_media",
                "stored": False,
                "supported": ["frame"],
            },
        )

    @app.post("/v1/ingest/doc", response_model=IngestAck, status_code=202)
    async def ingest_doc(
        file: Annotated[UploadFile, File(description="any document")],
        meta: Annotated[str, Form(description="JSON DocMeta")],
    ) -> IngestAck:
        # Skeleton: parse + accept. Real wiring (MarkItDown -> kb_chunks) is T2.3.
        _validated_meta(meta, DocMeta)
        raise HTTPException(
            status_code=501,
            detail={
                "code": "unsupported_media",
                "stored": False,
                "supported": ["frame"],
            },
        )

    @app.post("/v1/search")
    async def search(body: dict) -> dict:
        """Three-mode timeline search (handbook §6.5): semantic / exact / hybrid.

        Body: {query, mode=hybrid, k=5, time_from?, time_to?}. The query is
        embedded with the Qwen3 instruction prefix on the semantic side.
        """
        query = (body.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=422, detail="`query` is required")
        mode = body.get("mode", "hybrid")
        if mode not in ("hybrid", "semantic", "exact"):
            raise HTTPException(
                status_code=422,
                detail=f"mode must be hybrid|semantic|exact, got {mode}",
            )
        k = int(body.get("k", 5))
        time_from = body.get("time_from")
        time_to = body.get("time_to")

        # Embed the query (instruction-prefixed) for semantic/hybrid. exact-only
        # skips embedding.
        query_vec = None
        if mode in ("hybrid", "semantic"):
            if not isinstance(pipeline.embed, GatewayEmbed):
                raise HTTPException(
                    status_code=503,
                    detail="semantic/hybrid search requires the gateway-backed embed; "
                    "gateway not reachable (start dev-stack.sh up embed)",
                )
            query_vec = await pipeline.embed.embed_query(query)

        hits = await asyncio.to_thread(
            search_timeline,
            dsn=settings.timeline_db_url,
            query=query,
            mode=mode,
            k=k,
            time_from=time_from,
            time_to=time_to,
            query_vec=query_vec,
        )
        return {
            "query": query,
            "mode": mode,
            "k": k,
            "hits": [h.to_dict() for h in hits],
        }

    return app
