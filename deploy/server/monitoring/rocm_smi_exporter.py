#!/usr/bin/env python3
"""Expose the local AMD GPU's rocm-smi JSON as Prometheus metrics."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")


def _number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUMBER.search(str(value).replace(",", ""))
    if not match:
        raise ValueError(f"no number in {value!r}")
    return float(match.group(0))


def _flat_items(value: Mapping[str, Any], prefix: str = ""):
    for key, item in value.items():
        full_key = f"{prefix} {key}".strip()
        if isinstance(item, Mapping):
            yield from _flat_items(item, full_key)
        else:
            yield full_key, item


def _parse_card(values: Mapping[str, Any]) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for raw_key, raw_value in _flat_items(values):
        key = " ".join(raw_key.lower().replace("_", " ").split())
        if ("gpu use" in key or "gpu utilization" in key) and "memory" not in key:
            parsed["utilization_percent"] = _number(raw_value)
        elif "vram" in key and "memory" in key and "used" in key:
            parsed["vram_used_bytes"] = _number(raw_value)
        elif "vram" in key and "memory" in key and "total" in key and "used" not in key:
            parsed["vram_total_bytes"] = _number(raw_value)

    total = parsed.get("vram_total_bytes")
    used = parsed.get("vram_used_bytes")
    if total is not None and used is not None:
        parsed["vram_free_bytes"] = max(0.0, total - used)
        parsed["vram_used_percent"] = 100.0 * used / total if total else 0.0
    return parsed


def parse_rocm_smi_json(raw: str) -> dict[str, dict[str, float]]:
    document = json.loads(raw)
    if not isinstance(document, Mapping):
        raise TypeError("rocm-smi JSON root is not an object")

    cards: dict[str, dict[str, float]] = {}
    for name, values in document.items():
        if not isinstance(values, Mapping):
            continue
        parsed = _parse_card(values)
        if parsed:
            cards[str(name)] = parsed
    if not cards:
        raise ValueError("rocm-smi JSON contained no GPU measurements")
    return cards


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def render_prometheus(cards: Mapping[str, Mapping[str, float]]) -> str:
    definitions = (
        (
            "dejaview_rocm_gpu_utilization_percent",
            "AMD GPU busy percentage reported by rocm-smi.",
            "utilization_percent",
        ),
        (
            "dejaview_rocm_vram_used_bytes",
            "AMD GPU VRAM bytes in use.",
            "vram_used_bytes",
        ),
        (
            "dejaview_rocm_vram_total_bytes",
            "AMD GPU total VRAM bytes.",
            "vram_total_bytes",
        ),
        (
            "dejaview_rocm_vram_free_bytes",
            "AMD GPU free VRAM bytes.",
            "vram_free_bytes",
        ),
        (
            "dejaview_rocm_vram_used_percent",
            "AMD GPU VRAM utilization percentage.",
            "vram_used_percent",
        ),
    )
    lines = [
        "# HELP dejaview_rocm_exporter_scrape_success Whether rocm-smi was parsed.",
        "# TYPE dejaview_rocm_exporter_scrape_success gauge",
        "dejaview_rocm_exporter_scrape_success 1",
    ]
    for metric, help_text, field in definitions:
        lines.extend((f"# HELP {metric} {help_text}", f"# TYPE {metric} gauge"))
        for card, values in sorted(cards.items()):
            if field in values:
                lines.append(
                    f'{metric}{{gpu="{_escape_label(card)}"}} {values[field]:.6f}'
                )
    return "\n".join(lines) + "\n"


def collect(rocm_smi_bin: str) -> str:
    process = subprocess.run(
        [
            rocm_smi_bin,
            "--showuse",
            "--showmeminfo",
            "vram",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return render_prometheus(parse_rocm_smi_json(process.stdout))


class MetricsHandler(BaseHTTPRequestHandler):
    rocm_smi_bin = "rocm-smi"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._write(HTTPStatus.OK, "ok\n", "text/plain; charset=utf-8")
            return
        if self.path != "/metrics":
            self._write(HTTPStatus.NOT_FOUND, "not found\n", "text/plain")
            return
        try:
            body = collect(self.rocm_smi_bin)
        except (
            json.JSONDecodeError,
            OSError,
            subprocess.SubprocessError,
            TypeError,
            ValueError,
        ) as exc:
            body = (
                "# HELP dejaview_rocm_exporter_scrape_success "
                "Whether rocm-smi was parsed.\n"
                "# TYPE dejaview_rocm_exporter_scrape_success gauge\n"
                "dejaview_rocm_exporter_scrape_success 0\n"
                f"# scrape_error {type(exc).__name__}\n"
            )
        self._write(
            HTTPStatus.OK,
            body,
            "text/plain; version=0.0.4; charset=utf-8",
        )

    def _write(self, status: HTTPStatus, body: str, content_type: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9393)
    parser.add_argument("--rocm-smi-bin", default="rocm-smi")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    MetricsHandler.rocm_smi_bin = args.rocm_smi_bin
    server = ThreadingHTTPServer((args.host, args.port), MetricsHandler)
    print(f"rocm-smi metrics listening on http://{args.host}:{args.port}/metrics")
    server.serve_forever()


if __name__ == "__main__":
    main()
