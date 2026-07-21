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
    OcrBlock,
    OcrResult,
    PerceiveEvent,
    SentinelVerdict,
    Verbatim,
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


# --- Real implementations (M3.4 wiring) ------------------------------------
# These back the pipeline with the actual Metal inference stack via the gateway.
# MiniCPM / Gemma are reasoning models (verification-log 2026-07-21 M2.5), so
# the fast-track stages (sentinel, novelty) pass enable_thinking=false; the
# perceive stage leaves thinking on (it needs the reasoning for activity
# synthesis) and budgets tokens for it.

import asyncio
import base64
import json as _json
import re

import httpx

# Sentinel taxonomy must match timeline_events.sentinel_audit.category CHECK.
_SENTINEL_CATS = {"password_prompt", "banking_finance", "private_chat",
                  "id_document", "adult", "normal"}


class GatewaySentinel:
    """Privacy gate backed by the `sentinel` model (MiniCPM-V 4.6) via the
    gateway. Sends the image with a strict-JSON classification prompt;
    enable_thinking=false because classification is a fast-track judgment
    (handbook §6.2 step 1)."""

    def __init__(self, gateway_url: str, *, timeout: float = 180.0) -> None:
        self._base = gateway_url.rstrip("/").removesuffix("/v1")
        self._timeout = timeout

    async def classify(self, image_bytes: bytes) -> SentinelVerdict:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        # Sniff a plausible mime from the magic bytes; default png.
        mime = "image/png"
        if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            mime = "image/png"
        elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            mime = "image/webp"
        elif image_bytes[:3] == b"\xff\xd8\xff":
            mime = "image/jpeg"
        prompt = (
            "You are a privacy sentinel. Classify this screenshot for whether it "
            "should be remembered. Reply ONLY with compact JSON, no prose, exactly "
            "this schema: "
            '{"decision":"allow|block","category":"password_prompt|banking_finance'
            '|private_chat|id_document|adult|normal","confidence":0.0-1.0}. '
            "Block password fields, banking/finance pages with account numbers or "
            "balances, private DMs, ID documents, adult content. Allow normal "
            "code/docs/terminal/public webpages."
        )
        body = {
            "model": "sentinel",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}],
            "max_tokens": 80,
            "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        def _call():
            # Retry on transient tunnel errors (image payload + SSH tunnel jitter).
            last = None
            for _ in range(3):
                try:
                    with httpx.Client(timeout=self._timeout) as c:
                        r = c.post(f"{self._base}/v1/chat/completions", json=body)
                        r.raise_for_status()
                        return r.json()["choices"][0]["message"].get("content", "") or ""
                except (httpx.ReadTimeout, httpx.ConnectError) as exc:
                    last = exc
                    continue
            raise last
        content = await asyncio.to_thread(_call)
        return _parse_sentinel_json(content)


def _parse_sentinel_json(content: str) -> SentinelVerdict:
    """Tolerant JSON extraction: models sometimes wrap JSON in prose or fences.
    Defaults to allow/normal/0.5 on any parse failure so a malformed reply never
    silently blocks everything (fail-open for usability; the audit log still
    records the decision)."""
    # Try a direct parse first, then a fenced {...} extraction.
    candidates = [content]
    m = re.search(r"\{[^{}]*\}", content, re.S)
    if m:
        candidates.append(m.group(0))
    for cand in candidates:
        try:
            d = _json.loads(cand)
            decision = d.get("decision", "allow")
            category = d.get("category", "normal")
            confidence = float(d.get("confidence", 0.5))
            if decision not in ("allow", "block"):
                decision = "allow"
            if category not in _SENTINEL_CATS:
                category = "normal"
            confidence = max(0.0, min(1.0, confidence))
            return SentinelVerdict(decision=decision, category=category, confidence=confidence)
        except (ValueError, TypeError):
            continue
    return SentinelVerdict(decision="allow", category="normal", confidence=0.5)


