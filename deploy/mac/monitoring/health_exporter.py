#!/usr/bin/env python3
"""Active, privacy-safe DejaView health probes for Prometheus.

The exporter checks service contracts and compute paths only. It never reads
timeline rows, screenshots, credentials, or environment files.
"""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Optional
from urllib import request

LOGICAL_MODELS = frozenset({"brain", "perceive", "sentinel", "fast", "embed"})
LOCAL_CORE = ("database", "redis", "honcho", "ocrd", "memoryd", "agentd")
COMPONENT_ORDER = LOCAL_CORE + ("radeon_tunnel", "local_fallback")
COMPONENT_LAYERS = {
    "database": "local_data",
    "redis": "local_data",
    "honcho": "memory",
    "ocrd": "perception",
    "memoryd": "memory",
    "agentd": "agent",
    "radeon_tunnel": "compute",
    "local_fallback": "compute",
}
DEFAULT_PORTS = {
    "database": 5433,
    "redis": 6380,
    "honcho": 8100,
    "ocrd": 8006,
    "memoryd": 8090,
    "agentd": 8101,
    "radeon_tunnel": 14000,
    "local_fallback": 4000,
}


@dataclass(frozen=True)
class ProbeResult:
    healthy: bool
    latency_seconds: float
    detail: str = ""


Validator = Callable[[Mapping[str, Any]], None]


def derive_states(results: Mapping[str, ProbeResult]) -> tuple[int, int]:
    """Return ``(system_state, compute_path)`` using fail-closed semantics.

    System state: 2 READY, 1 DEGRADED, 0 FAILED.
    Compute path: 2 RADEON, 1 LOCAL, 0 NONE.
    """

    core_ready = all(results.get(name, ProbeResult(False, 0.0)).healthy for name in LOCAL_CORE)
    remote_ready = results.get("radeon_tunnel", ProbeResult(False, 0.0)).healthy
    local_ready = results.get("local_fallback", ProbeResult(False, 0.0)).healthy
    compute_path = 2 if remote_ready else 1 if local_ready else 0
    system_state = 2 if core_ready and remote_ready else 1 if core_ready and local_ready else 0
    return system_state, compute_path


