"""Screen capture via mss (Quartz backend) -> in-memory WebP (handbook §5.2).

Pipeline:
  1. mss grabs the primary monitor at native (Retina) resolution as BGRA bytes.
  2. Pillow wraps those bytes into an RGB image.
  3. Image is scaled down proportionally so its width is <= max_upload_width
     (default 2560px) — keeps enough detail for OCR on small text while trimming
     the multi-megabyte payload of a full Retina frame.
  4. Encoded to WebP quality 80 entirely in memory. Nothing is written to disk;
     the bytes are handed to the uploader and then dropped.

A black-frame heuristic lives in `permissions.py`, not here: this module just
captures whatever the compositor returns, including an all-black frame when
Screen Recording permission is missing.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import imagehash
import mss
from PIL import Image

if TYPE_CHECKING:
    from capture.config import CaptureConfig


def capture_webp(
    config: "CaptureConfig",
    *,
    monitor_index: int = 1,
) -> tuple[bytes, int, int, imagehash.ImageHash]:
    """Capture the screen and return (webp_bytes, scaled_width, scaled_height,
    dhash).

    `monitor_index=1` is the primary display in mss's numbering (0 is the
    "all monitors combined" virtual display). Returns the encoded WebP bytes,
    the post-scale dimensions, and a dhash of the captured frame so the caller
    can deduplicate near-identical consecutive frames (handbook §5.2: distance
    < threshold -> drop).
    """
    with mss.mss() as sct:
        mon = sct.monitors[monitor_index]
        raw = sct.grab(mon)

    # raw is a BGRA bytearray the size of the monitor. Pillow consumes it
    # directly via frombytes with mode "RGBA" — mss lays pixels out as B,G,R,A
    # which Pillow's "BGRA" mode (added in 9.1) reads correctly.
    img = Image.frombytes("RGBA", raw.size, raw.bgra, "raw", "BGRA")
    img = img.convert("RGB")

    # Compute the dhash on the full-resolution capture (before scaling) — dhash
    # is already a coarse 8x8 thumbnail fingerprint, so scaling doesn't help and
    # would only lose fidelity for the dedup decision.
    frame_hash = imagehash.dhash(img)

    scaled = _scale_to_max_width(img, config.max_upload_width)

    buf = io.BytesIO()
    scaled.save(buf, format="WEBP", quality=config.webp_quality, method=4)
    return buf.getvalue(), scaled.width, scaled.height, frame_hash


def _scale_to_max_width(img: Image.Image, max_width: int) -> Image.Image:
    """Shrink `img` proportionally so width <= max_width. Never upscale."""
    w, h = img.size
    if max_width <= 0 or w <= max_width:
        return img
    new_h = max(1, round(h * (max_width / w)))
    # LANCZOS keeps text edges crisp for downstream OCR.
    return img.resize((max_width, new_h), Image.LANCZOS)
