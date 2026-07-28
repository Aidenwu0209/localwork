#!/usr/bin/env python3
"""Build markdown tables from P3.1 raw JSON without hand-editing numbers."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any

BRAIN_RE = re.compile(
    r"^brain-(Q8_0|Q6_K|Q4_K_M)-mtp-(off|on)-c(1|4|8)\.json$"
)
PERCEIVE_RE = re.compile(
    r"^perceive-Q8_0-np(?P<parallel>1|2|4)-c(?P=parallel)\.json$"
)
QUANT_ORDER = {"Q8_0": 0, "Q6_K": 1, "Q4_K_M": 2}
PERCEIVE_IMAGE_SHA256 = (
    "d7903ab467f554b2fba7489380024c603c0ad3b8785ccb08f62af07cc976caf9"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Write the available rows even when expected cells are absent.",
    )
    return parser.parse_args()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("summary"), dict):
        raise TypeError(f"invalid benchmark record: {path}")
    return value


def _load_manifest(results: Path) -> tuple[dict[str, str], str]:
    manifest_path = results / "run-manifest.txt"
    checksum_path = results / "run-manifest.sha256"
    if not manifest_path.is_file() or not checksum_path.is_file():
        raise ValueError("run manifest or its checksum is missing")
    content = manifest_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if checksum_path.read_text(encoding="utf-8").strip() != (
        f"{digest}  run-manifest.txt"
    ):
        raise ValueError("run manifest checksum file does not match its content")
    fields: dict[str, str] = {}
    for line in content.decode("utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or key in fields:
            raise ValueError(f"invalid run manifest line: {line!r}")
        fields[key] = value
    required = {
        "manifest_schema",
        "run_id",
        "mode",
        "runs",
        "warmup_batches",
        "brain_max_tokens",
        "perceive_max_tokens",
        "brain_port",
        "perceive_port",
        "llama_cpp_commit",
        "llama_bin_sha256",
        "weights_manifest_sha256",
        "perceive_image_sha256",
        "brain_prompt_sha256",
        "perceive_prompt_sha256",
    }
    missing = sorted(required - fields.keys())
    if missing or fields["manifest_schema"] != "1":
        raise ValueError(f"invalid run manifest; missing={missing}")
    return fields, digest


def _same_number(actual: Any, expected: float) -> bool:
    try:
        return math.isclose(float(actual), expected, rel_tol=1e-9, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False


def _nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    return ordered[rank - 1]


def _validate_raw_batches(
    path: Path,
    record: dict[str, Any],
    *,
    concurrency: int,
    trials: int,
    require_draft: bool,
    require_image: bool,
    required_numeric_prefix: int,
) -> None:
    batches = record.get("batches")
    summary = record["summary"]
    if not isinstance(batches, list) or len(batches) != trials:
        raise ValueError(f"raw batch count mismatch: {path}")
    samples: list[dict[str, Any]] = []
    aggregate_rates: list[float] = []
    for expected_trial, batch in enumerate(batches, start=1):
        if not isinstance(batch, dict) or batch.get("trial") != expected_trial:
            raise ValueError(f"raw trial ordering mismatch: {path}")
        batch_samples = batch.get("samples")
        if not isinstance(batch_samples, list) or len(batch_samples) != concurrency:
            raise ValueError(f"raw sample count mismatch: {path}")
        batch_wall_ms = float(batch.get("batch_wall_ms") or 0)
        if batch_wall_ms <= 0:
            raise ValueError(f"invalid batch wall time: {path}")
        for request_index, sample in enumerate(batch_samples, start=1):
            if not isinstance(sample, dict):
                raise TypeError(f"invalid raw sample: {path}")
            if sample.get("request_id") != (
                f"trial-{expected_trial}-request-{request_index}"
            ):
                raise ValueError(f"request identity mismatch: {path}")
            if int(sample.get("http_status") or 0) != 200:
                raise ValueError(f"non-200 raw sample: {path}")
            for field in (
                "wall_ms",
                "prompt_n",
                "predicted_n",
                "prompt_per_second",
                "predicted_per_second",
            ):
                if float(sample.get(field) or 0) <= 0:
                    raise ValueError(f"invalid {field} in raw sample: {path}")
            timings = sample.get("timings")
            if not isinstance(timings, dict):
                raise TypeError(f"raw llama timings missing: {path}")
            if not _same_number(
                timings.get("prompt_per_second"),
                float(sample["prompt_per_second"]),
            ) or not _same_number(
                timings.get("predicted_per_second"),
                float(sample["predicted_per_second"]),
            ):
                raise ValueError(f"raw timing fields disagree: {path}")
            if (
                "cache_n" not in timings
                or int(timings["cache_n"]) != 0
                or sample.get("cache_n") != 0
            ):
                raise ValueError(f"prompt cache contaminated timing: {path}")
            if not re.fullmatch(r"[0-9a-f]{64}", str(sample.get("content_sha256"))):
                raise ValueError(f"invalid content hash: {path}")
            draft_value = sample.get("draft_n")
            accepted_value = sample.get("draft_n_accepted")
            if (
                not isinstance(draft_value, int)
                or isinstance(draft_value, bool)
                or not isinstance(accepted_value, int)
                or isinstance(accepted_value, bool)
                or draft_value < 0
                or accepted_value < 0
                or accepted_value > draft_value
            ):
                raise ValueError(f"invalid draft token counts: {path}")
            if require_image:
                preview = sample.get("content_preview")
                if (
                    sample.get("content_matches_required") is not True
                    or not isinstance(preview, str)
                    or re.search(r"(?i)\bparse\.py\b", preview) is None
                    or int(sample.get("content_length") or 0) != len(preview)
                    or sample.get("content_sha256")
                    != hashlib.sha256(preview.encode("utf-8")).hexdigest()
                ):
                    raise ValueError(f"visual-grounding match missing: {path}")
            if not require_image:
                expected = " ".join(
                    str(value) for value in range(1, required_numeric_prefix + 1)
                )
                preview = sample.get("content_preview")
                if (
                    int(sample.get("numeric_sequence_prefix") or 0)
                    != required_numeric_prefix
                    or sample.get("numeric_sequence_complete") is not True
                    or sample.get("numeric_sequence_exact") is not True
                    or preview != expected
                    or int(sample.get("content_length") or 0) != len(expected)
                    or sample.get("content_sha256")
                    != hashlib.sha256(expected.encode("utf-8")).hexdigest()
                ):
                    raise ValueError(f"numeric quality exact-match failed: {path}")
            samples.append(sample)
        generated_tokens = sum(
            int(sample.get("predicted_n") or 0) for sample in batch_samples
        )
        prompt_tokens = sum(
            int(sample.get("prompt_n") or 0) for sample in batch_samples
        )
        aggregate = generated_tokens / (batch_wall_ms / 1000.0)
        if (
            int(batch.get("generated_tokens") or 0) != generated_tokens
            or int(batch.get("prompt_tokens") or 0) != prompt_tokens
            or not _same_number(
                batch.get("aggregate_output_tps_end_to_end"),
                aggregate,
            )
        ):
            raise ValueError(f"batch aggregate disagrees with raw samples: {path}")
        aggregate_rates.append(aggregate)

    prompt_rates = [float(sample["prompt_per_second"]) for sample in samples]
    decode_rates = [float(sample["predicted_per_second"]) for sample in samples]
    request_walls = [float(sample["wall_ms"]) for sample in samples]
    batch_walls = [float(batch["batch_wall_ms"]) for batch in batches]
    completion_counts = [float(sample["predicted_n"]) for sample in samples]
    numeric_prefixes = [
        float(sample["numeric_sequence_prefix"]) for sample in samples
    ]
    numeric_complete = [
        bool(sample["numeric_sequence_complete"]) for sample in samples
    ]
    content_matches = [
        bool(sample["content_matches_required"]) for sample in samples
    ]
    draft_n = sum(int(sample.get("draft_n") or 0) for sample in samples)
    draft_accepted = sum(
        int(sample.get("draft_n_accepted") or 0) for sample in samples
    )
    expected_summary = {
        "successful_requests": len(samples),
        "trial_count": trials,
        "concurrency": concurrency,
        "prompt_tps_median": statistics.median(prompt_rates),
        "decode_tps_median_per_request": statistics.median(decode_rates),
        "aggregate_output_tps_end_to_end_median": statistics.median(
            aggregate_rates
        ),
        "request_wall_p50_ms": statistics.median(request_walls),
        "request_wall_p95_ms": _nearest_rank(request_walls, 95),
        "batch_wall_p50_ms": statistics.median(batch_walls),
        "batch_wall_p95_ms": _nearest_rank(batch_walls, 95),
        "completion_tokens_median": statistics.median(completion_counts),
        "numeric_sequence_prefix_median": statistics.median(numeric_prefixes),
        "numeric_sequence_complete_rate": (
            sum(numeric_complete) / len(numeric_complete)
        ),
        "required_content_match_rate": (
            sum(content_matches) / len(content_matches)
        ),
        "draft_n": draft_n,
        "draft_n_accepted": draft_accepted,
    }
    for field, expected in expected_summary.items():
        if not _same_number(summary.get(field), float(expected)):
            raise ValueError(f"summary does not match raw {field}: {path}")
    expected_draft_ratio = draft_accepted / draft_n if draft_n > 0 else None
    actual_draft_ratio = summary.get("draft_acceptance_ratio")
    if expected_draft_ratio is None:
        if actual_draft_ratio is not None:
            raise ValueError(
                f"summary does not match raw draft_acceptance_ratio: {path}"
            )
    elif not _same_number(actual_draft_ratio, expected_draft_ratio):
        raise ValueError(
            f"summary does not match raw draft_acceptance_ratio: {path}"
        )
    if require_draft and draft_n <= 0:
        raise ValueError(f"MTP cell generated no draft tokens: {path}")
    if not require_draft and draft_n != 0:
        raise ValueError(f"non-MTP raw samples contain drafted tokens: {path}")


def _validate_record(
    path: Path,
    record: dict[str, Any],
    *,
    manifest: dict[str, str],
    manifest_sha256: str,
    concurrency: int,
    require_draft: bool,
    require_image: bool,
    expected_model: str,
    max_tokens_field: str,
    prompt_sha_field: str,
) -> None:
    summary = record["summary"]
    method = record.get("method") or {}
    evidence = record.get("evidence") or {}
    trials = int(summary.get("trial_count") or 0)
    successful = int(summary.get("successful_requests") or 0)
    required_numeric_prefix = 0 if require_image else 80
    if record.get("schema_version") != 2 or record.get("label") != path.stem:
        raise ValueError(f"schema/label mismatch: {path}")
    if evidence != {
        "run_id": manifest["run_id"],
        "manifest_sha256": manifest_sha256,
        "llama_commit": manifest["llama_cpp_commit"],
        "llama_bin_sha256": manifest["llama_bin_sha256"],
    }:
        raise ValueError(f"run/build fingerprint mismatch: {path}")
    if trials < 3 or successful != trials * concurrency:
        raise ValueError(
            f"incomplete requests in {path}: {successful}/{trials * concurrency}"
        )
    if trials != int(manifest["runs"]):
        raise ValueError(f"trial count differs from run manifest: {path}")
    if int(method.get("concurrency") or 0) != concurrency:
        raise ValueError(f"concurrency mismatch: {path}")
    if (
        method.get("model") != expected_model
        or method.get("url")
        != (
            "http://127.0.0.1:"
            + manifest[
                "perceive_port" if require_image else "brain_port"
            ]
            + "/v1/chat/completions"
        )
        or int(method.get("runs") or 0) != int(manifest["runs"])
        or int(method.get("warmup_batches") or -1)
        != int(manifest["warmup_batches"])
        or int(method.get("max_tokens") or 0) != int(manifest[max_tokens_field])
        or method.get("synthetic_prompt_sha256") != manifest[prompt_sha_field]
        or method.get("enable_thinking") is not False
        or method.get("cache_prompt") is not False
        or method.get("temperature") != 0
        or int(method.get("required_numeric_prefix") or 0)
        != required_numeric_prefix
    ):
        raise ValueError(f"method differs from run manifest: {path}")
    if method.get("require_draft") is not require_draft:
        raise ValueError(f"draft mode mismatch: {path}")
    if method.get("forbid_draft") is not (not require_draft):
        raise ValueError(f"draft exclusion mismatch: {path}")
    _validate_raw_batches(
        path,
        record,
        concurrency=concurrency,
        trials=trials,
        require_draft=require_draft,
        require_image=require_image,
        required_numeric_prefix=required_numeric_prefix,
    )
    if require_image and (
        method.get("image_sha256") != PERCEIVE_IMAGE_SHA256
        or manifest["perceive_image_sha256"] != PERCEIVE_IMAGE_SHA256
        or method.get("required_content_regex") != r"(?i)\bparse\.py\b"
        or not _same_number(summary.get("required_content_match_rate"), 1.0)
    ):
        raise ValueError(f"perceive visual-grounding contract failed: {path}")
    if not require_image and (
        method.get("image_sha256") is not None
        or method.get("required_content_regex") is not None
    ):
        raise ValueError(f"brain cell unexpectedly used visual input: {path}")
    if not require_image and not _same_number(
        summary.get("numeric_sequence_complete_rate"), 1.0
    ):
        raise ValueError(f"brain numeric quality gate failed: {path}")


def _content_hash_sequence(record: dict[str, Any]) -> list[str]:
    return [
        str(sample["content_sha256"])
        for batch in record.get("batches") or []
        for sample in batch.get("samples") or []
        if sample.get("content_sha256")
    ]


def _number(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


def _nonempty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _flat_items(value: dict[str, Any], prefix: str = ""):
    for key, item in value.items():
        full_key = f"{prefix} {key}".strip()
        if isinstance(item, dict):
            yield from _flat_items(item, full_key)
        else:
            yield full_key, item


def _vram_gib(path: Path) -> str:
    if not path.is_file():
        return "—"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "—"
    values: list[float] = []
    if isinstance(document, dict):
        for key, value in _flat_items(document):
            normalized = " ".join(key.lower().replace("_", " ").split())
            if "vram" in normalized and "used" in normalized and "memory" in normalized:
                match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", str(value))
                if match:
                    values.append(float(match.group(0)))
    if not values:
        return "—"
    return f"{sum(values) / (1024 ** 3):.2f}"


def _peak_vram_gib(path: Path) -> str:
    if not path.is_file():
        return "—"
    value = int(path.read_text(encoding="utf-8").strip())
    if value <= 0:
        raise ValueError(f"invalid peak VRAM sample: {path}")
    return f"{value / (1024 ** 3):.2f}"


def _brain_rows(
    results: Path,
    manifest: dict[str, str],
    manifest_sha256: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in results.glob("brain-*-mtp-*-c*.json"):
        match = BRAIN_RE.match(path.name)
        if not match:
            continue
        quant, mtp, concurrency = match.groups()
        record = _load(path)
        _validate_record(
            path,
            record,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            concurrency=int(concurrency),
            require_draft=mtp == "on",
            require_image=False,
            expected_model="brain-bench",
            max_tokens_field="brain_max_tokens",
            prompt_sha_field="brain_prompt_sha256",
        )
        summary = record["summary"]
        rows.append(
            {
                "quant": quant,
                "mtp": mtp,
                "concurrency": int(concurrency),
                "prefill": summary.get("prompt_tps_median"),
                "decode": summary.get("decode_tps_median_per_request"),
                "aggregate": summary.get("aggregate_output_tps_end_to_end_median"),
                "p95": summary.get("request_wall_p95_ms"),
                "resident_vram": _vram_gib(
                    results / f"brain-{quant}-mtp-{mtp}-resident-rocm-smi.json"
                ),
                "peak_vram": _peak_vram_gib(
                    results
                    / f"brain-{quant}-mtp-{mtp}-c{concurrency}-peak-vram-bytes.txt"
                ),
                "quality": summary.get("numeric_sequence_prefix_median"),
                "draft_n": summary.get("draft_n"),
                "draft_accepted": summary.get("draft_n_accepted"),
                "draft_ratio": summary.get("draft_acceptance_ratio"),
                "content_hash_sequence": _content_hash_sequence(record),
                "quality_pass_rate": summary.get(
                    "numeric_sequence_complete_rate"
                ),
                "gpu_proof": _nonempty_file(
                    results / f"brain-{quant}-mtp-{mtp}-gpu-proof.txt"
                ),
                "trials": summary.get("trial_count"),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            QUANT_ORDER[row["quant"]],
            0 if row["mtp"] == "off" else 1,
            row["concurrency"],
        ),
    )


def _perceive_rows(
    results: Path,
    manifest: dict[str, str],
    manifest_sha256: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in results.glob("perceive-Q8_0-np*-c*.json"):
        match = PERCEIVE_RE.match(path.name)
        if not match:
            continue
        parallel = match.group("parallel")
        concurrency = parallel
        record = _load(path)
        _validate_record(
            path,
            record,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            concurrency=int(concurrency),
            require_draft=False,
            require_image=True,
            expected_model="perceive-bench",
            max_tokens_field="perceive_max_tokens",
            prompt_sha_field="perceive_prompt_sha256",
        )
        summary = record["summary"]
        rows.append(
            {
                "parallel": int(parallel),
                "concurrency": int(concurrency),
                "prefill": summary.get("prompt_tps_median"),
                "decode": summary.get("decode_tps_median_per_request"),
                "aggregate": summary.get("aggregate_output_tps_end_to_end_median"),
                "p95": summary.get("request_wall_p95_ms"),
                "resident_vram": _vram_gib(
                    results / f"perceive-np{parallel}-resident-rocm-smi.json"
                ),
                "peak_vram": _peak_vram_gib(
                    results
                    / f"perceive-Q8_0-np{parallel}-c{concurrency}-peak-vram-bytes.txt"
                ),
                "trials": summary.get("trial_count"),
                "image_sha256": (record.get("method") or {}).get("image_sha256"),
                "visual_match_rate": summary.get(
                    "required_content_match_rate"
                ),
                "gpu_proof": _nonempty_file(
                    results / f"perceive-np{parallel}-gpu-proof.txt"
                ),
            }
        )
    return sorted(rows, key=lambda row: row["parallel"])


def _mtp_speedups(rows: list[dict[str, Any]]) -> list[tuple[str, int, float]]:
    lookup = {
        (row["quant"], row["mtp"], row["concurrency"]): row for row in rows
    }
    comparisons: list[tuple[str, int, float]] = []
    for quant in QUANT_ORDER:
        for concurrency in (1, 4, 8):
            off = lookup.get((quant, "off", concurrency))
            on = lookup.get((quant, "on", concurrency))
            if not off or not on:
                continue
            off_value = off.get("aggregate")
            on_value = on.get("aggregate")
            if off_value and on_value:
                comparisons.append(
                    (quant, concurrency, float(on_value) / float(off_value))
                )
    return comparisons


def _mtp_output_mismatches(rows: list[dict[str, Any]]) -> list[tuple[str, int]]:
    lookup = {
        (row["quant"], row["mtp"], row["concurrency"]): row for row in rows
    }
    mismatches: list[tuple[str, int]] = []
    for quant in QUANT_ORDER:
        for concurrency in (1, 4, 8):
            off = lookup.get((quant, "off", concurrency))
            on = lookup.get((quant, "on", concurrency))
            if not off or not on:
                continue
            if off["content_hash_sequence"] != on["content_hash_sequence"]:
                mismatches.append((quant, concurrency))
    return mismatches


def _assert_complete_metrics(
    brain: list[dict[str, Any]],
    perceive: list[dict[str, Any]],
) -> None:
    required_numeric = ("prefill", "decode", "aggregate", "p95")
    for row in [*brain, *perceive]:
        missing = [field for field in required_numeric if row.get(field) is None]
        if missing:
            raise ValueError(f"missing timing metrics in row: {missing}")
        if row["resident_vram"] == "—" or row["peak_vram"] == "—":
            raise ValueError("missing resident or peak VRAM evidence")
        if not row.get("gpu_proof"):
            raise ValueError("missing KFD/HIP/full-offload proof")
    if any(not _same_number(row.get("quality_pass_rate"), 1.0) for row in brain):
        raise ValueError("one or more brain cells failed the 1..80 quality gate")
    if any(not _same_number(row.get("visual_match_rate"), 1.0) for row in perceive):
        raise ValueError("one or more perceive cells failed the visual-text gate")
    image_hashes = {row.get("image_sha256") for row in perceive}
    if perceive and image_hashes != {PERCEIVE_IMAGE_SHA256}:
        raise ValueError("perceive cells did not use one identical image fixture")


def _expected_matrix_counts(mode: str) -> tuple[int, int]:
    expected = {
        "all": (3 * 2 * 3, 3),
        "brain": (3 * 2 * 3, 0),
        "perceive": (0, 3),
    }
    try:
        return expected[mode]
    except KeyError as exc:
        raise ValueError(f"invalid run manifest mode: {mode!r}") from exc


def _assert_supporting_evidence(
    results: Path,
    manifest: dict[str, str],
) -> None:
    required_nonempty = (
        "before-rocm-smi.txt",
        "before-rocm-smi.json",
        "after-rocm-smi.txt",
        "after-rocm-smi.json",
        "environment.txt",
        "weights-verified.txt",
        "llama-build-verified.txt",
        "rocminfo-gfx1100.txt",
    )
    missing = [
        filename
        for filename in required_nonempty
        if not (results / filename).is_file()
        or (results / filename).stat().st_size == 0
    ]
    if missing:
        raise ValueError(f"missing supporting ROCm evidence: {missing}")
    weights_digest = hashlib.sha256(
        (results / "weights-verified.txt").read_bytes()
    ).hexdigest()
    if weights_digest != manifest["weights_manifest_sha256"]:
        raise ValueError("verified weight list differs from run manifest")
    environment = (results / "environment.txt").read_text(encoding="utf-8")
    for marker in (
        f"run_id={manifest['run_id']}",
        f"llama_cpp_commit={manifest['llama_cpp_commit']}",
        f"llama_bin_sha256={manifest['llama_bin_sha256']}",
    ):
        if marker not in environment:
            raise ValueError(f"environment evidence is missing {marker}")
    if manifest["mode"] != "brain":
        fixture = results / "perceive-fixture-verified.txt"
        if not _nonempty_file(fixture) or PERCEIVE_IMAGE_SHA256 not in (
            fixture.read_text(encoding="utf-8")
        ):
            raise ValueError("reviewed perceive fixture proof is missing")
    for filename in ("before-rocm-smi.json", "after-rocm-smi.json"):
        try:
            parsed = json.loads((results / filename).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"invalid ROCm JSON evidence: {filename}") from exc
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError(f"empty ROCm JSON evidence: {filename}")


def _render(
    results: Path,
    brain: list[dict[str, Any]],
    perceive: list[dict[str, Any]],
) -> str:
    lines = [
        "# P3.1 ROCm ablation summary",
        "",
        (
            f"Raw evidence run directory: `{results.name}/` "
            "(the directory containing this summary)."
        ),
        "Every row is derived from one warm-up plus the recorded measured batches;",
        "the benchmark driver enforces at least three trials.",
        "",
        "## Brain: quant × MTP × client concurrency",
        "",
        "| Quant | MTP | conc | prefill t/s | decode t/s/request | aggregate output t/s | request P95 ms | resident / peak VRAM GiB | draft accepted / generated | correct prefix / pass | n |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in brain:
        lines.append(
            "| {quant} | {mtp} | {concurrency} | {prefill} | {decode} | "
            "{aggregate} | {p95} | {vram} | {draft} | {quality} | {trials} |".format(
                quant=row["quant"],
                mtp=row["mtp"],
                concurrency=row["concurrency"],
                prefill=_number(row["prefill"]),
                decode=_number(row["decode"]),
                aggregate=_number(row["aggregate"]),
                p95=_number(row["p95"]),
                vram=f"{row['resident_vram']} / {row['peak_vram']}",
                draft=(
                    f"{row['draft_accepted']} / {row['draft_n']} "
                    f"({_number(100 * row['draft_ratio'])}%)"
                    if row["draft_ratio"] is not None
                    else "—"
                ),
                quality=(
                    f"{_number(row['quality'], 0)} / "
                    f"{_number(100 * row['quality_pass_rate'], 0)}%"
                ),
                trials=row["trials"],
            )
        )

    lines.extend(
        [
            "",
            "### MTP aggregate-throughput ratio (on / off)",
            "",
            "| Quant | concurrency | ratio |",
            "|---|---:|---:|",
        ]
    )
    for quant, concurrency, ratio in _mtp_speedups(brain):
        lines.append(f"| {quant} | {concurrency} | {ratio:.3f}× |")
    mismatches = _mtp_output_mismatches(brain)
    lines.extend(
        [
            "",
            (
                "Deterministic MTP output parity: **PASS**."
                if not mismatches
                else "Deterministic MTP output parity: **FAIL** for "
                + ", ".join(
                    f"{quant} c{concurrency}"
                    for quant, concurrency in mismatches
                )
                + ". Treat MTP as unsafe for production until reviewed."
            ),
        ]
    )

    lines.extend(
        [
            "",
            "## Perceive: server slots paired with client concurrency",
            "",
            "| Quant | server -np | client conc | prefill t/s | decode t/s/request | aggregate output t/s | request P95 ms | resident / peak VRAM GiB | visual text pass | n |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in perceive:
        lines.append(
            "| Q8_0 | {parallel} | {concurrency} | {prefill} | {decode} | "
            "{aggregate} | {p95} | {vram} | {visual} | {trials} |".format(
                parallel=row["parallel"],
                concurrency=row["concurrency"],
                prefill=_number(row["prefill"]),
                decode=_number(row["decode"]),
                aggregate=_number(row["aggregate"]),
                p95=_number(row["p95"]),
                vram=f"{row['resident_vram']} / {row['peak_vram']}",
                visual=f"{_number(100 * row['visual_match_rate'], 0)}%",
                trials=row["trials"],
            )
        )
    lines.extend(
        [
            "",
            (
                "Environment, hashes, server logs, per-request timings, and "
                "rocm-smi JSON remain beside this summary."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = _parse_args()
    manifest, manifest_sha256 = _load_manifest(args.results)
    brain = _brain_rows(args.results, manifest, manifest_sha256)
    perceive = _perceive_rows(args.results, manifest, manifest_sha256)
    expected_brain, expected_perceive = _expected_matrix_counts(manifest["mode"])
    if not args.allow_partial and (
        len(brain) != expected_brain
        or len(perceive) != expected_perceive
    ):
        raise SystemExit(
            "incomplete P3.1 evidence: "
            f"brain {len(brain)}/{expected_brain}, "
            f"perceive {len(perceive)}/{expected_perceive}"
        )
    if not args.allow_partial:
        _assert_supporting_evidence(args.results, manifest)
        _assert_complete_metrics(brain, perceive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        _render(args.results, brain, perceive),
        encoding="utf-8",
    )
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
