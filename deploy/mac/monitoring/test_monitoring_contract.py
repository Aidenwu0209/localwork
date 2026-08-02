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
        exporter = self.panels["ROCm采集"]
        series = self.panels["GPU数量"]

        self.assertEqual(
            exporter["targets"][0]["expr"],
            'min(dejaview_rocm_exporter_scrape_success{job="rocm"} '
            'or on (job, instance) (0 * up{job="rocm"}))',
        )
        self.assertEqual(
            series["targets"][0]["expr"],
            'count(dejaview_rocm_gpu_utilization_percent{job="rocm"}) or vector(0)',
        )
        self.assertEqual(series["gridPos"], {"h": 3, "w": 4, "x": 12, "y": 3})

    def test_dashboard_has_required_role_health_and_throughput_gates(self) -> None:
        health = self.panels["常驻模型"]
        throughput = self.panels["活跃吞吐"]

        self.assertEqual(
            health["targets"][0]["expr"],
            f'sum(up{{job="llama",role=~"{REQUIRED_ROLES}"}} or vector(0))',
        )
        self.assertIn("predicted_tokens_seconds", throughput["targets"][0]["expr"])
        self.assertIn("prompt_tokens_seconds", throughput["targets"][0]["expr"])
        self.assertIn(f'role=~"{REQUIRED_ROLES}"', throughput["targets"][0]["expr"])
        self.assertEqual(throughput["gridPos"], {"h": 3, "w": 4, "x": 20, "y": 3})

    def test_dashboard_has_system_selfcheck_row(self) -> None:
        expected_positions = {
            "系统状态": {"h": 3, "w": 6, "x": 0, "y": 0},
            "数据新鲜度": {"h": 3, "w": 6, "x": 6, "y": 0},
            "本机核心": {"h": 3, "w": 6, "x": 12, "y": 0},
            "算力路径": {"h": 3, "w": 6, "x": 18, "y": 0},
        }
        for title, position in expected_positions.items():
            self.assertEqual(self.panels[title]["gridPos"], position)

        self.assertEqual(
            self.panels["系统状态"]["targets"][0]["expr"],
            "max(dejaview_selfcheck_state) or vector(0)",
        )
        self.assertIn(
            "dejaview_selfcheck_last_probe_unixtime",
            self.panels["数据新鲜度"]["targets"][0]["expr"],
        )
        self.assertIn(
            'component=~"database|redis|honcho|ocrd|memoryd|agentd"',
            self.panels["本机核心"]["targets"][0]["expr"],
        )
        self.assertEqual(
            self.panels["算力路径"]["targets"][0]["expr"],
            "max(dejaview_compute_path_state) or vector(0)",
        )

    def test_dashboard_no_data_is_never_green_and_layout_stays_one_screen(self) -> None:
        for title in ("GPU", "VRAM"):
            defaults = self.panels[title]["fieldConfig"]["defaults"]
            self.assertEqual(defaults["noValue"], "NO DATA")
            special = [
                mapping
                for mapping in defaults["mappings"]
                if mapping.get("type") == "special"
            ]
            self.assertEqual(len(special), 1)
            self.assertEqual(special[0]["options"]["match"], "null")
            self.assertNotEqual(special[0]["options"]["result"]["color"], "green")

        self.assertEqual(
            self.panels["数据新鲜度"]["fieldConfig"]["defaults"]["noValue"],
            "STALE",
        )
        self.assertTrue(
            self.panels["记忆流水线 · events/min"]["targets"][0]["expr"].endswith(
                "or vector(0)"
            )
        )
        self.assertLessEqual(
            max(
                panel["gridPos"]["y"] + panel["gridPos"]["h"]
                for panel in self.dashboard["panels"]
            ),
            18,
        )

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

    def test_selfcheck_exporter_is_private_healthy_and_scraped(self) -> None:
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
        services = json.loads(rendered.stdout)["services"]
        exporter = services["health-exporter"]

        self.assertEqual(exporter["image"], "python:3.12-alpine")
        self.assertNotIn("ports", exporter)
        self.assertIn(
            "http://127.0.0.1:9400/health",
            exporter["healthcheck"]["test"][1],
        )
        self.assertEqual(
            services["prometheus"]["depends_on"]["health-exporter"]["condition"],
            "service_healthy",
        )
        self.assertIn("job_name: dejaview-selfcheck", self.prometheus)
        self.assertIn('targets: ["health-exporter:9400"]', self.prometheus)


if __name__ == "__main__":
    unittest.main()
