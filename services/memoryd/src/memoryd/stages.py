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


def _to_png_if_needed(image_bytes: bytes) -> tuple[bytes, str]:
    """llama.cpp's vision backend only accepts PNG/JPEG. Capture encodes frames
    as WebP (handbook §5.2), so re-encode to PNG before sending to sentinel /
    perceive. Returns (png_bytes, mime)."""
    is_webp = image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP"
    if not is_webp:
        # Already png/jpeg/other -> sniff a mime, pass through.
        if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            return image_bytes, "image/png"
        if image_bytes[:3] == b"\xff\xd8\xff":
            return image_bytes, "image/jpeg"
        return image_bytes, "image/png"  # default assumption
    from PIL import Image
    from io import BytesIO
    with Image.open(BytesIO(image_bytes)) as img:
        img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), "image/png"


# Category-first prompt: MiniCPM-V often returns incomplete
# {"decision":"allow"} or disagrees decision↔category. We ask only for
# category (+ confidence) and derive decision in code (P3.6).
_SENTINEL_PROMPT = """Look at this screenshot. What privacy category is it?
Choose ONE label only from:
password_prompt, banking_finance, private_chat, id_document, adult, normal

Definitions:
- password_prompt: screen shows a password, PIN, or credentials entry field
- banking_finance: bank or finance app/page showing money balances, account numbers, cards, or transfers
- private_chat: private one-to-one messenger / DM conversation content
- id_document: passport, driver license, or government ID card
- adult: adult sexual imagery
- normal: everything else (code, terminal, documents, public websites, email lists, settings)

Answer with JSON only:
{"category":"<label>","confidence":0.0-1.0}
"""

_SENTINEL_SENSITIVE = frozenset(
    {"password_prompt", "banking_finance", "private_chat", "id_document", "adult"}
)


