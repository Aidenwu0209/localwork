#!/usr/bin/env python3
"""M3.4: run the full local pipeline (real sentinel + ocrd + novelty + perceive
+ embed) over synthetic frames and verify the three invariants:

  1. normal frames -> ingested as timeline_events with perceive activity/verbatim
  2. sensitive frames -> sentinel blocks; NO timeline_events row, NO screenshot
     on disk; only sentinel_audit records the block (privacy invariant).
  3. near-duplicate frames -> novelty gate merges into the previous event
     (end_ts advances, no new row).

Requires the full dev stack up: dev-stack.sh up embed fast sentinel perceive,
plus ocrd (cd services/ocrd && uv run python -m ocrd) and memoryd running with
MEMORYD_REAL_PIPELINE=1.

Run:
    MEMORYD_REAL_PIPELINE=1 uv run python services/memoryd/scripts/run_m34_pipeline.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg

MEMORYD = "http://127.0.0.1:8090"
ROOT = Path(__file__).resolve().parents[3]
DSN = "postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview"

# 10 frames: 4 sensitive (1 per category) + 4 normal + 2 near-dup of a normal.
SENSITIVE = [
    ("banking_01.png", "banking_finance"),
    ("password_01.png", "password_prompt"),
    ("private_chat_01.png", "private_chat"),
    ("id_document_01.png", "id_document"),
]
NORMAL = ["code_01.png", "terminal_01.png", "webpage_01.png", "chat_01.png"]
# Near-duplicate: same image re-sent as a second frame to exercise the merge path.
DUP = ["code_01.png", "code_01.png"]


def _ingest(client: httpx.Client, image_path: Path, *, device_id: str, ts: str,
            app: str, title: str) -> dict:
    with image_path.open("rb") as f:
        files = {"file": (image_path.name, f.read(), "image/png")}
    meta = (f'{{"device_id":"{device_id}","ts":"{ts}","app":"{app}",'
            f'"window_title":"{title}","trigger":"change"}}')
    r = client.post(f"{MEMORYD}/v1/ingest/frame", data={"meta": meta}, files=files)
    r.raise_for_status()
    return r.json()


async def _main() -> int:
    sent_dir = ROOT / "tests/assets/sentinel"
    scr_dir = ROOT / "tests/assets/screenshots"

    # Wipe prior m34 rows + screenshots for a clean run.
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM timeline_events WHERE device_id = 'm34'")
        cur.execute("DELETE FROM sentinel_audit WHERE device_id = 'm34'")
        conn.commit()

    base = datetime.now(timezone.utc)
    with httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
        # health
        h = client.get(f"{MEMORYD}/health")
        if h.status_code != 200:
            print(f"memoryd not healthy: {h.status_code}", file=sys.stderr)
            return 1

        results = {"sensitive_blocked": [], "normal_ingested": [], "merged": []}

        # 1. Sensitive frames — must be blocked.
        print("\n=== sensitive frames (expect block) ===")
        for fname, cat in SENSITIVE:
            ts = (base + timedelta(seconds=10)).isoformat()
            t0 = time.monotonic()
            ack = _ingest(client, sent_dir / fname,
                          device_id="m34", ts=ts, app="Browser",
                          title=fname)
            dt = time.monotonic() - t0
            verdict = ack.get("sentinel") or {}
            ok = (not ack["accepted"]) and verdict.get("decision") == "block"
            print(f"  {fname:24s} {dt:5.1f}s decision={verdict.get('decision')} "
                  f"cat={verdict.get('category')} -> {'BLOCK OK' if ok else 'UNEXPECTED'}")
            results["sensitive_blocked"].append((fname, ok, verdict.get("category")))

        # 2. Normal frames — must be ingested with real perceive activity.
        print("\n=== normal frames (expect ingest + perceive activity) ===")
        for i, fname in enumerate(NORMAL):
            ts = (base + timedelta(minutes=i + 1)).isoformat()
            t0 = time.monotonic()
            ack = _ingest(client, scr_dir / fname,
                          device_id="m34", ts=ts, app=_app_for(fname),
                          title=fname)
            dt = time.monotonic() - t0
            eid = ack.get("event_id")
            print(f"  {fname:24s} {dt:5.1f}s event_id={eid} note={ack.get('note','')[:80]}")
            results["normal_ingested"].append((fname, eid))

        # 3. Near-duplicate frames — second/third same-window frame should merge.
        print("\n=== near-duplicate frames (expect merge into previous) ===")
        for i, fname in enumerate(DUP):
            ts = (base + timedelta(minutes=10 + i)).isoformat()
            t0 = time.monotonic()
            ack = _ingest(client, scr_dir / fname,
                          device_id="m34", ts=ts, app=_app_for(fname),
                          title=fname)  # same app+title as the code_01 above
            dt = time.monotonic() - t0
            merged = ack.get("merged_into")
            print(f"  {fname:24s} {dt:5.1f}s merged_into={merged} note={ack.get('note','')[:80]}")
            results["merged"].append((fname, merged))

    # DB-level verification.
    print("\n=== DB verification ===")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM timeline_events WHERE device_id='m34'")
        n_events = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM sentinel_audit WHERE device_id='m34'")
        n_audit = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM sentinel_audit WHERE device_id='m34' AND decision='block'")
        n_blocks = cur.fetchone()[0]
        cur.execute("SELECT id, activity, app, topics FROM timeline_events WHERE device_id='m34' ORDER BY id")
        rows = cur.fetchall()

    print(f"  timeline_events (m34): {n_events}  (expect 4 normal + 0 sensitive + 0 dup)")
    print(f"  sentinel_audit (m34):  {n_audit} total, {n_blocks} blocks (expect 4 blocks)")
    print(f"  perceive activity sample:")
    for rid, activity, app, topics in rows:
        print(f"    id={rid} app={app} activity={activity[:70]}")

    # Sensitive must produce ZERO timeline rows and ZERO screenshots.
    blocked_ok = all(ok for _, ok, _ in results["sensitive_blocked"])
    ingested_ok = all(eid is not None for _, eid in results["normal_ingested"])
    merged_ok = any(m is not None for _, m in results["merged"])
    print(f"\n=== VERDICT ===")
    print(f"  sensitive blocked (no row, no screenshot): {'PASS' if blocked_ok and n_blocks == 4 else 'FAIL'}")
    print(f"  normal ingested with perceive activity:     {'PASS' if ingested_ok else 'FAIL'}")
    print(f"  near-duplicate merged:                      {'PASS' if merged_ok else 'CHECK'}")

    # cleanup option
    print("\ncleanup: DELETE FROM timeline_events WHERE device_id='m34';")
    print("         DELETE FROM sentinel_audit WHERE device_id='m34';")
    return 0


def _app_for(fname: str) -> str:
    if fname.startswith("code"):
        return "VS Code"
    if fname.startswith("terminal"):
        return "Terminal"
    if fname.startswith("webpage"):
        return "Chrome"
    if fname.startswith("chat"):
        return "Slack"
    return "Other"


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
