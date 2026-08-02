"""Async frame uploader to memoryd's /v1/ingest/frame (handbook §5.3).

POSTs a multipart/form-data request with:
  - file:  the WebP bytes (filename frame.webp, content-type image/webp)
  - meta:  JSON string matching memoryd.models.FrameMeta

Failure policy (handbook §5.2: "POST 完即丢,客户端磁盘零残留"):
  - On any network/HTTP error, the frame is dropped silently. Nothing is cached
    to disk; the next frame starts fresh.
  - Uploads run with a short timeout so a hung memoryd can't stall the loop.

The uploader never touches the filesystem — it holds the bytes in memory only
long enough to hand them to httpx, then drops them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

import httpx

if TYPE_CHECKING:
    from capture.config import CaptureConfig


log = logging.getLogger("capture.uploader")

UploadState = Literal["stored", "merged", "blocked", "failed"]


@dataclass(frozen=True)
class UploadOutcome:
    state: UploadState
    event_id: int | None = None
    merged_into: int | None = None
    error_code: str | None = None

# Shapes memoryd's FrameMeta exactly (services/memoryd/src/memoryd/models.py).
# Keep these keys in sync if memoryd's model changes.
def build_meta(
    *,
    device_id: str,
    app: str | None,
    window_title: str | None,
    url: str | None,
    trigger: str,
) -> str:
    """Serialize the FrameMeta JSON string. `ts` is ISO-8601 UTC, captured now."""
    ts = datetime.now(timezone.utc).isoformat()
    meta = {
        "device_id": device_id,
        "ts": ts,
        "app": app,
        "window_title": window_title,
        "url": url,
        "trigger": trigger,
    }
    return json.dumps(meta)


async def upload_frame(
    config: "CaptureConfig",
    *,
    webp_bytes: bytes,
    app: str | None,
    window_title: str | None,
    url: str | None,
    trigger: str,
    client: httpx.AsyncClient | None = None,
) -> UploadOutcome:
    """POST one frame and return a closed terminal outcome.

    `client` may be passed in so the caller reuses a pooled connection across
    frames. If omitted, a short-lived client is created for this call.

    Any error is logged at debug and swallowed — the caller keeps the loop
    running and the frame is gone (privacy invariant: never cached to disk).
    """
    meta_json = build_meta(
        device_id=config.device_id,
        app=app,
        window_title=window_title,
        url=url,
        trigger=trigger,
    )

    files = {"file": ("frame.webp", webp_bytes, "image/webp")}
    data = {"meta": meta_json}

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0))

    try:
        resp = await client.post(config.frame_endpoint, files=files, data=data)
        if not 200 <= resp.status_code < 300:
            return UploadOutcome(state="failed", error_code="http_error")
        try:
            body = resp.json()
        except (ValueError, TypeError):
            return UploadOutcome(state="failed", error_code="invalid_response")
        return _parse_outcome(body)
    except (httpx.HTTPError, OSError):
        log.debug("frame upload failed (dropped, not cached)")
        return UploadOutcome(state="failed", error_code="transport_error")
    finally:
        if owns_client:
            await client.aclose()


def _parse_outcome(body: object) -> UploadOutcome:
    if not isinstance(body, dict):
        return UploadOutcome(state="failed", error_code="invalid_response")
    state = body.get("processing_state")
    accepted = body.get("accepted")
    event_id = body.get("event_id")
    merged_into = body.get("merged_into")
    if state == "stored" and accepted is True and isinstance(event_id, int) and not isinstance(event_id, bool) and merged_into is None:
        return UploadOutcome(state="stored", event_id=event_id)
    if state == "merged" and accepted is True and event_id is None and isinstance(merged_into, int) and not isinstance(merged_into, bool):
        return UploadOutcome(state="merged", merged_into=merged_into)
    if state == "blocked" and accepted is False and event_id is None and merged_into is None:
        return UploadOutcome(state="blocked")
    return UploadOutcome(state="failed", error_code="invalid_response")
