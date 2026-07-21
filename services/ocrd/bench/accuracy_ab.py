#!/usr/bin/env python3
"""DejaView M5.2 / T0.5 - PP-OCR accuracy A/B (handbook section 6.1 + T0.5).

Compares per-character/per-token recall of the two OCR backends shipped in
``services/ocrd`` on the M6.1 synthetic screenshot set (20 PNGs in
``tests/assets/screenshots/`` with paired ground-truth JSON).

Backends compared
-----------------
* ``rapidocr`` -> ``rapidocr-onnxruntime`` (PP-OCRv4 ONNX). Default on Mac/ARM
  dev. ~1 s/image.
* ``paddleocr`` -> PaddleOCR 3.7 driving PP-OCRv6_medium via ONNX Runtime.
  Production target on EPYC. Slow on this M5 Mac (20-116 s/image), so the
  script accepts ``--paddle-subset`` to only run PaddleOCR on a representative
  subset (default: 2 per category = 8 images), as allowed by the task spec.

Evaluation metric
-----------------
Per-image, per-category **entity recall**:

    recall = (# ground-truth entities the OCR transcript contains)
           / (# ground-truth entities in that category)

A ground-truth entity (a string from ``text_snippets``/``error_codes``/
``urls``/``identifiers``/``numbers``) is "contained" if its normalized form is
a substring of the normalized OCR ``full_text``.

Normalization (applied identically to entity and transcript):

* lowercase
* replace any run of whitespace with a single space, strip ends
* strip punctuation/symbols EXCEPT alphanumerics and CJK -- keep ``-``, ``_``,
  ``.``, ``/``, ``:`` which matter inside URLs/identifiers/error codes
* **do NOT unify O/0**: O-vs-0 confusion is a weak point we explicitly want
  to surface, so we keep the two characters distinct.

The script writes:
* ``--out`` (JSON) -- raw transcripts, per-image per-category hit/miss lists,
  per-image timings, aggregate recalls. This is the audit trail; never edit
  numbers by hand.
* stdout -- a markdown-shaped summary table.

Usage
-----
::

    # Full rapidocr pass on 20 images + paddleocr on the default 8-image
    # representative subset:
    uv run python -m bench.accuracy_ab

    # Force paddleocr over all 20 images (slow; hours on this Mac):
    uv run python -m bench.accuracy_ab --paddle-all

    # Skip paddleocr entirely (rapidocr only):
    uv run python -m bench.accuracy_ab --no-paddle

Run from ``services/ocrd`` so the package + its venv resolve.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Make ``ocrd`` importable whether we're run as a module (-m bench.accuracy_ab)
# or as a script. ``services/ocrd`` is the package root.
HERE = Path(__file__).resolve().parent
PKG_SRC = HERE.parent / "src"
if str(PKG_SRC) not in sys.path:
    sys.path.insert(0, str(PKG_SRC))

# Locate the screenshot corpus relative to the repo root.
# HERE = <repo>/services/ocrd/bench -> parents[2] = <repo>.
REPO_ROOT = HERE.parents[2]
DEFAULT_ASSETS = REPO_ROOT / "tests" / "assets" / "screenshots"

CATEGORIES = ("code", "terminal", "webpage", "chat")
ENTITY_TYPES = ("text_snippets", "urls", "error_codes", "identifiers", "numbers")
# Pretty header for tables / docs.
ENTITY_LABEL = {
    "text_snippets": "snippet",
    "urls": "url",
    "error_codes": "errcode",
    "identifiers": "ident",
    "numbers": "number",
}


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
# Keep alphanumerics, CJK, and the punctuation that carries signal inside
# URLs / identifiers / error codes. Drop everything else (quotes, brackets,
# commas, semicolons, emoji, etc.). Whitespace is collapsed separately.
_KEEP_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff_./:\-]+")
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, drop non-signal punctuation, collapse whitespace.

    Importantly, the letters ``o`` and the digit ``0`` are NOT unified -- the
    O/0 confusion is one of the weak points this benchmark is meant to surface,
    so a hit only counts when the OCR transcript matches the ground-truth
    character verbatim.
    """
    s = text.lower()
    s = _KEEP_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def entity_hit(needle_norm: str, haystack_norm: str) -> bool:
    """Substring containment on normalized strings.

    Empty needles always count as a miss (defensive -- shouldn't happen with
    the M6.1 corpus, but the JSON schema allows it).
    """
    if not needle_norm:
        return False
    return needle_norm in haystack_norm


