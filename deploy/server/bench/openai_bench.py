#!/usr/bin/env python3
"""Benchmark an OpenAI-compatible llama-server endpoint with stdlib only.

The driver uses synthetic prompts, keeps every request result auditable, and
reports medians over independent batches.  It is intentionally dependency-free
so it can run directly on the AMD compute host.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_PROMPT = (
    "Synthetic throughput benchmark. Output only the ascending integers from "
    "1 through 80, separated by one space. Do not explain."
)


def _percentile(values: list[float], percentile: float) -> float:
    """Return the nearest-rank percentile for a non-empty sample."""

    ordered = sorted(values)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    return ordered[rank - 1]


def _median_or_none(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _numeric_sequence_prefix(content: str) -> int:
    """Count the correct 1, 2, 3, ... prefix in a synthetic completion."""

    observed = [int(value) for value in re.findall(r"\d+", content)]
    matched = 0
    for expected, value in enumerate(observed, start=1):
        if value != expected:
            break
        matched += 1
    return matched


def _numeric_sequence_exact(content: str, count: int) -> bool:
    """Require exactly ``1 2 ... count`` with no extra output."""

    if count <= 0:
        return True
    expected = " ".join(str(value) for value in range(1, count + 1))
    return content == expected


def _request(
    *,
    url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    timeout: float,
    enable_thinking: bool,
    image_data_url: str | None,
    request_id: str,
    required_content_regex: str | None,
    required_numeric_prefix: int,
) -> dict[str, Any]:
    content: str | list[dict[str, Any]]
    if image_data_url is None:
        content = prompt
    else:
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
        "cache_prompt": False,
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise RuntimeError(
            f"{request_id}: HTTP {exc.code}: {raw[:500].decode('utf-8', 'replace')}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{request_id}: request failed: {exc}") from exc

    wall_ms = (time.perf_counter() - started) * 1000.0
    body = json.loads(raw)
    choices = body.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    content = message.get("content") or ""
    timings = body.get("timings") or {}
    usage = body.get("usage") or {}
    predicted_n = timings.get("predicted_n", usage.get("completion_tokens", 0))
    prompt_n = timings.get("prompt_n", usage.get("prompt_tokens", 0))
    required_timings = ("cache_n", "prompt_per_second", "predicted_per_second")
    missing_timings = [key for key in required_timings if timings.get(key) is None]
    if not choices:
        raise RuntimeError(f"{request_id}: response has no choices")
    if not content.strip():
        raise RuntimeError(f"{request_id}: response content is empty")
    if int(predicted_n or 0) <= 0:
        raise RuntimeError(f"{request_id}: response generated zero tokens")
    if missing_timings:
        raise RuntimeError(
            f"{request_id}: llama-server timings missing {missing_timings}"
        )
    if int(timings["cache_n"]) != 0:
        raise RuntimeError(
            f"{request_id}: prompt cache contaminated timing "
            f"(cache_n={timings['cache_n']})"
        )
    numeric_prefix = _numeric_sequence_prefix(content)
    numeric_exact = _numeric_sequence_exact(content, required_numeric_prefix)
    content_matches_required = (
        required_content_regex is None
        or re.search(required_content_regex, content) is not None
    )
    if not content_matches_required:
        raise RuntimeError(
            f"{request_id}: response did not match required visual text "
            f"{required_content_regex!r}: {content[:300]!r}"
        )
    if required_numeric_prefix > 0 and not numeric_exact:
        raise RuntimeError(
            f"{request_id}: response failed the exact numeric sequence "
            f"1..{required_numeric_prefix} gate "
            f"(observed prefix={numeric_prefix}): {content[:300]!r}"
        )

    return {
        "request_id": request_id,
        "http_status": status,
        "wall_ms": wall_ms,
        "cache_n": int(timings["cache_n"]),
        "prompt_n": prompt_n,
        "predicted_n": predicted_n,
        "prompt_per_second": timings.get("prompt_per_second"),
        "predicted_per_second": timings.get("predicted_per_second"),
        "prompt_ms": timings.get("prompt_ms"),
        "predicted_ms": timings.get("predicted_ms"),
        "draft_n": int(timings.get("draft_n") or 0),
        "draft_n_accepted": int(timings.get("draft_n_accepted") or 0),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content_length": len(content),
        "content_preview": content[:500],
        "numeric_sequence_prefix": numeric_prefix,
        "numeric_sequence_complete": numeric_exact,
        "numeric_sequence_exact": numeric_exact,
        "content_matches_required": content_matches_required,
        "finish_reason": choices[0].get("finish_reason") if choices else None,
        "timings": timings,
    }


def _run_batch(args: argparse.Namespace, trial: int) -> dict[str, Any]:
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.concurrency
    ) as executor:
        futures = [
            executor.submit(
                _request,
                url=args.url,
                model=args.model,
                prompt=args.prompt,
                max_tokens=args.max_tokens,
                timeout=args.timeout,
                enable_thinking=args.enable_thinking,
                image_data_url=args.image_data_url,
                request_id=f"trial-{trial}-request-{index + 1}",
                required_content_regex=args.required_content_regex,
                required_numeric_prefix=args.required_numeric_prefix,
            )
            for index in range(args.concurrency)
        ]
        samples = [future.result() for future in futures]

    batch_wall_ms = (time.perf_counter() - started) * 1000.0
    generated_tokens = sum(int(sample["predicted_n"] or 0) for sample in samples)
    prompt_tokens = sum(int(sample["prompt_n"] or 0) for sample in samples)
    return {
        "trial": trial,
        "batch_wall_ms": batch_wall_ms,
        "aggregate_output_tps_end_to_end": (
            generated_tokens / (batch_wall_ms / 1000.0) if batch_wall_ms else None
        ),
        "generated_tokens": generated_tokens,
        "prompt_tokens": prompt_tokens,
        "samples": samples,
    }


def _summarize(batches: list[dict[str, Any]]) -> dict[str, Any]:
    samples = [sample for batch in batches for sample in batch["samples"]]
    request_walls = [float(sample["wall_ms"]) for sample in samples]
    prompt_rates = [
        float(sample["prompt_per_second"])
        for sample in samples
        if sample["prompt_per_second"] is not None
    ]
    decode_rates = [
        float(sample["predicted_per_second"])
        for sample in samples
        if sample["predicted_per_second"] is not None
    ]
    batch_walls = [float(batch["batch_wall_ms"]) for batch in batches]
    aggregate_rates = [
        float(batch["aggregate_output_tps_end_to_end"])
        for batch in batches
        if batch["aggregate_output_tps_end_to_end"] is not None
    ]
    generated_counts = [float(sample["predicted_n"] or 0) for sample in samples]
    sequence_prefixes = [
        float(sample["numeric_sequence_prefix"]) for sample in samples
    ]
    draft_n = sum(int(sample["draft_n"]) for sample in samples)
    draft_n_accepted = sum(int(sample["draft_n_accepted"]) for sample in samples)
    sequence_complete = [
        bool(sample["numeric_sequence_complete"]) for sample in samples
    ]
    content_matches = [
        bool(sample["content_matches_required"]) for sample in samples
    ]

    return {
        "successful_requests": len(samples),
        "trial_count": len(batches),
        "concurrency": len(samples) // len(batches),
        "prompt_tps_median": _median_or_none(prompt_rates),
        "decode_tps_median_per_request": _median_or_none(decode_rates),
        "aggregate_output_tps_end_to_end_median": _median_or_none(aggregate_rates),
        "request_wall_p50_ms": _median_or_none(request_walls),
        "request_wall_p95_ms": _percentile(request_walls, 95),
        "batch_wall_p50_ms": _median_or_none(batch_walls),
        "batch_wall_p95_ms": _percentile(batch_walls, 95),
        "completion_tokens_median": _median_or_none(generated_counts),
        "numeric_sequence_prefix_median": _median_or_none(sequence_prefixes),
        "numeric_sequence_complete_rate": (
            sum(sequence_complete) / len(sequence_complete)
        ),
        "required_content_match_rate": sum(content_matches) / len(content_matches),
        "draft_n": draft_n,
        "draft_n_accepted": draft_n_accepted,
        "draft_acceptance_ratio": (
            draft_n_accepted / draft_n if draft_n > 0 else None
        ),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run repeatable synthetic requests against llama-server."
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:18001/v1/chat/completions",
    )
    parser.add_argument("--model", default="brain-bench")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument(
        "--image",
        type=Path,
        help="Optional fixed PNG/JPEG fixture for a multimodal request.",
    )
    parser.add_argument(
        "--require-draft",
        action="store_true",
        help="Fail unless llama-server reports speculative draft tokens.",
    )
    parser.add_argument(
        "--forbid-draft",
        action="store_true",
        help="Fail if llama-server reports any speculative draft tokens.",
    )
    parser.add_argument(
        "--enable-thinking",
        action="store_true",
        help="Enable model thinking. Default is disabled for comparable timings.",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--llama-commit", required=True)
    parser.add_argument("--llama-bin-sha256", required=True)
    parser.add_argument(
        "--required-content-regex",
        help="Fail a request unless its content matches this reviewed regex.",
    )
    parser.add_argument(
        "--required-numeric-prefix",
        type=int,
        default=0,
        help="Record whether each response reaches this exact 1..N prefix.",
    )
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.concurrency < 1 or args.runs < 3 or args.warmup < 0:
        parser.error("concurrency must be >=1, runs >=3, and warmup >=0")
    if args.require_draft and args.forbid_draft:
        parser.error("--require-draft and --forbid-draft are mutually exclusive")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.run_id):
        parser.error("run-id contains unsupported characters")
    for label, value in (
        ("manifest-sha256", args.manifest_sha256),
        ("llama-bin-sha256", args.llama_bin_sha256),
    ):
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            parser.error(f"{label} must be 64 lowercase hexadecimal characters")
    if re.fullmatch(r"[0-9a-f]{7,40}", args.llama_commit) is None:
        parser.error("llama-commit must be a hexadecimal git revision")
    if args.required_numeric_prefix < 0:
        parser.error("required-numeric-prefix must be >= 0")
    if args.required_content_regex is not None:
        try:
            re.compile(args.required_content_regex)
        except re.error as exc:
            parser.error(f"invalid required-content-regex: {exc}")
    args.image_data_url = None
    args.image_sha256 = None
    if args.image is not None:
        if not args.image.is_file():
            parser.error(f"image fixture does not exist: {args.image}")
        suffix = args.image.suffix.lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
            suffix.lstrip(".")
        )
        if mime is None:
            parser.error("image fixture must be PNG or JPEG")
        image_bytes = args.image.read_bytes()
        args.image_sha256 = hashlib.sha256(image_bytes).hexdigest()
        args.image_data_url = (
            f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        )
    return args


def main() -> int:
    args = _parse_args()
    for index in range(args.warmup):
        _run_batch(args, -(index + 1))

    batches = [_run_batch(args, trial + 1) for trial in range(args.runs)]
    drafted = sum(
        int(sample["draft_n"])
        for batch in batches
        for sample in batch["samples"]
    )
    if args.require_draft and drafted <= 0:
        raise RuntimeError(
            "MTP was requested but llama-server reported zero draft tokens"
        )
    if args.forbid_draft and drafted > 0:
        raise RuntimeError(
            "MTP was disabled but llama-server reported speculative draft tokens"
        )
    record = {
        "schema_version": 2,
        "label": args.label,
        "measured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "evidence": {
            "run_id": args.run_id,
            "manifest_sha256": args.manifest_sha256,
            "llama_commit": args.llama_commit,
            "llama_bin_sha256": args.llama_bin_sha256,
        },
        "method": {
            "url": args.url,
            "model": args.model,
            "concurrency": args.concurrency,
            "runs": args.runs,
            "warmup_batches": args.warmup,
            "max_tokens": args.max_tokens,
            "temperature": 0,
            "cache_prompt": False,
            "enable_thinking": args.enable_thinking,
            "required_content_regex": args.required_content_regex,
            "required_numeric_prefix": args.required_numeric_prefix,
            "image_sha256": args.image_sha256,
            "require_draft": args.require_draft,
            "forbid_draft": args.forbid_draft,
            "synthetic_prompt_sha256": hashlib.sha256(
                args.prompt.encode("utf-8")
            ).hexdigest(),
        },
        "summary": _summarize(batches),
        "batches": batches,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    json.dump(record["summary"], sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
