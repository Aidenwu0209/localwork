#!/usr/bin/env python3
"""Seed an isolated, synthetic P3.4 timeline and its screenshot evidence."""

from __future__ import annotations

import json
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import psycopg
from PIL import Image, ImageDraw, ImageFont
from psycopg.types.json import Jsonb

DEVICE_ID = "demo-p34"
TZ = timezone(timedelta(hours=8))
DEFAULT_DSN = "postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview_demo"
DEFAULT_DATA_ROOT = Path("/tmp/dejaview-p34-data").resolve()


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/SFNSMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _make_pr_screenshot(path: Path) -> list[dict]:
    """Render an original synthetic code-review page and return OCR blocks."""

    width, height = 1600, 900
    image = Image.new("RGB", (width, height), "#f5f7fb")
    draw = ImageDraw.Draw(image)
    title_font = _font(38, bold=True)
    body_font = _font(25)
    mono_font = _font(23)
    small_font = _font(20)

    draw.rectangle((0, 0, width, 84), fill="#151b2b")
    draw.text((48, 23), "ACME CODE REVIEW", font=_font(28, bold=True), fill="white")
    draw.rounded_rectangle(
        (52, 122, 1548, 820), radius=16, fill="white", outline="#d8deea"
    )

    draw.text((96, 158), "rocm-lab / pull / 1842", font=small_font, fill="#536079")
    draw.text(
        (96, 215),
        "PR #1842  Enable MTP batching on gfx1100",
        font=title_font,
        fill="#172033",
    )
    draw.rounded_rectangle((98, 278, 232, 322), radius=18, fill="#daf5e5")
    draw.text((125, 287), "OPEN", font=small_font, fill="#137a43")
    draw.text(
        (260, 286),
        "demo-bot wants to merge 6 commits into rocm-lab:main",
        font=small_font,
        fill="#536079",
    )

    url = "https://code.demo-acme.io/rocm-lab/pull/1842"
    draw.text((96, 360), url, font=mono_font, fill="#0969da")
    draw.line((96, 397, 776, 397), fill="#0969da", width=2)

    draw.text((96, 445), "Summary", font=_font(26, bold=True), fill="#172033")
    summary_lines = [
        "• adds self-speculative MTP decode for ThinkingCap-27B",
        "• keeps Radeon PRO W7900D on the gfx1100 HIP path",
        "• prepares concurrency 1 / 4 / 8 synthetic benchmark cells",
        "• no user data is stored on the compute node",
    ]
    y = 495
    for line in summary_lines:
        draw.text((116, y), line, font=body_font, fill="#27334a")
        y += 54

    draw.rounded_rectangle((96, 735, 1500, 792), radius=10, fill="#eef3ff")
    draw.text(
        (116, 751),
        "Review scope: HIP build · quant sweep · synthetic data only",
        font=small_font,
        fill="#344d8c",
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return [
        {"text": "rocm-lab / pull / 1842", "bbox": [96, 158, 520, 190], "conf": 1.0},
        {
            "text": "PR #1842 Enable MTP batching on gfx1100",
            "bbox": [96, 212, 995, 260],
            "conf": 1.0,
        },
        {"text": url, "bbox": [96, 355, 790, 402], "conf": 1.0},
    ]


def _previous_week_wednesday(now: datetime) -> datetime:
    monday_this_week = now.date() - timedelta(days=now.weekday())
    previous_wednesday = monday_this_week - timedelta(days=5)
    return datetime.combine(previous_wednesday, time(15, 18), TZ)


def main() -> int:
    if os.environ.get("DEJAVIEW_DEMO_MODE") != "1":
        raise SystemExit("set DEJAVIEW_DEMO_MODE=1 for the isolated P3.4 seed")
    dsn = os.environ.get("TIMELINE_DB_URL", DEFAULT_DSN)
    if dsn != DEFAULT_DSN:
        raise SystemExit("TIMELINE_DB_URL must target local database dejaview_demo")
    data_root = (
        Path(os.environ.get("DATA_ROOT", DEFAULT_DATA_ROOT)).expanduser().resolve()
    )
    if data_root != DEFAULT_DATA_ROOT:
        raise SystemExit(f"DATA_ROOT must be {DEFAULT_DATA_ROOT}")
    screenshot_path = (
        data_root / "screenshots" / DEVICE_ID / "rocm-pr-1842.png"
    ).resolve()
    blocks = _make_pr_screenshot(screenshot_path)

    now = datetime.now(TZ)
    pr_event = {
        "ts": _previous_week_wednesday(now),
        "app": "Chrome",
        "title": "rocm-lab PR #1842",
        "url": "https://code.demo-acme.io/rocm-lab/pull/1842",
        "activity": "Reviewing PR #1842 for MTP batching on ROCm gfx1100",
        "ocr": (
            "rocm-lab / pull / 1842\n"
            "PR #1842 Enable MTP batching on gfx1100\n"
            "https://code.demo-acme.io/rocm-lab/pull/1842\n"
            "self-speculative MTP decode for ThinkingCap-27B"
        ),
        "topics": ["rocm", "mtp", "pull-request"],
        "screenshot_path": str(screenshot_path),
        "ocr_blocks": blocks,
    }
    events = [pr_event]

    inserted: list[dict] = []
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        if cur.fetchone()[0] != "dejaview_demo":
            raise SystemExit("refusing to seed a non-demo database")
        cur.execute(
            "SELECT count(*) FROM timeline_events WHERE device_id NOT LIKE 'demo-%%'"
        )
        if int(cur.fetchone()[0]) != 0:
            raise SystemExit("demo database contains a non-demo device")
        cur.execute("DELETE FROM timeline_events WHERE device_id = %s", (DEVICE_ID,))
        cur.execute("DELETE FROM sentinel_audit WHERE device_id = %s", (DEVICE_ID,))
        for event in events:
            cur.execute(
                """
                INSERT INTO timeline_events
                    (ts, device_id, kind, app, window_title, url, activity,
                     topics, verbatim, ocr_text, ocr_blocks, screenshot_path)
                VALUES
                    (%s, %s, 'frame', %s, %s, %s, %s,
                     %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    event["ts"],
                    DEVICE_ID,
                    event["app"],
                    event["title"],
                    event.get("url"),
                    event["activity"],
                    event["topics"],
                    Jsonb({}),
                    event["ocr"],
                    Jsonb(event.get("ocr_blocks", [])),
                    event.get("screenshot_path"),
                ),
            )
            event_id = cur.fetchone()[0]
            inserted.append(
                {
                    "id": event_id,
                    "ts": event["ts"].isoformat(),
                    "app": event["app"],
                    "activity": event["activity"],
                }
            )
        conn.commit()

    print(
        json.dumps(
            {
                "device_id": DEVICE_ID,
                "rows_replaced": len(inserted),
                "pr_event_id": inserted[0]["id"],
                "screenshot": str(screenshot_path),
                "events": inserted,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
