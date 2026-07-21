"""Pydantic models for the ingest contract (handbook §5.3) and pipeline stages.

These types are the stable boundary between the capture client, memoryd, and the
timeline DB. They mirror the timeline_events DDL (deploy/mac/timeline-init.sql):
`verbatim`, `ocr_blocks`, `topics` etc. flow straight into columns.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --- sentinel ---------------------------------------------------------------
SentinelCategory = Literal[
    "password_prompt",
    "banking_finance",
    "private_chat",
    "id_document",
    "adult",
    "normal",
]
SentinelDecision = Literal["allow", "block"]


class SentinelVerdict(BaseModel):
    """Output of the privacy sentinel on one frame (handbook §6.2 step 1).

    A `block` decision MUST prevent the image from reaching OCR or disk; only
    the category is recorded in sentinel_audit, never the pixels.
    """

    decision: SentinelDecision
    category: SentinelCategory
    confidence: float = Field(ge=0.0, le=1.0)


# --- OCR --------------------------------------------------------------------
class OcrBlock(BaseModel):
    text: str
    bbox: list[float] = Field(min_length=4, max_length=4)  # [x1, y1, x2, y2]
    conf: float = Field(ge=0.0, le=1.0)


class OcrResult(BaseModel):
    """Deterministic verbatim layer from ocrd (PP-OCRv6, handbook §6.1)."""

    full_text: str
    blocks: list[OcrBlock] = Field(default_factory=list)


# --- novelty gate -----------------------------------------------------------
class NoveltyVerdict(BaseModel):
    """Output of the two-tier novelty gate (handbook §6.2 step 3).

    `merge_into_previous=True` means this frame extends the last event's
    `end_ts` instead of creating a new timeline_events row.
    """

    novelty: float = Field(ge=0.0, le=1.0)
    delta: str  # one-line description of what changed
    merge_into_previous: bool
    tier: Literal["jaccard", "fast"]  # which tier made the call (free vs cheap)


# --- perceive ---------------------------------------------------------------
AppContext = Literal[
    "ide", "browser", "terminal", "chat", "docs", "other"
]


class Verbatim(BaseModel):
    """Entities extracted from OCR text only (handbook §6.2 step 4 rule).

    The perceive prompt MUST source these from OCR text, never transcribe the
    image — this is the anti-hallucination guardrail. Defaults empty so a stub
    is schema-valid.
    """

    errors: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)


class PerceiveEvent(BaseModel):
    """Semantic understanding of one frame (handbook §6.2 step 4)."""

    activity: str  # one line: what the user is doing
    app_context: AppContext
    topics: list[str] = Field(default_factory=list)
    verbatim: Verbatim = Field(default_factory=Verbatim)


# --- ingest payloads (handbook §5.3) ---------------------------------------
class FrameMeta(BaseModel):
    device_id: str
    ts: str  # ISO-8601; capture client owns the clock
    app: str | None = None
    window_title: str | None = None
    url: str | None = None
    trigger: Literal["change", "periodic"] = "change"


class AudioMeta(BaseModel):
    device_id: str
    ts_start: str
    ts_end: str


class DocMeta(BaseModel):
    source_path: str
    tags: list[str] = Field(default_factory=list)


class IngestAck(BaseModel):
    """202 Accepted reply — the frame passed the gateway and is queued."""

    accepted: bool = True
    event_id: int | None = None  # set when a new timeline_events row was created
    merged_into: int | None = None  # set when novelty gate merged into previous
    sentinel: SentinelVerdict | None = None  # present for frame ingest
    note: str = ""
