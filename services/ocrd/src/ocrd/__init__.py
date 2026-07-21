"""ocrd — deterministic verbatim OCR microservice (handbook §6.1).

Exposes :func:`create_app` for ASGI runners and :func:`main` for the
``python -m ocrd`` / ``ocrd`` console-script entry point. The default dev
backend is ``rapidocr-onnxruntime`` (fast on Apple Silicon); switch to
PaddleOCR PP-OCRv6 by setting ``OCR_BACKEND=paddleocr`` (production target on
EPYC). See ``services/ocrd/README.md`` and ``docs/verification-log.md``.
"""

from __future__ import annotations

from .server import create_app

__all__ = ["create_app", "main"]


def main() -> None:
    """Console entry point: serve ocrd on 127.0.0.1:8006 via uvicorn.

    Binds to loopback only — this is an internal service called by memoryd,
    never exposed directly. Port 8006 is the handbook §6.1 designation.
    """
    import uvicorn

    uvicorn.run(
        "ocrd:create_app",
        factory=True,
        host="127.0.0.1",
        port=8006,
        log_level="info",
        # Access log off by default: handbook §6.1 requires no content
        # logging, and the access log would record request paths only (no
        # bodies) — we keep it off to minimise disk chatter.
        access_log=False,
    )