# ---------------------------------------------------------------------------
# Result containers (serialized to the JSON audit trail)
# ---------------------------------------------------------------------------
@dataclass
class EntityCheck:
    entity: str          # ground-truth entity, original casing
    category: str        # one of ENTITY_TYPES
    hit: bool
    normalized: str      # normalized form actually compared
    note: str = ""       # optional e.g. "near-miss R0CM vs ROCM"


@dataclass
class ImageResult:
    image: str
    category: str        # screenshot category (code/terminal/webpage/chat)
    backend: str
    elapsed_ms: float
    n_blocks: int
    full_text: str       # raw OCR transcript (for the weak-point audit)
    entities: list[EntityCheck] = field(default_factory=list)

    @property
    def hits_total(self) -> int:
        return sum(1 for e in self.entities if e.hit)

    @property
    def total(self) -> int:
        return len(self.entities)


# ---------------------------------------------------------------------------
# Backend construction
# ---------------------------------------------------------------------------
def build_backend(name: str):
    """Build an OCR engine by name. Errors propagate -- caller surfaces them."""
    # Import lazily so --help doesn't pay the model-load cost.
    from ocrd.engine import RapidOcrEngine, PaddleOcrEngine  # type: ignore

    if name == "rapidocr":
        return RapidOcrEngine()
    if name == "paddleocr":
        return PaddleOcrEngine()
    raise ValueError(f"Unknown backend: {name!r}")


def run_backend(backend, image_path: Path) -> tuple[str, float, int]:
    """Run one backend on one image. Returns (full_text, elapsed_ms, n_blocks)."""
    import numpy as np  # local import; heavy
    from PIL import Image

    with Image.open(image_path) as im:
        arr = np.asarray(im.convert("RGB"))
    t0 = time.perf_counter()
    result = backend.ocr(arr)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return result.full_text, elapsed_ms, len(result.blocks)


# ---------------------------------------------------------------------------
# Corpus + image selection
# ---------------------------------------------------------------------------
def list_corpus(assets_dir: Path) -> list[tuple[str, Path, Path]]:
    """Return [(image_name, png_path, json_path), ...] sorted by category."""
    out: list[tuple[str, Path, Path]] = []
    for png in sorted(assets_dir.glob("*.png")):
        gt = png.with_suffix(".json")
        if gt.exists():
            out.append((png.name, png, gt))
    return out


def pick_paddle_subset(corpus: list[tuple[str, Path, Path]], per_cat: int) -> list[tuple[str, Path, Path]]:
    """Pick ``per_cat`` images per screenshot category for the slow backend.

    We take the first ``per_cat`` of each category in sorted order, which
    deterministically covers code/terminal/webpage/chat. ``per_cat=2`` is the
    task spec's representative-subset default (8 images total).
    """
    by_cat: dict[str, list[tuple[str, Path, Path]]] = {c: [] for c in CATEGORIES}
    for entry in corpus:
        name = entry[0]
        for c in CATEGORIES:
            if name.startswith(c + "_"):
                by_cat[c].append(entry)
                break
    picked: list[tuple[str, Path, Path]] = []
    for c in CATEGORIES:
        picked.extend(by_cat[c][:per_cat])
    return picked


# ---------------------------------------------------------------------------
# Per-image evaluation
# ---------------------------------------------------------------------------
def evaluate(backend, backend_name: str, image_name: str, png: Path, gt: Path) -> ImageResult:
    gt_data = json.loads(gt.read_text())
    full_text, elapsed_ms, n_blocks = run_backend(backend, png)
    ocr_norm = normalize(full_text)

    checks: list[EntityCheck] = []
    for ent_type in ENTITY_TYPES:
        for ent in gt_data.get(ent_type, []):
            ent_s = str(ent)
            ent_norm = normalize(ent_s)
            hit = entity_hit(ent_norm, ocr_norm)
            note = ""
            if not hit and _near_miss_o_zero(ent_norm, ocr_norm):
                note = "near-miss O/0 or 1/l confusion"
            checks.append(
                EntityCheck(
                    entity=ent_s,
                    category=ent_type,
                    hit=hit,
                    normalized=ent_norm,
                    note=note,
                )
            )
    return ImageResult(
        image=image_name,
        category=gt_data.get("category", ""),
        backend=backend_name,
        elapsed_ms=elapsed_ms,
        n_blocks=n_blocks,
        full_text=full_text,
        entities=checks,
    )


