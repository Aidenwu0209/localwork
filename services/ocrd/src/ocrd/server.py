"""FastAPI app for ocrd — the deterministic verbatim OCR microservice.

Handbook §6.1 contract:

    POST /ocr  (multipart, field ``file`` = image)
        -> {"full_text": "...",
            "blocks": [{"text": "...", "bbox": [x1,y1,x2,y2], "conf": 0.98}]}

memoryd reaches this service directly via ``OCR_URL`` — it is NOT an LLM and
does not go through the LiteLLM gateway. The service is stateless: images are
processed in-memory and discarded immediately (no content logging, no disk
persistence), per the privacy rules in handbook §6.1.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .engine import get_engine, load_image

logger = logging.getLogger(__name__)

# Allowed upload MIME prefixes. We deliberately accept the common image/*
# variants plus the generic octet-stream (curl -F sometimes sends that) and
# then let PIL sniff the actual format.
_ACCEPTED_MIME = ("image/", "application/octet-stream")
_MAX_IMAGE_BYTES = 25 * 1024 * 1024  # 25 MiB cap; screenshots are well under.


def create_app() -> FastAPI:
    """Build the FastAPI app.

    The OCR engine is intentionally NOT constructed here — it is lazily built
    on the first ``/ocr`` request via :func:`ocrd.engine.get_engine`, so the
    service starts in milliseconds and only pays the model-load cost when OCR
    is actually needed. ``/health`` returns before any model is loaded.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = FastAPI(
        title="ocrd",
        description=(
            "Deterministic verbatim OCR layer (PP-OCR series, ONNX Runtime). "
            "Handbook §6.1. Not an LLM; memoryd connects via OCR_URL."
        ),
        version="0.1.0",
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Liveness probe.

        Returns the configured backend name; does NOT trigger model load so
        it stays cheap. ``ready=False`` until the first successful OCR has
        warmed the engine.
        """
        from .engine import DEFAULT_BACKEND, _registry

        return {
            "status": "ok",
            "backend": DEFAULT_BACKEND,
            "engine_loaded": _registry._engine is not None,  # noqa: SLF001
        }

    @app.post("/ocr")
    async def ocr(file: UploadFile = File(...)) -> JSONResponse:
        """Run OCR on a single uploaded image.

        Returns the handbook §6.1 contract: ``full_text`` + ``blocks`` where
        each block has ``text``, ``bbox`` (``[x1,y1,x2,y2]``) and ``conf``.
        Layout is compatible with ``memoryd.models.OcrResult`` / ``OcrBlock``.
        """
        # Read while still in the async context (the upload stream lives only
        # for the request), then do the CPU-bound OCR work.
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="empty file upload")
        if len(raw) > _MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"image too large: {len(raw)} bytes "
                f"(limit {_MAX_IMAGE_BYTES})",
            )
        ctype = (file.content_type or "").lower()
        if ctype and not ctype.startswith(_ACCEPTED_MIME):
            raise HTTPException(
                status_code=415,
                detail=f"unsupported content_type: {ctype}",
            )

        try:
            image = load_image(raw)
        except Exception as exc:  # noqa: BLE001 - PIL raises various formats
            logger.warning("image decode failed: %s", exc)
            raise HTTPException(
                status_code=422, detail="could not decode image"
            ) from exc

        engine = get_engine()
        t0 = time.perf_counter()
        result = engine.ocr(image)
        dt_ms = (time.perf_counter() - t0) * 1000.0

        # Build the JSON-serialisable payload. bbox stays as float pixels.
        payload: dict[str, Any] = {
            "full_text": result.full_text,
            "blocks": [
                {
                    "text": b.text,
                    "bbox": [float(v) for v in b.bbox],
                    "conf": float(b.conf),
                }
                for b in result.blocks
            ],
            # Diagnostics (non-contract extras; safe for memoryd to ignore).
            "backend": engine.backend_name,
            "elapsed_ms": round(dt_ms, 1),
            "n_blocks": len(result.blocks),
        }
        logger.info(
            "ocr ok backend=%s blocks=%d elapsed_ms=%.1f",
            engine.backend_name,
            len(result.blocks),
            dt_ms,
        )
        # No image bytes or pixel data are retained beyond this point.
        return JSONResponse(payload)

    return app


__all__ = ["create_app"]
