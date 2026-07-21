"""DejaView memoryd — ingestion orchestrator (Mac, data-sovereignty side).

Per handbook §6.2, each frame flows through:
  sentinel -> ocrd -> novelty gate -> perceive -> embed -> timeline store -> Honcho

M3.2 ships the skeleton: FastAPI with the three ingest endpoints
(`/v1/ingest/{frame,audio,doc}`) and a pluggable pipeline where every stage is a
Protocol backed by a stub. Real inference backends are wired in M3.4 (sentinel /
perceive / embed via the gateway) and M5.1 (ocrd). The stubs return canned but
schema-correct results so the ingest path and audit log can be exercised end to
end before any GPU is involved.

Privacy invariant (handbook §0): ingested media is held in memory only, written
to disk solely under DATA_ROOT as the final store step, and a blocked sentinel
decision never reaches OCR. This file enforces neither yet; the stubs make it
trivially auditable.
"""

from memoryd.server import create_app

__all__ = ["create_app", "main"]


def main() -> None:
    """Entry point for `python -m memoryd` / the `memoryd` console script."""
    import uvicorn

    uvicorn.run(
        "memoryd:create_app",
        factory=True,
        host="127.0.0.1",
        port=8090,
        reload=False,
    )
