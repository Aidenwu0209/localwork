"""Static P3.2 contract checks; intentionally no Docker or live endpoints."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

MONITORING_DIR = Path(__file__).resolve().parent
COMPOSE_PATH = MONITORING_DIR.parent / "compose.monitoring.yml"
PROMETHEUS_PATH = MONITORING_DIR / "prometheus.yml"
DASHBOARD_PATH = MONITORING_DIR / "grafana" / "dashboards" / "dejaview-rocm-live.json"
REQUIRED_ROLES = "perceive|sentinel|embed|fast"


class MonitoringContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = COMPOSE_PATH.read_text()
        cls.prometheus = PROMETHEUS_PATH.read_text()
        cls.dashboard = json.loads(DASHBOARD_PATH.read_text())
        cls.panels = {panel["title"]: panel for panel in cls.dashboard["panels"]}

    def test_dashboard_has_exporter_and_exactly_one_gpu_gates(self) -> None:
        exporter = self.panels["ROCm exporter scrape success"]
        series = self.panels["GPU series count · must be 1"]

        self.assertEqual(
            exporter["targets"][0]["expr"],
            'min(dejaview_rocm_exporter_scrape_success{job="rocm"} '
            'or on (job, instance) (0 * up{job="rocm"}))',
        )
        self.assertEqual(
            series["targets"][0]["expr"],
            'count(dejaview_rocm_gpu_utilization_percent{job="rocm"}) or vector(0)',
        )
        self.assertEqual(series["gridPos"], {"h": 4, "w": 4, "x": 12, "y": 0})

    def test_dashboard_has_required_role_health_and_throughput_gates(self) -> None:
        health = self.panels["Required role health · must be 4"]
        throughput = self.panels["Required roles with tokens/s > 0 · must be 4"]

        self.assertEqual(
            health["targets"][0]["expr"],
            f'sum(up{{job="llama",role=~"{REQUIRED_ROLES}"}} or vector(0))',
        )
        self.assertIn("predicted_tokens_seconds", throughput["targets"][0]["expr"])
        self.assertIn("prompt_tokens_seconds", throughput["targets"][0]["expr"])
        self.assertIn(f'role=~"{REQUIRED_ROLES}"', throughput["targets"][0]["expr"])
        self.assertEqual(throughput["gridPos"], {"h": 4, "w": 4, "x": 20, "y": 0})

    def test_compose_waits_for_real_prometheus_and_grafana_health(self) -> None:
        self.assertIn("condition: service_healthy", self.compose)
        self.assertIn("http://127.0.0.1:9090/-/ready", self.compose)
        self.assertIn("http://127.0.0.1:3000/api/health", self.compose)

        rendered = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                COMPOSE_PATH,
                "config",
                "--format",
                "json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        health_command = json.loads(rendered.stdout)["services"]["grafana"][
            "healthcheck"
        ]["test"][1]
        probe = health_command.replace(
            "wget -qO- http://127.0.0.1:3000/api/health",
            "printf '%s\\n' '{\n  \"database\": \"ok\"\n}'",
        )
        subprocess.run(["/bin/sh", "-c", probe], check=True)

    def test_prometheus_keeps_all_gated_scrape_targets(self) -> None:
        for role in ("brain", "perceive", "sentinel", "embed", "fast"):
            self.assertIn(f"role: {role}", self.prometheus)
        self.assertIn('targets: ["host.docker.internal:19393"]', self.prometheus)
        self.assertIn('targets: ["host.docker.internal:8090"]', self.prometheus)


if __name__ == "__main__":
    unittest.main()