def build_local_fallback_request() -> dict[str, Any]:
    return {
        "model": "fast",
        "messages": [{"role": "user", "content": "Reply only OK"}],
        "max_tokens": 8,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def _validate_status(payload: Mapping[str, Any], *, service: str) -> None:
    if payload.get("status") != "ok":
        raise ValueError(f"{service} status is not ok")


def validate_honcho(payload: Mapping[str, Any]) -> None:
    _validate_status(payload, service="honcho")


def validate_ocrd(payload: Mapping[str, Any]) -> None:
    _validate_status(payload, service="ocrd")
    if not payload.get("backend"):
        raise ValueError("ocrd backend is missing")


def validate_memoryd(payload: Mapping[str, Any]) -> None:
    _validate_status(payload, service="memoryd")
    if payload.get("pipeline") != "real":
        raise ValueError("memoryd pipeline is not real")


def validate_agentd(payload: Mapping[str, Any]) -> None:
    _validate_status(payload, service="agentd")
    if payload.get("service") != "agentd":
        raise ValueError("agentd service identity mismatch")


def validate_models(payload: Mapping[str, Any]) -> None:
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("model list is missing")
    actual = {
        str(item.get("id"))
        for item in data
        if isinstance(item, Mapping) and item.get("id")
    }
    missing = sorted(LOGICAL_MODELS - actual)
    if missing:
        raise ValueError("missing models: " + ",".join(missing))


def probe_tcp(host: str, port: int, *, timeout: float) -> ProbeResult:
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return ProbeResult(True, time.monotonic() - started)
    except OSError as exc:
        return ProbeResult(False, time.monotonic() - started, type(exc).__name__)


def probe_json_url(url: str, validator: Validator, *, timeout: float) -> ProbeResult:
    started = time.monotonic()
    try:
        with request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read())
        if not isinstance(payload, Mapping):
            raise ValueError("JSON response is not an object")
        validator(payload)
        return ProbeResult(True, time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 - probe failures become metrics
        return ProbeResult(False, time.monotonic() - started, str(exc)[:160])


def probe_local_fallback(url: str, *, timeout: float) -> ProbeResult:
    started = time.monotonic()
    body = json.dumps(build_local_fallback_request()).encode()
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read())
        content = payload["choices"][0]["message"]["content"]
        if str(content).strip() != "OK":
            raise ValueError("fast smoke did not return OK")
        return ProbeResult(True, time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 - probe failures become metrics
        return ProbeResult(False, time.monotonic() - started, str(exc)[:160])


def render_metrics(
    *,
    results: Mapping[str, ProbeResult],
    last_success: Mapping[str, float],
    probed_at: float,
) -> str:
    system_state, compute_path = derive_states(results)
    lines = [
        "# HELP dejaview_component_up DejaView component contract is healthy.",
        "# TYPE dejaview_component_up gauge",
    ]
    for name in COMPONENT_ORDER:
        result = results.get(name, ProbeResult(False, 0.0, "not probed"))
        labels = f'component="{name}",layer="{COMPONENT_LAYERS[name]}"'
        lines.append(f"dejaview_component_up{{{labels}}} {1 if result.healthy else 0}")
    lines.extend(
        [
            "# HELP dejaview_component_latency_seconds Latest active probe latency.",
            "# TYPE dejaview_component_latency_seconds gauge",
        ]
    )
    for name in COMPONENT_ORDER:
        result = results.get(name, ProbeResult(False, 0.0, "not probed"))
        labels = f'component="{name}",layer="{COMPONENT_LAYERS[name]}"'
        lines.append(
            f"dejaview_component_latency_seconds{{{labels}}} "
            f"{result.latency_seconds:.6f}"
        )
    lines.extend(
        [
            "# HELP dejaview_component_last_success_unixtime Last successful active probe.",
            "# TYPE dejaview_component_last_success_unixtime gauge",
        ]
    )
    for name in COMPONENT_ORDER:
        labels = f'component="{name}",layer="{COMPONENT_LAYERS[name]}"'
        lines.append(
            f"dejaview_component_last_success_unixtime{{{labels}}} "
            f"{last_success.get(name, 0.0):.6f}"
        )
    lines.extend(
        [
            "# HELP dejaview_selfcheck_last_probe_unixtime Latest completed probe cycle.",
            "# TYPE dejaview_selfcheck_last_probe_unixtime gauge",
            f"dejaview_selfcheck_last_probe_unixtime {probed_at:.6f}",
            "# HELP dejaview_selfcheck_state Overall self-check: 0 failed, 1 degraded, 2 ready.",
            "# TYPE dejaview_selfcheck_state gauge",
            f"dejaview_selfcheck_state {system_state}",
            "# HELP dejaview_compute_path_state Compute path: 0 none, 1 local, 2 Radeon.",
            "# TYPE dejaview_compute_path_state gauge",
            f"dejaview_compute_path_state {compute_path}",
        ]
    )
    return "\n".join(lines) + "\n"


class SelfCheck:
    def __init__(
        self,
        *,
        host: str,
        ports: Optional[Mapping[str, int]] = None,
        timeout: float = 2.0,
        fallback_timeout: float = 20.0,
        fallback_ttl: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.host = host
        self.ports = dict(DEFAULT_PORTS if ports is None else ports)
        self.timeout = timeout
        self.fallback_timeout = fallback_timeout
        self.fallback_ttl = fallback_ttl
        self.clock = clock
        self._lock = threading.Lock()
        self._cycle_lock = threading.Lock()
        self._fallback_cache: Optional[tuple[float, ProbeResult]] = None
        self.results = {
            name: ProbeResult(False, 0.0, "not probed") for name in COMPONENT_ORDER
        }
        self.last_success = {name: 0.0 for name in COMPONENT_ORDER}
        self.probed_at = 0.0

    def _url(self, component: str, path: str) -> str:
        return f"http://{self.host}:{self.ports[component]}{path}"

    def _probe_named(self, name: str) -> ProbeResult:
        if name in {"database", "redis"}:
            return probe_tcp(self.host, self.ports[name], timeout=self.timeout)
        if name == "honcho":
            return probe_json_url(
                self._url(name, "/health"), validate_honcho, timeout=self.timeout
            )
        if name == "ocrd":
            return probe_json_url(
                self._url(name, "/health"), validate_ocrd, timeout=self.timeout
            )
        if name == "memoryd":
            return probe_json_url(
                self._url(name, "/health"), validate_memoryd, timeout=self.timeout
            )
        if name == "agentd":
            return probe_json_url(
                self._url(name, "/health"), validate_agentd, timeout=self.timeout
            )
        if name == "radeon_tunnel":
            return probe_json_url(
                self._url(name, "/v1/models"), validate_models, timeout=self.timeout
            )
        raise KeyError(name)

    def _probe_fallback(self, now: float) -> ProbeResult:
        if self._fallback_cache is not None and now < self._fallback_cache[0]:
            return self._fallback_cache[1]
        result = probe_local_fallback(
            self._url("local_fallback", "/v1/chat/completions"),
            timeout=self.fallback_timeout,
        )
        self._fallback_cache = (now + self.fallback_ttl, result)
        return result

    def run_cycle(self) -> None:
        if not self._cycle_lock.acquire(blocking=False):
            return
        try:
            now = self.clock()
            cycle_results: dict[str, ProbeResult] = {}
            with ThreadPoolExecutor(max_workers=len(COMPONENT_ORDER)) as pool:
                futures = {
                    pool.submit(self._probe_named, name): name
                    for name in COMPONENT_ORDER
                    if name != "local_fallback"
                }
                futures[pool.submit(self._probe_fallback, now)] = "local_fallback"
                for future in as_completed(futures):
                    cycle_results[futures[future]] = future.result()
            with self._lock:
                self.results = cycle_results
                for name, result in cycle_results.items():
                    if result.healthy:
                        self.last_success[name] = now
                self.probed_at = now
        finally:
            self._cycle_lock.release()

    def metrics(self) -> str:
        with self._lock:
            return render_metrics(
                results=dict(self.results),
                last_success=dict(self.last_success),
                probed_at=self.probed_at,
            )

    def run_forever(self, *, interval: float, stop: threading.Event) -> None:
        while not stop.is_set():
            self.run_cycle()
            stop.wait(interval)


class ExporterHandler(BaseHTTPRequestHandler):
    checker: SelfCheck

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path == "/health":
            self._respond(
                json.dumps({"status": "ok"}).encode(),
                "application/json",
            )
            return
        if self.path == "/metrics":
            self._respond(
                self.checker.metrics().encode(),
                "text/plain; version=0.0.4; charset=utf-8",
            )
            return
        self.send_error(404)

    def _respond(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="host.docker.internal")
    parser.add_argument("--port", type=int, default=9400)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--fallback-timeout", type=float, default=20.0)
    parser.add_argument("--fallback-ttl", type=float, default=30.0)
    args = parser.parse_args()

    checker = SelfCheck(
        host=args.host,
        timeout=args.timeout,
        fallback_timeout=args.fallback_timeout,
        fallback_ttl=args.fallback_ttl,
    )
    ExporterHandler.checker = checker
    stop = threading.Event()
    worker = threading.Thread(
        target=checker.run_forever,
        kwargs={"interval": args.interval, "stop": stop},
        daemon=True,
    )
    worker.start()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), ExporterHandler)
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=max(args.interval, 1.0) + 1.0)


if __name__ == "__main__":
    main()
