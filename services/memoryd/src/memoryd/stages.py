"""Pluggable pipeline stages (handbook §6.2).

Each stage is a Protocol with a stub implementation. M3.2 wires the stubs so the
ingest path runs end to end; M3.4 swaps sentinel/perceive/embed for real
gateway-backed implementations and M5.1 swaps OCR for the ocrd microservice.

The stubs return canned but schema-correct results, so the FastAPI layer, the
audit log, and the timeline write path can all be exercised before any model is
loaded.
"""

from __future__ import annotations

from typing import Protocol

from memoryd.models import (
    NoveltyVerdict,
    OcrResult,
    PerceiveEvent,
    SentinelVerdict,
)


class SentinelStage(Protocol):
    """Privacy gate. Sees the raw image; returns allow/block + category."""

    async def classify(self, image_bytes: bytes) -> SentinelVerdict: ...


class OcrStage(Protocol):
    """Deterministic verbatim layer. Never called on a blocked frame."""

    async def recognize(self, image_bytes: bytes) -> OcrResult: ...


class NoveltyStage(Protocol):
    """Two-tier gate. Jaccard on OCR tokens (free) then `fast` (cheap) if borderline.

    Implementations receive the current frame's OCR text and the previous event's
    OCR text + window, so they can decide merge-vs-new without the server holding
    extra state.
    """

    async def assess(
        self,
        ocr_text: str,
        prev_ocr_text: str | None,
        prev_window_key: str | None,
        current_window_key: str | None,
    ) -> NoveltyVerdict: ...


class PerceiveStage(Protocol):
    """Semantic understanding. Sees image + OCR full text + window metadata."""

    async def understand(
        self, image_bytes: bytes, ocr_full_text: str, app: str, window_title: str
    ) -> PerceiveEvent: ...


class EmbedStage(Protocol):
    """Vectorise text for the HNSW index. Query-side adds the instruction prefix
    (handbook §6.5); ingest-side embeds the plain activity+topics text."""

    async def embed(self, text: str) -> list[float]: ...


# --- Stub implementations (M3.2 default wiring) -----------------------------
# These let the full ingest path run with zero GPU. They are deliberately
# obvious fakes so test output cannot be confused with real inference.


class StubSentinel:
    async def classify(self, image_bytes: bytes) -> SentinelVerdict:
        # Always allow: the stub exists to exercise the pipeline, not to test
        # privacy behaviour. The real sentinel is wired in M3.4 / T2.1.
        return SentinelVerdict(
            decision="allow", category="normal", confidence=0.5
        )


class StubOcr:
    async def recognize(self, image_bytes: bytes) -> OcrResult:
        # Empty OCR is schema-valid and lets the rest of the pipeline proceed.
        # The real ocrd (M5.1) returns full_text + blocks with bbox.
        return OcrResult(full_text="", blocks=[])


class StubNovelty:
    async def assess(
        self,
        ocr_text: str,
        prev_ocr_text: str | None,
        prev_window_key: str | None,
        current_window_key: str | None,
    ) -> NoveltyVerdict:
        # No previous event, or a window change -> always a new event under the
        # stub. The real gate does Jaccard then `fast` (handbook §6.2 step 3).
        if prev_ocr_text is None or prev_window_key != current_window_key:
            return NoveltyVerdict(
                novelty=1.0, delta="stub: first frame or window change",
                merge_into_previous=False, tier="jaccard",
            )
        return NoveltyVerdict(
            novelty=0.0, delta="stub: same window, merging",
            merge_into_previous=True, tier="jaccard",
        )


class StubPerceive:
    async def understand(
        self, image_bytes: bytes, ocr_full_text: str, app: str, window_title: str
    ) -> PerceiveEvent:
        # A minimal but valid event. Real perceive fills activity/topics/verbatim
        # from image + OCR (handbook §6.2 step 4, verbatim from OCR only).
        return PerceiveEvent(
            activity=f"stub activity in {app or 'unknown'}",
            app_context="other",
            topics=[],
        )


class StubEmbed:
    async def embed(self, text: str) -> list[float]:
        # Deterministic placeholder vector so the DB write succeeds and the HNSW
        # index has something to store. Real embed (Qwen3-Embedding-0.6B, 1024
        # dims) is wired in M3.3/M3.4.
        return [0.0] * 1024


class GatewayEmbed:
    """Real embed stage backed by the LiteLLM gateway (Qwen3-Embedding-0.6B,
    1024-dim). Handbook §6.5: the QUERY side adds an instruction prefix
    (`Instruct: 检索用户活动时间线\\nQuery: …`) because Qwen3-Embedding is
    instruction-aware; the INGEST side embeds plain text. So callers must pass
    `instruct=True` for queries and leave it False when embedding events to
    store. The EmbedStage Protocol only has `embed(text)`, so this class also
    exposes `embed_query` for the asymmetric case; ingest uses `embed(text)`
    without prefix.
    """

    def __init__(self, gateway_url: str, *, timeout: float = 30.0) -> None:
        # Drop trailing /v1 if present so we control the path below.
        self._base = gateway_url.rstrip("/")
        if self._base.endswith("/v1"):
            self._base = self._base[:-3]
        self._timeout = timeout

    def _embed_sync(self, text: str) -> list[float]:
        import httpx
        url = f"{self._base}/v1/embeddings"
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(url, json={"model": "embed", "input": text})
            r.raise_for_status()
            data = r.json()
        return data["data"][0]["embedding"]

    async def embed(self, text: str) -> list[float]:
        # Ingest path: plain text, no instruction prefix (handbook §6.5).
        import asyncio
        return await asyncio.to_thread(self._embed_sync, text)

    async def embed_query(self, query: str) -> list[float]:
        # Query path: instruction-aware prefix (handbook §6.5). This is what
        # search.semantic / search.hybrid must use on the user query.
        import asyncio
        prefixed = f"Instruct: 检索用户活动时间线\nQuery: {query}"
        return await asyncio.to_thread(self._embed_sync, prefixed)
