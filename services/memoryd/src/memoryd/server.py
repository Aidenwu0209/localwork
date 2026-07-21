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
import json
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from memoryd.config import Settings
from memoryd.models import AudioMeta, DocMeta, FrameMeta, IngestAck
from memoryd.pipeline import Pipeline
from memoryd.search import SearchMode, SearchHit, search_timeline
from memoryd.stages import (
    GatewayEmbed,
    StubEmbed,
    StubNovelty,
    StubOcr,
    StubPerceive,
    StubSentinel,
)
from memoryd.storage import TimelineStore


def _make_embed(settings: Settings):
    """Use the real gateway-backed embed when a gateway is configured; fall back
    to the stub when the gateway isn't reachable (so dev without a GPU still
    works for the ingest path)."""
    try:
        import httpx
        base = settings.gateway_url.rstrip("/").removesuffix("/v1")
        with httpx.Client(timeout=3.0) as c:
            c.get(f"{base}/v1/models")
        return GatewayEmbed(settings.gateway_url)
    except Exception:
        return StubEmbed()


def _default_pipeline(settings: Settings) -> Pipeline:
    """Pipeline wiring. Set env MEMORYD_REAL_PIPELINE=1 to use the real Metal
    inference stack (M3.4): sentinel + ocrd + fast novelty + perceive + embed,
    all via the gateway / ocrd. Without it (default), the stub stages run except
    for embed, which auto-upgrades to GatewayEmbed when the gateway is up."""
    import os
    real = os.environ.get("MEMORYD_REAL_PIPELINE", "").strip() in ("1", "true", "yes")
    if real:
        from memoryd.stages import GatewayPerceive, GatewaySentinel, OcrdClient, RealNovelty
        return Pipeline(
            sentinel=GatewaySentinel(settings.gateway_url),
            ocr=OcrdClient(settings.ocr_url),
            novelty=RealNovelty(settings.gateway_url),
            perceive=GatewayPerceive(settings.gateway_url),
            embed=_make_embed(settings),
            store=TimelineStore(dsn=settings.timeline_db_url, data_root=settings.data_root),
        )
    return Pipeline(
        sentinel=StubSentinel(),
        ocr=StubOcr(),
        novelty=StubNovelty(),
        perceive=StubPerceive(),
        embed=_make_embed(settings),
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
            raise HTTPException(status_code=422, detail=f"mode must be hybrid|semantic|exact, got {mode}")
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
        return {"query": query, "mode": mode, "k": k, "hits": [h.to_dict() for h in hits]}

    return app