_O_ZERO_MAP = str.maketrans({"0": "o", "o": "0"})
_ONE_L_MAP = str.maketrans({"1": "l", "l": "1"})


def _near_miss_o_zero(needle_norm: str, haystack_norm: str, min_len: int = 3) -> bool:
    """Heuristic: did we miss only because of O/0 or 1/l confusion?

    Used purely to flag interesting weak-points in the audit JSON. Counts as a
    near-miss if normalizing O<->0 (and 1<->l) in BOTH strings makes the needle
    a substring of the haystack. We require ``len >= 3`` so trivial 1-2 char
    entities don't dominate.
    """
    if len(needle_norm) < min_len:
        return False
    n = needle_norm.translate(_O_ZERO_MAP).translate(_ONE_L_MAP)
    h = haystack_norm.translate(_O_ZERO_MAP).translate(_ONE_L_MAP)
    # If after O/0 + 1/l unification it matches, the original miss was almost
    # certainly that confusion.
    if n in h:
        return True
    return False


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def aggregate(results: list[ImageResult]) -> dict:
    """Build per-backend, per-category, per-entity-type recall.

    Returns a nested dict::

        {
          "by_entity_type": {backend: {ent_type: {hit, total, recall}}},
          "by_screenshot_category": {backend: {cat: {hit, total, recall}}},
          "overall": {backend: {hit, total, recall}},
          "timings_ms": {backend: {count, mean, median, total_s}},
        }
    """
    backends = sorted({r.backend for r in results})
    out: dict = {
        "by_entity_type": {},
        "by_screenshot_category": {},
        "overall": {},
        "timings_ms": {},
    }

    for b in backends:
        bres = [r for r in results if r.backend == b]
        # by entity type
        et_agg: dict[str, dict] = {et: {"hit": 0, "total": 0} for et in ENTITY_TYPES}
        cat_agg: dict[str, dict] = {c: {"hit": 0, "total": 0} for c in CATEGORIES}
        overall = {"hit": 0, "total": 0}
        timings: list[float] = []
        for r in bres:
            timings.append(r.elapsed_ms)
            for e in r.entities:
                et_agg[e.category]["hit"] += int(e.hit)
                et_agg[e.category]["total"] += 1
                cat_agg[r.category]["hit"] += int(e.hit)
                cat_agg[r.category]["total"] += 1
                overall["hit"] += int(e.hit)
                overall["total"] += 1
        out["by_entity_type"][b] = {
            et: {
                "hit": v["hit"],
                "total": v["total"],
                "recall": round(v["hit"] / v["total"], 4) if v["total"] else None,
            }
            for et, v in et_agg.items()
        }
        out["by_screenshot_category"][b] = {
            c: {
                "hit": v["hit"],
                "total": v["total"],
                "recall": round(v["hit"] / v["total"], 4) if v["total"] else None,
            }
            for c, v in cat_agg.items()
        }
        out["overall"][b] = {
            "hit": overall["hit"],
            "total": overall["total"],
            "recall": round(overall["hit"] / overall["total"], 4) if overall["total"] else None,
            "n_images": len(bres),
        }
        srt = sorted(timings)
        out["timings_ms"][b] = {
            "count": len(timings),
            "mean": round(sum(timings) / len(timings), 1) if timings else 0,
            "median": round(srt[len(srt) // 2], 1) if timings else 0,
            "min": round(min(timings), 1) if timings else 0,
            "max": round(max(timings), 1) if timings else 0,
            "total_s": round(sum(timings) / 1000.0, 1),
        }
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_summary(agg: dict, sample_notes: dict[str, str]) -> None:
    backends = list(agg["overall"].keys())
    print()
    print("## Overall recall")
    print()
    print("| backend | recall | hits/total | images | mean ms/img | total s |")
    print("|---|---|---|---|---|---|")
    for b in backends:
        o = agg["overall"][b]
        t = agg["timings_ms"][b]
        print(
            f"| {b} | {o['recall']} | {o['hit']}/{o['total']} | {o['n_images']} | {t['mean']} | {t['total_s']} |"
        )
    print()
    if sample_notes:
        for k, v in sample_notes.items():
            print(f"_{k}: {v}_")
        print()

    print("## Recall by entity type")
    print()
    header = "| entity type | " + " | ".join(backends) + " |"
    print(header)
    print("|" + "---|" * (len(backends) + 1))
    for et in ENTITY_TYPES:
        row = [ENTITY_LABEL[et]]
        for b in backends:
            cell = agg["by_entity_type"][b][et]
            if cell["total"] == 0:
                row.append("- (0)")
            else:
                row.append(f"{cell['recall']} ({cell['hit']}/{cell['total']})")
        print("| " + " | ".join(row) + " |")
    print()

    print("## Recall by screenshot category")
    print()
    print(header)
    print("|" + "---|" * (len(backends) + 1))
    for c in CATEGORIES:
        row = [c]
        for b in backends:
            cell = agg["by_screenshot_category"][b][c]
            if cell["total"] == 0:
                row.append("- (0)")
            else:
                row.append(f"{cell['recall']} ({cell['hit']}/{cell['total']})")
        print("| " + " | ".join(row) + " |")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--assets",
        type=Path,
        default=DEFAULT_ASSETS,
        help=f"Screenshots directory (default: {DEFAULT_ASSETS})",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=HERE / "accuracy_ab_results.json",
        help="JSON audit-trail output path.",
    )
    p.add_argument(
        "--no-paddle",
        action="store_true",
        help="Skip the paddleocr backend (rapidocr only).",
    )
    p.add_argument(
        "--paddle-all",
        action="store_true",
        help="Run paddleocr on ALL images (overrides --paddle-subset). Slow.",
    )
    p.add_argument(
        "--paddle-subset",
        type=int,
        default=2,
        help="Images per screenshot category for paddleocr (default: 2 = 8 total).",
    )
    p.add_argument(
        "--rapid-only",
        type=str,
        default="",
        help="Comma-separated image names; if set, rapidocr runs only on these.",
    )
    args = p.parse_args(argv)

    if not args.assets.is_dir():
        print(f"ERROR: assets dir not found: {args.assets}", file=sys.stderr)
        return 2

    corpus = list_corpus(args.assets)
    if not corpus:
        print(f"ERROR: no PNG+JSON pairs in {args.assets}", file=sys.stderr)
        return 2

    if args.rapid_only:
        wanted = {s.strip() for s in args.rapid_only.split(",") if s.strip()}
        corpus = [c for c in corpus if c[0] in wanted]

    rapid_corpus = corpus
    paddle_corpus = corpus if args.paddle_all else pick_paddle_subset(corpus, args.paddle_subset)

    sample_notes: dict[str, str] = {}
    if args.no_paddle:
        backends_to_run: list[tuple[str, list]] = [("rapidocr", rapid_corpus)]
        sample_notes["paddleocr"] = "skipped (--no-paddle)"
    else:
        backends_to_run = [("rapidocr", rapid_corpus), ("paddleocr", paddle_corpus)]
        if not args.paddle_all:
            sample_notes["paddleocr sample"] = (
                f"representative subset ({args.paddle_subset}/category "
                f"= {len(paddle_corpus)} images); see --paddle-all for full pass"
            )

    all_results: list[ImageResult] = []
    for name, sub in backends_to_run:
        print(f"\n=== backend={name}, {len(sub)} images ===", flush=True)
        try:
            engine = build_backend(name)
        except Exception as e:  # pragma: no cover - reported on stderr
            print(f"ERROR building backend {name!r}: {e}", file=sys.stderr)
            sample_notes[f"{name} error"] = str(e)
            continue
        for image_name, png, gt in sub:
            t0 = time.perf_counter()
            try:
                res = evaluate(engine, name, image_name, png, gt)
            except Exception as e:  # pragma: no cover
                print(f"  [{name}] {image_name}: FAIL {e}", file=sys.stderr)
                continue
            wall = time.perf_counter() - t0
            print(
                f"  [{name}] {image_name}: recall={res.hits_total}/{res.total} "
                f"ocr_ms={res.elapsed_ms:.0f} wall={wall:.1f}s "
                f"blocks={res.n_blocks}",
                flush=True,
            )
            all_results.append(res)

    if not all_results:
        print("No results; aborting.", file=sys.stderr)
        return 1

    agg = aggregate(all_results)

    payload = {
        "schema_version": 1,
        "generated_at_unix": int(time.time()),
        "assets_dir": str(args.assets),
        "rapidocr_images": [r.image for r in all_results if r.backend == "rapidocr"],
        "paddleocr_images": [r.image for r in all_results if r.backend == "paddleocr"],
        "aggregates": agg,
        "results": [asdict(r) for r in all_results],
    }
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\nWrote audit JSON -> {args.out}")

    print_summary(agg, sample_notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
