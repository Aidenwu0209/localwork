"""DejaView memoryd — ingestion orchestrator (Mac, data-sovereignty side).

Per handbook §6.2, each frame flows through:
  sentinel -> ocrd -> novelty gate -> perceive -> embed -> timeline store -> Honcho

The production wiring uses real Sentinel, OCR, novelty, perceive, and embed
stages by default. Stub stages remain available only through the explicit
`MEMORYD_ALLOW_STUB_PIPELINE=true` test opt-in; that mode reports degraded and
rejects frame pixels. Frame ingest is supported, while audio and document
routes return an honest HTTP 501 until their durable pipelines exist.

Privacy invariant (handbook §0): ingested media is held in memory until the
final allowed store step under DATA_ROOT. A blocked or uncertain Sentinel
decision writes metadata-only audit state and never reaches OCR, screenshot
storage, timeline, or downstream models.
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
