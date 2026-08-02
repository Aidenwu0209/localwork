from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REMOTE_GATEWAY = "http://host.docker.internal:14000/v1"
BASE_URL_KEYS = {
    "DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL",
    "SUMMARY_MODEL_CONFIG__OVERRIDES__BASE_URL",
    "EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL",
    "DIALECTIC_LEVELS__minimal__MODEL_CONFIG__OVERRIDES__BASE_URL",
    "DIALECTIC_LEVELS__low__MODEL_CONFIG__OVERRIDES__BASE_URL",
    "DIALECTIC_LEVELS__medium__MODEL_CONFIG__OVERRIDES__BASE_URL",
    "DIALECTIC_LEVELS__high__MODEL_CONFIG__OVERRIDES__BASE_URL",
    "DIALECTIC_LEVELS__max__MODEL_CONFIG__OVERRIDES__BASE_URL",
}


class HonchoDemoComposeTest(unittest.TestCase):
    def test_demo_services_route_every_model_role_through_radeon_tunnel(self) -> None:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                HERE / "compose.honcho.yml",
                "-f",
                HERE / "compose.honcho-demo.yml",
                "config",
                "--no-env-resolution",
                "--format",
                "json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        services = json.loads(result.stdout)["services"]

        for service_name in ("honcho-api", "honcho-deriver"):
            environment = services[service_name]["environment"]
            self.assertEqual(
                {key: environment.get(key) for key in BASE_URL_KEYS},
                {key: REMOTE_GATEWAY for key in BASE_URL_KEYS},
            )


if __name__ == "__main__":
    unittest.main()
