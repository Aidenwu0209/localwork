#!/usr/bin/env python3
"""P3.7 perceive regression on tests/assets/screenshots (~20 frames).

For each PNG: OCR via OCR_URL (or --ocr-from-gt using the sibling JSON
text_snippets as a stand-in), then GatewayPerceive. Reports:
  - generic activity rate ("working in X" / "using X")
  - verbatim ⊆ ocr_text violations

Examples:
  GATEWAY_URL=http://127.0.0.1:14000/v1 OCR_URL=http://127.0.0.1:8006 \\
    uv run python scripts/eval_perceive.py
  GATEWAY_URL=http://127.0.0.1:8002/v1 uv run python scripts/eval_perceive.py --ocr-from-gt
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memoryd.stages import GatewayPerceive, OcrdClient  # noqa: E402

_GENERIC = re.compile(r"^\s*(working in|using)\b", re.I)


def gt_ocr(meta: dict) -> str:
    bits: list[str] = []
    for k in ("text_snippets", "identifiers", "urls", "error_codes", "numbers"):
        for x in meta.get(k) or []:
            bits.append(str(x))
    return "\n".join(bits)


def verbatim_items(vb) -> list[str]:
    out: list[str] = []
    for field in ("errors", "urls", "identifiers", "numbers", "quotes"):
        out.extend(getattr(vb, field) or [])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", type=Path, default=ROOT / "tests/assets/screenshots")
    ap.add_argument(
        "--gateway",
        default=os.environ.get("GATEWAY_URL", "http://127.0.0.1:14000/v1"),
    )
    ap.add_argument("--ocr-url", default=os.environ.get("OCR_URL", "http://127.0.0.1:8006"))
    ap.add_argument("--ocr-from-gt", action="store_true",
                    help="use JSON text_snippets as OCR (no ocrd needed)")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--out", type=Path, default=Path("/tmp/perceive_eval_p37.json"))
    args = ap.parse_args()

    pngs = sorted(args.assets.glob("*.png"))[: args.limit]
    if not pngs:
        print(f"no pngs under {args.assets}", file=sys.stderr)
        return 2

    perceive = GatewayPerceive(args.gateway)
    ocr = None if args.ocr_from_gt else OcrdClient(args.ocr_url)

    async def run() -> list[dict]:
        rows: list[dict] = []
        for png in pngs:
            meta = json.loads(png.with_suffix(".json").read_text())
            app = meta.get("category", "app")
            title = png.stem
            if args.ocr_from_gt:
                ocr_text = gt_ocr(meta)
            else:
                assert ocr is not None
                ocr_res = await ocr.recognize(png.read_bytes())
                ocr_text = ocr_res.full_text
            ev = await perceive.understand(png.read_bytes(), ocr_text, app, title)
            items = verbatim_items(ev.verbatim)
            ocr_l = ocr_text.lower()
            bad = [s for s in items if s not in ocr_text and s.lower() not in ocr_l]
            generic = bool(_GENERIC.match(ev.activity or ""))
            row = {
                "file": png.name,
                "activity": ev.activity,
                "app_context": ev.app_context,
                "topics": ev.topics,
                "verbatim": ev.verbatim.model_dump(),
                "generic_activity": generic,
                "verbatim_violations": bad,
                "ocr_chars": len(ocr_text),
            }
            rows.append(row)
            flag = "GENERIC" if generic else "ok"
            viol = f" viol={bad}" if bad else ""
            print(f"{png.name:16s} [{flag}] {ev.activity!r}{viol}", flush=True)
        return rows

    rows = asyncio.run(run())
    n = len(rows)
    g = sum(1 for r in rows if r["generic_activity"])
    v = sum(1 for r in rows if r["verbatim_violations"])
    summary = {
        "n": n,
        "generic_activity": f"{g}/{n}",
        "verbatim_violation_events": f"{v}/{n}",
        "verbatim_rule": "all verbatim strings must be substrings of ocr_text (enforced in parser)",
    }
    print(f"SUMMARY generic={summary['generic_activity']} verbatim_viol_events={summary['verbatim_violation_events']}")
    args.out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
