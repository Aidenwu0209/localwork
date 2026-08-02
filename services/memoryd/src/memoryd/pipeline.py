"""Frame ingestion pipeline (handbook §6.2).

Steps per frame:
  1. sentinel  -> block? write audit, STOP (image never reaches OCR/disk)
  2. ocrd      -> deterministic full_text + blocks
  3. novelty   -> Jaccard then `fast`; merge into previous event if below threshold
  4. perceive  -> activity/topics/verbatim (verbatim from OCR text only)
  5. store     -> screenshot under DATA_ROOT, timeline_events row w/ embedding
  6. honcho    -> throttled batch (every N events or M seconds)  [stubbed here]

Each stage is injected (Protocol in stages.py), so M3.4 swaps stubs for real
gateway-backed implementations without touching this orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass

from memoryd.models import (
    IngestAck,
    FrameMeta,
    SentinelVerdict,
)
from memoryd.stages import (
    EmbedStage,
    NoveltyStage,
    OcrStage,
    PerceiveStage,
    SentinelStage,
)
from memoryd.storage import TimelineStore


@dataclass
class Pipeline:
    sentinel: SentinelStage
    ocr: OcrStage
    novelty: NoveltyStage
    perceive: PerceiveStage
    embed: EmbedStage
    store: TimelineStore

    async def ingest_frame(
        self, image_bytes: bytes, meta: FrameMeta
    ) -> IngestAck:
        # Step 1 — privacy gate. Always audit (allow AND block); on block, the
        # image is dropped here and never reaches OCR or disk (handbook §6.2.1).
        try:
            verdict: SentinelVerdict = await self.sentinel.classify(image_bytes)
        except Exception:
            verdict = SentinelVerdict(
                decision="block",
                category="normal",
                confidence=0.0,
                reason="sentinel_unavailable",
            )
        self.store.write_sentinel_audit(
            ts=meta.ts, device_id=meta.device_id, verdict=verdict
        )
        if verdict.decision == "block":
            return IngestAck(
                processing_state="blocked",
                accepted=False,
                sentinel=verdict,
                note="blocked by privacy sentinel; image discarded, not OCR'd, not stored",
            )

        # Step 2 — deterministic verbatim OCR.
        ocr = await self.ocr.recognize(image_bytes)

        # Step 3 — novelty gate. Fetch the previous event in the same window and
        # ask the gate whether to merge or create new.
        prev_id, prev_ocr_text = self.store.fetch_last_event_ocr(
            device_id=meta.device_id, app=meta.app
        )
        window_key = f"{meta.app}|{meta.window_title}"
        # prev_window_key isn't stored separately; under M3.2 the stub keys off
        # app only, so we pass app as the window key for both sides.
        novelty = await self.novelty.assess(
            ocr_text=ocr.full_text,
            prev_ocr_text=prev_ocr_text,
            prev_window_key=meta.app,
            current_window_key=meta.app,
        )
        if novelty.merge_into_previous and prev_id is not None:
            self.store.merge_into_previous(event_id=prev_id, ts=meta.ts)
            return IngestAck(
                processing_state="merged",
                accepted=True,
                merged_into=prev_id,
                sentinel=verdict,
                note=f"merged into event {prev_id}: {novelty.delta}",
            )

        # Step 4 — semantic understanding (verbatim sourced from OCR text only).
        event = await self.perceive.understand(
            image_bytes=image_bytes,
            ocr_full_text=ocr.full_text,
            app=meta.app or "",
            window_title=meta.window_title or "",
        )

        # Step 5 — embed the activity+topics text and persist.
        embed_input = " ".join([event.activity, *event.topics]).strip() or event.activity
        vector = await self.embed.embed(embed_input)

        # The storage boundary validates the device id, creates a contained
        # no-symlink directory chain, and atomically publishes the WebP. Invalid
        # synthetic image bytes retain the historical no-screenshot behavior.
        screenshot_target = self.store.write_screenshot(
            device_id=meta.device_id,
            ts=meta.ts,
            image_bytes=image_bytes,
        )
        screenshot_path = str(screenshot_target) if screenshot_target is not None else None

        event_id = self.store.insert_event(
            ts=meta.ts,
            device_id=meta.device_id,
            kind="frame",
            app=meta.app,
            window_title=meta.window_title,
            url=meta.url,
            activity=event.activity,
            topics=event.topics,
            verbatim=event.verbatim.model_dump(),
            ocr_text=ocr.full_text,
            ocr_blocks=[b.model_dump() for b in ocr.blocks],
            screenshot_path=screenshot_path,
            embedding=vector,
            app_context=event.app_context,
        )

        # Step 6 — Honcho throttled flush is stubbed in M3.2 (no-op); M2.6 wires
        # the real deriver message via the gateway.
        return IngestAck(
            processing_state="stored",
            accepted=True, event_id=event_id, sentinel=verdict,
            note=f"new event {event_id} ({event.activity})",
        )
