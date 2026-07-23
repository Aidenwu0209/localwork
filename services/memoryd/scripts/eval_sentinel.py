#!/usr/bin/env python3
"""P3.6 sentinel regression on tests/assets/sentinel.

Uses GatewaySentinel's prompt + _parse_sentinel_json (category→decision).
Default GATEWAY_URL=http://127.0.0.1:14000/v1 (SSH tunnel) or point at a
local llama-server / LiteLLM gateway.

Examples:
  GATEWAY_URL=http://127.0.0.1:14000/v1 python scripts/eval_sentinel.py
  GATEWAY_URL=http://127.0.0.1:8003/v1 python scripts/eval_sentinel.py --direct
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # localwork/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memoryd.stages import GatewaySentinel  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--assets",
        type=Path,
        default=ROOT / "tests/assets/sentinel",
    )
    ap.add_argument(
        "--gateway",
        default=os.environ.get("GATEWAY_URL", "http://127.0.0.1:14000/v1"),
    )
    ap.add_argument("--limit", type=int, default=0, help="optional cap on images")
    ap.add_argument("--out", type=Path, default=Path("/tmp/sentinel_eval_p36.json"))
    args = ap.parse_args()

    pngs = sorted(args.assets.glob("*.png"))
    if args.limit:
        pngs = pngs[: args.limit]
    if not pngs:
        print(f"no pngs under {args.assets}", file=sys.stderr)
        return 2

    sentinel = GatewaySentinel(args.gateway)

    async def run() -> list[dict]:
        rows: list[dict] = []
        for png in pngs:
            meta = json.loads(png.with_suffix(".json").read_text())
            raw_bytes = png.read_bytes()
            verdict = await sentinel.classify(raw_bytes)
            ok = verdict.decision == meta["expected_decision"]
            row = {
                "file": png.name,
                "exp_decision": meta["expected_decision"],
                "exp_category": meta["category"],
                "got_decision": verdict.decision,
                "got_category": verdict.category,
                "confidence": verdict.confidence,
                "ok": ok,
            }
            rows.append(row)
            print(
                f"{png.name:28s} exp={meta['expected_decision']:5s}/{meta['category']:16s} "
                f"got={verdict.decision:5s}/{verdict.category:16s} "
                f"conf={verdict.confidence:.2f} ok={ok}",
                flush=True,
            )
        return rows

    rows = asyncio.run(run())
    n_normal = sum(1 for r in rows if r["exp_category"] == "normal")
    fp = sum(1 for r in rows if r["exp_category"] == "normal" and r["got_decision"] == "block")
    n_block = sum(1 for r in rows if r["exp_decision"] == "block")
    tp = sum(1 for r in rows if r["exp_decision"] == "block" and r["got_decision"] == "block")
    summary = {
        "n": len(rows),
        "block_recall": f"{tp}/{n_block}",
        "normal_false_positive": f"{fp}/{n_normal}",
        "block_recall_pct": (tp / n_block) if n_block else None,
        "normal_fp_pct": (fp / n_normal) if n_normal else None,
        "key_categories": {},
    }
    for key in ("banking_finance", "password_prompt", "private_chat", "id_document"):
        items = [r for r in rows if r["exp_category"] == key]
        blocked = sum(1 for r in items if r["got_decision"] == "block")
        summary["key_categories"][key] = f"{blocked}/{len(items)}"
        print(f"KEY {key}: {blocked}/{len(items)} blocked")
    print(
        f"SUMMARY block-recall={summary['block_recall']} "
        f"normal-FP={summary['normal_false_positive']}"
    )
    payload = {"summary": summary, "rows": rows}
    args.out.write_text(json.dumps(payload, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
