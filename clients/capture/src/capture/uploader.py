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
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from capture.config import CaptureConfig


log = logging.getLogger("capture.uploader")

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
) -> bool:
    """POST one frame; return True if memoryd accepted it (HTTP 2xx).

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
        if 200 <= resp.status_code < 300:
            log.debug(
                "frame uploaded: trigger=%s app=%s title=%r -> %s %s",
                trigger, app, window_title, resp.status_code, resp.text[:200],
            )
            return True
        log.warning(
            "memoryd returned %s for frame (trigger=%s): %s",
            resp.status_code, trigger, resp.text[:200],
        )
        return False
    except (httpx.HTTPError, OSError) as exc:
        log.debug("frame upload failed (dropped, not cached): %s", exc)
        return False
    finally:
        if owns_client:
            await client.aclose()