class OcrdClient:
    """OCR stage backed by the ocrd microservice (PP-OCRv4/v6, handbook §6.1).
    ocrd is deterministic, not an LLM — memoryd reaches it directly via OCR_URL,
    NOT through the LiteLLM gateway."""

    def __init__(self, ocr_url: str, *, timeout: float = 30.0) -> None:
        self._url = ocr_url.rstrip("/").removesuffix("/ocr") + "/ocr"
        self._timeout = timeout

    async def recognize(self, image_bytes: bytes) -> OcrResult:
        def _call():
            with httpx.Client(timeout=self._timeout) as c:
                r = c.post(self._url, files={"file": ("frame.png", image_bytes, "image/png")})
                r.raise_for_status()
                return r.json()
        data = await asyncio.to_thread(_call)
        return OcrResult(
            full_text=data.get("full_text", ""),
            blocks=[OcrBlock(**b) for b in data.get("blocks", [])],
        )


class RealNovelty:
    """Two-tier novelty gate (handbook §6.2 step 3).

    Tier 1 (free): Jaccard on whitespace-token sets of the OCR text.
      - > 0.85 -> merge (same event, just cursor blink / clock tick)
      - < 0.5  -> new event (clearly different content)
      - 0.5–0.85 (borderline) -> tier 2
    Tier 2 (cheap): ask `fast` (MiniCPM5, no-think) with a JSON prompt for a
      novelty score + one-line delta. < 0.35 -> merge.
    Only same-window frames are compared (app + window_title match).
    """

    def __init__(self, gateway_url: str, *, timeout: float = 30.0,
                 merge_threshold: float = 0.85, new_threshold: float = 0.5,
                 fast_merge_novelty: float = 0.35) -> None:
        self._base = gateway_url.rstrip("/").removesuffix("/v1")
        self._timeout = timeout
        self._merge = merge_threshold
        self._new = new_threshold
        self._fast_merge = fast_merge_novelty

    @staticmethod
    def _jaccard(a: str, b: str) -> float:
        sa = set(a.lower().split())
        sb = set(b.lower().split())
        if not sa and not sb:
            return 1.0
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    async def assess(self, ocr_text, prev_ocr_text, prev_window_key, current_window_key):
        # Different window -> always new (no comparison possible/useful).
        if prev_window_key != current_window_key or prev_ocr_text is None:
            return NoveltyVerdict(novelty=1.0, delta="window change or first frame",
                                  merge_into_previous=False, tier="jaccard")
        j = self._jaccard(ocr_text, prev_ocr_text)
        if j >= self._merge:
            return NoveltyVerdict(novelty=1.0 - j, delta=f"jaccard {j:.2f} >= {self._merge}",
                                  merge_into_previous=True, tier="jaccard")
        if j < self._new:
            return NoveltyVerdict(novelty=1.0 - j, delta=f"jaccard {j:.2f} < {self._new}",
                                  merge_into_previous=False, tier="jaccard")
        # Borderline -> tier 2 (fast model).
        novelty, delta = await self._fast_assess(ocr_text, prev_ocr_text)
        merge = novelty < self._fast_merge
        return NoveltyVerdict(novelty=novelty, delta=delta,
                              merge_into_previous=merge, tier="fast")

    async def _fast_assess(self, cur: str, prev: str) -> tuple[float, str]:
        # Truncate to keep the prompt small; novelty is about the gist, not the
        # full text.
        prompt = (
            "Compare two consecutive screen captures (as OCR text) from the same "
            "window. How novel is the second vs the first? Reply ONLY with JSON: "
            '{"novelty":0.0-1.0,"delta":"one-line description of what changed"}. '
            "novelty 0 = identical/ trivial, 1 = totally new content."
        )
        body = {
            "model": "fast",
            "messages": [{"role": "user", "content": (
                f"{prompt}\n\nPREVIOUS (truncated):\n{prev[:1200]}\n\n"
                f"CURRENT (truncated):\n{cur[:1200]}"
            )}],
            "max_tokens": 120,
            "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        def _call():
            with httpx.Client(timeout=self._timeout) as c:
                r = c.post(f"{self._base}/v1/chat/completions", json=body)
                r.raise_for_status()
                return r.json()["choices"][0]["message"].get("content", "") or ""
        content = await asyncio.to_thread(_call)
        m = re.search(r"\{[^{}]*\}", content, re.S)
        if m:
            try:
                d = _json.loads(m.group(0))
                return float(d.get("novelty", 0.5)), str(d.get("delta", ""))[:120]
            except (ValueError, TypeError):
                pass
        return 0.5, "fast parse failed; defaulting to new"


class GatewayPerceive:
    """Semantic understanding backed by `perceive` (Gemma 4 E4B). Handbook §6.2
    step 4: the prompt MUST source verbatim entities from OCR text only (never
    transcribe the image), and MUST NOT rewrite the app/title metadata (system-
    injected). Thinking is left ON because activity synthesis benefits from it;
    we budget enough tokens."""

    def __init__(self, gateway_url: str, *, timeout: float = 240.0) -> None:
        self._base = gateway_url.rstrip("/").removesuffix("/v1")
        self._timeout = timeout

    async def understand(self, image_bytes, ocr_full_text, app, window_title) -> PerceiveEvent:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        prompt = (
            "You are summarising what the user is doing in this screenshot. The "
            "window metadata (app, title) is system-provided — do NOT change it. "
            "The OCR text below is the ground truth for any verbatim entities; do "
            "NOT transcribe text from the image yourself. Reply ONLY with compact "
            "JSON, no prose: "
            '{"activity":"one line: what the user is doing",'
            '"app_context":"ide|browser|terminal|chat|docs|other",'
            '"topics":["..."],"verbatim":{"errors":[],"urls":[],'
            '"identifiers":[],"numbers":[],"quotes":[]}}. '
            "Populate verbatim ONLY from the OCR text. image is for layout/visual "
            "context only."
        )
        body = {
            "model": "perceive",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": (
                    f"{prompt}\n\nAPP: {app}\nWINDOW TITLE: {window_title}\n"
                    f"OCR TEXT:\n{ocr_full_text[:3000]}"
                )},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}],
            "max_tokens": 600,
            "temperature": 0.1,
        }
        def _call():
            last = None
            for _ in range(3):
                try:
                    with httpx.Client(timeout=self._timeout) as c:
                        r = c.post(f"{self._base}/v1/chat/completions", json=body)
                        r.raise_for_status()
                        return r.json()["choices"][0]["message"].get("content", "") or ""
                except (httpx.ReadTimeout, httpx.ConnectError) as exc:
                    last = exc
                    continue
            raise last
        content = await asyncio.to_thread(_call)
        return _parse_perceive_json(content, app, window_title)


def _parse_perceive_json(content: str, app: str, window_title: str) -> PerceiveEvent:
    m = re.search(r"\{.*\}", content, re.S)
    if m:
        try:
            d = _json.loads(m.group(0))
            ctx = d.get("app_context", "other")
            if ctx not in ("ide", "browser", "terminal", "chat", "docs", "other"):
                ctx = "other"
            vb = d.get("verbatim", {}) or {}
            return PerceiveEvent(
                activity=str(d.get("activity", ""))[:300] or f"working in {app or 'unknown'}",
                app_context=ctx,
                topics=[str(t) for t in d.get("topics", [])][:10],
                verbatim=Verbatim(
                    errors=[str(x) for x in vb.get("errors", [])],
                    urls=[str(x) for x in vb.get("urls", [])],
                    identifiers=[str(x) for x in vb.get("identifiers", [])],
                    numbers=[str(x) for x in vb.get("numbers", [])],
                    quotes=[str(x) for x in vb.get("quotes", [])],
                ),
            )
        except (ValueError, TypeError):
            pass
    # Fallback: minimal valid event derived from metadata (no hallucinated text).
    return PerceiveEvent(
        activity=f"working in {app or 'unknown'}",
        app_context="other",
        topics=[],
    )
