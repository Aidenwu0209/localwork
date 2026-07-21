"""FastAPI app exposing the three ingest endpoints (handbook §5.3).

  POST /v1/ingest/frame   multipart/form-data: file=webp, meta={...}
  POST /v1/ingest/audio   wav bytes + meta
  POST /v1/ingest/doc     file + meta

All return 202 with an IngestAck (or 202 with accepted=false when the sentinel
blocks a frame — the request succeeded, the frame was just refused on privacy
grounds). Health check at /health for docker/orchestration.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from memoryd.config import Settings
from memoryd.models import AudioMeta, DocMeta, FrameMeta, IngestAck
from memoryd.pipeline import Pipeline
from memoryd.stages import (
    StubEmbed,
    StubNovelty,
    StubOcr,
    StubPerceive,
    StubSentinel,
)
from memoryd.storage import TimelineStore


def _default_pipeline(settings: Settings) -> Pipeline:
    """M3.2 default: all-stub pipeline + real Postgres store. M3.4 replaces the
    stubs with gateway-backed stages by constructing Pipeline explicitly."""
    return Pipeline(
        sentinel=StubSentinel(),
        ocr=StubOcr(),
        novelty=StubNovelty(),
        perceive=StubPerceive(),
        embed=StubEmbed(),
        store=TimelineStore(
            dsn=settings.timeline_db_url, data_root=settings.data_root
        ),
    )


def create_app(
    *, settings: Settings | None = None, pipeline: Pipeline | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    pipeline = pipeline or _default_pipeline(settings)

    app = FastAPI(
        title="DejaView memoryd",
        version="0.1.0",
        description="Ingestion orchestrator (handbook §6.2).",
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/ingest/frame", response_model=IngestAck, status_code=202)
    async def ingest_frame(
        file: Annotated[UploadFile, File(description="webp/png/jpeg frame image")],
        meta: Annotated[str, Form(description="JSON FrameMeta")],
    ) -> IngestAck:
        try:
            meta_obj = FrameMeta.model_validate_json(meta)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"invalid meta JSON: {exc}") from exc
        image_bytes = await file.read()
        ack = await pipeline.ingest_frame(image_bytes, meta_obj)
        # Keep 202 even on sentinel-block: the ingest call itself succeeded.
        return ack

    @app.post("/v1/ingest/audio", response_model=IngestAck, status_code=202)
    async def ingest_audio(
        file: Annotated[UploadFile, File(description="wav (16k mono) segment")],
        meta: Annotated[str, Form(description="JSON AudioMeta")],
    ) -> IngestAck:
        # Skeleton: parse + accept. Real wiring (perceive/whisper.cpp transcript
        # -> transcript event) lands with T1.7 once the audio path is decided.
        try:
            AudioMeta.model_validate_json(meta)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"invalid meta JSON: {exc}") from exc
        await file.read()  # drain; not persisted in M3.2
        return IngestAck(accepted=True, note="audio stubbed: accepted, not transcribed")

    @app.post("/v1/ingest/doc", response_model=IngestAck, status_code=202)
    async def ingest_doc(
        file: Annotated[UploadFile, File(description="any document")],
        meta: Annotated[str, Form(description="JSON DocMeta")],
    ) -> IngestAck:
        # Skeleton: parse + accept. Real wiring (MarkItDown -> kb_chunks) is T2.3.
        try:
            DocMeta.model_validate_json(meta)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"invalid meta JSON: {exc}") from exc
        await file.read()
        return IngestAck(accepted=True, note="doc stubbed: accepted, not chunked")

    return app
