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

import io
from dataclasses import dataclass
from PIL import Image

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
        verdict: SentinelVerdict = await self.sentinel.classify(image_bytes)
        self.store.write_sentinel_audit(
            ts=meta.ts, device_id=meta.device_id, verdict=verdict
        )
        if verdict.decision == "block":
            return IngestAck(
                accepted=False,
                sentinel=verdict,
                note=f"blocked by sentinel ({verdict.category}); image discarded, "
                     f"not OCR'd, not stored",
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

        # Write the screenshot to DATA_ROOT (only for ALLOWED frames).
        screenshot_target = self.store.screenshot_target(
            device_id=meta.device_id, ts=meta.ts
        )
        # Re-encode as WebP per handbook §5.2 (<=1600px, quality 80). Downscaling
        # is intentionally omitted here; M3.4 / capture client owns final sizing.
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img.save(screenshot_target, format="WEBP", quality=80)
            screenshot_path = str(screenshot_target)
        except Exception:
            # If the bytes aren't a decodable image (e.g. a stub test payload),
            # skip the screenshot write but still record the event.
            screenshot_path = None

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
        )

        # Step 6 — Honcho throttled flush is stubbed in M3.2 (no-op); M2.6 wires
        # the real deriver message via the gateway.
        return IngestAck(
            accepted=True, event_id=event_id, sentinel=verdict,
            note=f"new event {event_id} ({event.activity})",
        )