class GatewaySentinel:
    """Privacy gate backed by the `sentinel` model (MiniCPM-V 4.6) via the
    gateway. Category-first JSON prompt; enable_thinking=false (fast-track,
    handbook §6.2 step 1). Decision is derived from category so the model
    cannot block `normal` or allow a sensitive label."""

    def __init__(self, gateway_url: str, *, timeout: float = 180.0) -> None:
        self._base = gateway_url.rstrip("/").removesuffix("/v1")
        self._timeout = timeout

    async def classify(self, image_bytes: bytes) -> SentinelVerdict:
        # llama.cpp's vision backend only accepts PNG/JPEG — capture sends
        # WebP (handbook §5.2), so re-encode to PNG before base64-embedding.
        image_bytes, mime = _to_png_if_needed(image_bytes)
        b64 = base64.b64encode(image_bytes).decode("ascii")
        body = {
            "model": "sentinel",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": _SENTINEL_PROMPT},
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


def _normalize_sentinel_category(raw: object) -> str:
    """Map model category strings onto the closed taxonomy."""
    if not isinstance(raw, str):
        return "normal"
    cat = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if cat in _SENTINEL_CATS:
        return cat
    # Common near-misses from small VLMs.
    aliases = {
        "password": "password_prompt",
        "passwords": "password_prompt",
        "login": "password_prompt",
        "banking": "banking_finance",
        "finance": "banking_finance",
        "bank": "banking_finance",
        "chat": "private_chat",
        "dm": "private_chat",
        "message": "private_chat",
        "messenger": "private_chat",
        "id": "id_document",
        "passport": "id_document",
        "license": "id_document",
        "nsfw": "adult",
    }
    if cat in aliases:
        return aliases[cat]
    for known in _SENTINEL_CATS:
        if known in cat or cat in known:
            return known
    return "normal"


def _extract_json_objects(content: str) -> list[str]:
    """Pull JSON object candidates out of prose / markdown fences."""
    text = (content or "").strip()
    if not text:
        return []
    # Strip ```json ... ``` fences if present.
    fenced = re.sub(r"```(?:json)?\s*", "", text, flags=re.I)
    fenced = fenced.replace("```", "")
    candidates: list[str] = []
    for blob in (text, fenced):
        candidates.append(blob)
        for m in re.finditer(r"\{[^{}]*\}", blob, re.S):
            candidates.append(m.group(0))
    # De-dupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _parse_sentinel_json(content: str) -> SentinelVerdict:
    """Strict-ish JSON extraction with category→decision consistency (P3.6).

    MiniCPM-V often returns partial JSON (e.g. only ``decision``) or sets
    ``decision=block`` with ``category=normal``. We:
      1. parse whatever fields are present;
      2. normalise category onto the closed taxonomy;
      3. DERIVE decision from category (sensitive→block, normal→allow).

    Fail-open (allow/normal) only when nothing usable is found.
    """
    parsed: dict | None = None
    for cand in _extract_json_objects(content):
        try:
            # Tolerate trailing commas common in small-model JSON.
            cleaned = re.sub(r",\s*([}\]])", r"\1", cand)
            d = _json.loads(cleaned)
            if isinstance(d, dict):
                parsed = d
                break
        except (ValueError, TypeError):
            continue

    if parsed is None:
        # Last-resort: scan free text for a known category token.
        lowered = (content or "").lower()
        category = "normal"
        for cat in ("password_prompt", "banking_finance", "private_chat",
                    "id_document", "adult", "normal"):
            if cat in lowered:
                category = cat
                break
        decision = "block" if category in _SENTINEL_SENSITIVE else "allow"
        return SentinelVerdict(decision=decision, category=category, confidence=0.5)

    category = _normalize_sentinel_category(parsed.get("category", "normal"))
    if "confidence" in parsed:
        try:
            confidence = float(parsed.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.75
    else:
        # Category present without confidence — prefer a mid-high prior over the
        # old hard-coded 0.5 that made every audit row look like a parse fallback.
        confidence = 0.75
    confidence = max(0.0, min(1.0, confidence))
    # Category wins: never block normal, never allow a sensitive label.
    decision = "block" if category in _SENTINEL_SENSITIVE else "allow"
    return SentinelVerdict(decision=decision, category=category, confidence=confidence)


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


# Perceive prompt (P3.7): demand concrete activity; verbatim ⊆ OCR only.
_PERCEIVE_PROMPT = """Summarise what the user is doing in this screenshot.

Rules:
1. APP and WINDOW TITLE are system-provided — do NOT rewrite them.
2. OCR TEXT is the ONLY allowed source for verbatim entities — copy substrings
   exactly; never invent or retype text from the image pixels.
3. activity MUST be specific: verb + object + short context (e.g. "editing
   TimelineParser.parse_timeline in VS Code", "reading ROCM-4042 docs in
   Chrome"). FORBIDDEN vague lines: "working in X", "using X", "looking at
   the screen", "browsing".
4. Reply with ONLY compact JSON (no markdown, no prose):
{"activity":"<specific one line>",
 "app_context":"ide|browser|terminal|chat|docs|other",
 "topics":["..."],
 "verbatim":{"errors":[],"urls":[],"identifiers":[],"numbers":[],"quotes":[]}}
5. Leave a verbatim list empty when OCR has nothing for that bucket.
Image is for layout/visual context only.
"""

# Vague one-liners we refuse to store (P3.7). Keep this narrow — the OCR
# fallback itself uses "inspecting «…»" and must NOT match.
_GENERIC_ACTIVITY_RE = re.compile(
    r"^\s*(working in|using|looking at the screen|browsing)\b[\w ._-]*$",
    re.I,
)


class GatewayPerceive:
    """Semantic understanding backed by `perceive` (Gemma 4 E4B). Handbook §6.2
    step 4: verbatim entities MUST come from OCR text only; app/title are
    system-injected. Thinking stays ON for activity synthesis; we budget
    tokens. Post-parse filters drop any verbatim string not found in OCR and
    replace empty/generic activity with an OCR-grounded fallback (P3.7)."""

    def __init__(self, gateway_url: str, *, timeout: float = 240.0) -> None:
        self._base = gateway_url.rstrip("/").removesuffix("/v1")
        self._timeout = timeout

    async def understand(self, image_bytes, ocr_full_text, app, window_title) -> PerceiveEvent:
        # Re-encode WebP -> PNG (llama.cpp vision only accepts PNG/JPEG).
        image_bytes, _mime = _to_png_if_needed(image_bytes)
        b64 = base64.b64encode(image_bytes).decode("ascii")
        body = {
            "model": "perceive",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": (
                    f"{_PERCEIVE_PROMPT}\n\nAPP: {app}\nWINDOW TITLE: {window_title}\n"
                    f"OCR TEXT:\n{ocr_full_text[:3000]}"
                )},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}],
            "max_tokens": 500,
            "temperature": 0.1,
            # Structured JSON is more reliable with thinking off (P3.7); activity
            # specificity is enforced by the prompt + non-generic fallback.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        def _call():
            last = None
            for _ in range(3):
                try:
                    with httpx.Client(timeout=self._timeout) as c:
                        r = c.post(f"{self._base}/v1/chat/completions", json=body)
                        r.raise_for_status()
                        msg = r.json()["choices"][0]["message"]
                        content = msg.get("content", "") or ""
                        if not content.strip():
                            # Some builds still park the answer in reasoning_content.
                            content = msg.get("reasoning_content", "") or ""
                        return content
                except (httpx.ReadTimeout, httpx.ConnectError) as exc:
                    last = exc
                    continue
            raise last
        content = await asyncio.to_thread(_call)
        return _parse_perceive_json(content, app, window_title, ocr_full_text)


