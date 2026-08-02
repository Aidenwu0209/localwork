from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
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
        # A clean checkout intentionally has no deploy/mac/honcho.env.  Compose
        # still validates service-level env_file paths during `config`, so give
        # this contract test an isolated copy of the public example instead of
        # depending on a developer's private runtime file.
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for name in ("compose.honcho.yml", "compose.honcho-demo.yml"):
                shutil.copy2(HERE / name, temp_path / name)
            shutil.copy2(HERE / "honcho.env.example", temp_path / "honcho.env")

            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    temp_path / "compose.honcho.yml",
                    "-f",
                    temp_path / "compose.honcho-demo.yml",
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
