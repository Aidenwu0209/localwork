"""Behavior tests for the local DejaView self-check exporter."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import health_exporter as health  # noqa: E402


def _result(healthy: bool, latency: float = 0.01) -> health.ProbeResult:
    return health.ProbeResult(healthy=healthy, latency_seconds=latency)


def _healthy_core() -> dict[str, health.ProbeResult]:
    return {name: _result(True) for name in health.LOCAL_CORE}


class SelfCheckStateTest(unittest.TestCase):
    def test_state_machine_distinguishes_ready_degraded_and_failed(self) -> None:
        cases = [
            (
                _healthy_core()
                | {"radeon_tunnel": _result(True), "local_fallback": _result(False)},
                (2, 2),
            ),
            (
                _healthy_core()
                | {"radeon_tunnel": _result(False), "local_fallback": _result(True)},
                (1, 1),
            ),
            (
                (_healthy_core() | {"agentd": _result(False)})
                | {"radeon_tunnel": _result(True), "local_fallback": _result(True)},
                (0, 2),
            ),
            (
                _healthy_core()
                | {"radeon_tunnel": _result(False), "local_fallback": _result(False)},
                (0, 0),
            ),
        ]

        for results, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(health.derive_states(results), expected)

    def test_local_fallback_payload_disables_thinking(self) -> None:
        self.assertEqual(
            health.build_local_fallback_request(),
            {
                "model": "fast",
                "messages": [{"role": "user", "content": "Reply only OK"}],
                "max_tokens": 8,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )

    def test_metrics_report_components_states_and_probe_time(self) -> None:
        results = _healthy_core() | {
            "radeon_tunnel": _result(True, 0.25),
            "local_fallback": _result(False, 0.5),
        }
        last_success = {name: 1234.0 for name in results}
        last_success["local_fallback"] = 0.0

        rendered = health.render_metrics(
            results=results,
            last_success=last_success,
            probed_at=1235.0,
        )

        self.assertIn(
            'dejaview_component_up{component="radeon_tunnel",layer="compute"} 1',
            rendered,
        )
        self.assertIn(
            'dejaview_component_last_success_unixtime{component="local_fallback",layer="compute"} 0.000000',
            rendered,
        )
        self.assertIn("dejaview_selfcheck_last_probe_unixtime 1235.000000", rendered)
        self.assertIn("dejaview_selfcheck_state 2", rendered)
        self.assertIn("dejaview_compute_path_state 2", rendered)


class _FixtureHandler(BaseHTTPRequestHandler):
    models = {"brain", "perceive", "sentinel", "fast", "embed"}
    fallback_requests: list[dict] = []

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        payloads = {
            "/honcho": {"status": "ok"},
            "/ocrd": {"status": "ok", "backend": "paddleocr"},
            "/memoryd": {"status": "ok", "pipeline": "real"},
            "/agentd": {"status": "ok", "service": "agentd"},
            "/models": {"data": [{"id": name} for name in sorted(self.models)]},
            "/health": {
                "status": "ok",
                "backend": "paddleocr",
                "pipeline": "real",
                "service": "agentd",
            },
            "/v1/models": {
                "data": [{"id": name} for name in sorted(self.models)]
            },
        }
        payload = payloads.get(self.path)
        if payload is None:
            self.send_error(404)
            return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).fallback_requests.append(payload)
        body = json.dumps(
            {"choices": [{"message": {"content": "OK"}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ProbeIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        _FixtureHandler.models = {"brain", "perceive", "sentinel", "fast", "embed"}
        _FixtureHandler.fallback_requests = []

    def test_json_contracts_reject_wrong_pipeline_and_missing_model(self) -> None:
        good_memory = health.probe_json_url(
            f"{self.base_url}/memoryd",
            health.validate_memoryd,
            timeout=1.0,
        )
        good_models = health.probe_json_url(
            f"{self.base_url}/models",
            health.validate_models,
            timeout=1.0,
        )
        _FixtureHandler.models.remove("brain")
        missing_model = health.probe_json_url(
            f"{self.base_url}/models",
            health.validate_models,
            timeout=1.0,
        )

        self.assertTrue(good_memory.healthy)
        self.assertTrue(good_models.healthy)
        self.assertFalse(missing_model.healthy)
        self.assertIn("brain", missing_model.detail)

    def test_live_fallback_probe_sends_safe_payload_and_requires_ok(self) -> None:
        result = health.probe_local_fallback(
            f"{self.base_url}/chat",
            timeout=1.0,
        )

        self.assertTrue(result.healthy)
        self.assertEqual(
            _FixtureHandler.fallback_requests,
            [health.build_local_fallback_request()],
        )

    def test_probe_cycle_caches_live_fallback_inside_ttl(self) -> None:
        moments = iter((100.0, 105.0))
        checker = health.SelfCheck(
            host="127.0.0.1",
            timeout=1.0,
            fallback_timeout=1.0,
            fallback_ttl=30.0,
            clock=lambda: next(moments),
        )
        fallback_calls: list[str] = []

        def fake_fallback(url: str, *, timeout: float) -> health.ProbeResult:
            fallback_calls.append(url)
            return _result(True)

        with (
            patch.object(checker, "_probe_named", return_value=_result(True)),
            patch.object(health, "probe_local_fallback", side_effect=fake_fallback),
        ):
            checker.run_cycle()
            checker.run_cycle()

        self.assertTrue(checker.results["local_fallback"].healthy)
        self.assertEqual(len(fallback_calls), 1)


if __name__ == "__main__":
    unittest.main()