def _activity_fallback(app: str, window_title: str, ocr_full_text: str) -> str:
    """Concrete-ish fallback when the model returns empty/generic activity."""
    app_l = (app or "").strip() or "unknown app"
    title = (window_title or "").strip()
    # Prefer a short OCR line that looks informative.
    for line in (ocr_full_text or "").splitlines():
        s = line.strip()
        if len(s) >= 12 and not s.startswith("http"):
            snippet = s[:120]
            if title:
                return f"inspecting «{snippet}» in {app_l} ({title[:60]})"
            return f"inspecting «{snippet}» in {app_l}"
    if title:
        return f"active in {app_l}: {title[:120]}"
    return f"active in {app_l}"


def _filter_verbatim_to_ocr(items: list, ocr_full_text: str) -> list[str]:
    """Keep only strings that appear as substrings of OCR (case-sensitive first,
    then case-insensitive). Drops hallucinations (handbook §6.2 step 4)."""
    if not ocr_full_text:
        return []
    ocr_lower = ocr_full_text.lower()
    out: list[str] = []
    seen: set[str] = set()
    for raw in items or []:
        s = str(raw).strip()
        if not s or s in seen:
            continue
        if s in ocr_full_text or s.lower() in ocr_lower:
            out.append(s)
            seen.add(s)
    return out[:20]


def _parse_perceive_json(
    content: str,
    app: str,
    window_title: str,
    ocr_full_text: str = "",
) -> PerceiveEvent:
    """Parse perceive JSON; enforce verbatim ⊆ OCR and non-generic activity."""
    data: dict | None = None
    # Prefer the largest {...} blob (verbatim object is nested).
    candidates: list[str] = []
    m = re.search(r"\{.*\}", content or "", re.S)
    if m:
        candidates.append(m.group(0))
    candidates.extend(_extract_json_objects(content or ""))
    for cand in candidates:
        try:
            cleaned = re.sub(r",\s*([}\]])", r"\1", cand)
            d = _json.loads(cleaned)
            if isinstance(d, dict) and (
                "activity" in d or "verbatim" in d or "app_context" in d
            ):
                data = d
                break
        except (ValueError, TypeError):
            continue

    if data is None:
        return PerceiveEvent(
            activity=_activity_fallback(app, window_title, ocr_full_text),
            app_context="other",
            topics=[],
        )

    ctx = data.get("app_context", "other")
    if ctx not in ("ide", "browser", "terminal", "chat", "docs", "other"):
        ctx = "other"
    vb = data.get("verbatim", {}) or {}
    if not isinstance(vb, dict):
        vb = {}
    activity = str(data.get("activity", "") or "").strip()[:300]
    if not activity or _GENERIC_ACTIVITY_RE.match(activity):
        activity = _activity_fallback(app, window_title, ocr_full_text)

    return PerceiveEvent(
        activity=activity,
        app_context=ctx,
        topics=[str(t) for t in (data.get("topics") or []) if str(t).strip()][:10],
        verbatim=Verbatim(
            errors=_filter_verbatim_to_ocr(vb.get("errors", []), ocr_full_text),
            urls=_filter_verbatim_to_ocr(vb.get("urls", []), ocr_full_text),
            identifiers=_filter_verbatim_to_ocr(vb.get("identifiers", []), ocr_full_text),
            numbers=_filter_verbatim_to_ocr(vb.get("numbers", []), ocr_full_text),
            quotes=_filter_verbatim_to_ocr(vb.get("quotes", []), ocr_full_text),
        ),
    )
