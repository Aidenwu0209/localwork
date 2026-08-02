#!/usr/bin/env python3
"""Render OCR bbox evidence for one isolated synthetic demo event."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg
from PIL import Image, ImageDraw

DEFAULT_DSN = "postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview_demo"
DEFAULT_DATA_ROOT = Path("/tmp/dejaview-p34-data").resolve()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-id", required=True, type=int)
    parser.add_argument("--highlight-text", required=True)
    parser.add_argument("--device-id", default="demo-p34")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.device_id.startswith("demo-"):
        parser.error("device-id must start with 'demo-' to prevent real-data export")
    return args


def main() -> int:
    args = _parse_args()
    if os.environ.get("DEJAVIEW_DEMO_MODE") != "1":
        raise SystemExit("DEJAVIEW_DEMO_MODE=1 is required")
    dsn = os.environ.get("TIMELINE_DB_URL", DEFAULT_DSN)
    if dsn != DEFAULT_DSN:
        raise SystemExit("TIMELINE_DB_URL must target local database dejaview_demo")
    data_root = (
        Path(os.environ.get("DATA_ROOT", DEFAULT_DATA_ROOT)).expanduser().resolve()
    )
    if data_root != DEFAULT_DATA_ROOT:
        raise SystemExit(f"DATA_ROOT must be {DEFAULT_DATA_ROOT}")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        if cur.fetchone()[0] != "dejaview_demo":
            raise SystemExit("connected database is not dejaview_demo")
        cur.execute(
            """
            SELECT screenshot_path, ocr_blocks
            FROM timeline_events
            WHERE id = %s AND device_id = %s
            """,
            (args.event_id, args.device_id),
        )
        row = cur.fetchone()
    if row is None:
        raise SystemExit("synthetic demo event not found")

    screenshot_path = Path(row[0] or "").expanduser().resolve()
    if not screenshot_path.is_relative_to(data_root):
        raise SystemExit("refusing screenshot path outside DATA_ROOT")
    if not screenshot_path.is_file():
        raise SystemExit(f"screenshot missing: {screenshot_path}")

    needle = args.highlight_text.casefold()
    highlights = [
        block
        for block in (row[1] or [])
        if needle in str(block.get("text", "")).casefold()
    ]
    if not highlights:
        raise SystemExit(f"no OCR bbox contains: {args.highlight_text!r}")

    with Image.open(screenshot_path) as source:
        image = source.convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    line_width = max(4, round(max(image.size) / 260))
    for block in highlights:
        bbox = [round(float(value)) for value in block.get("bbox", [])]
        if len(bbox) != 4:
            continue
        overlay_draw.rectangle(
            bbox,
            fill=(255, 59, 48, 35),
            outline=(255, 59, 48, 255),
            width=line_width,
        )
    rendered = Image.alpha_composite(image, overlay).convert("RGB")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered.save(args.output, format="PNG")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
