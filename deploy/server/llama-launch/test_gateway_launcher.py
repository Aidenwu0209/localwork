from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


class GatewayLauncherTest(unittest.TestCase):
    def test_perceive_route_disables_thinking_for_structured_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            litellm = bin_dir / "litellm"
            litellm.write_text(
                "#!/bin/sh\n"
                'while [ "$#" -gt 0 ]; do\n'
                '  if [ "$1" = --config ]; then cat "$2"; exit 0; fi\n'
                "  shift\n"
                "done\n"
                "exit 2\n",
                encoding="utf-8",
            )
            litellm.chmod(0o755)
            env = os.environ | {"LLITELLM_VENV": tmp}

            result = subprocess.run(
                [HERE / "gateway.sh"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

        config = yaml.safe_load(result.stdout)
        perceive = next(
            item for item in config["model_list"] if item["model_name"] == "perceive"
        )
        self.assertIs(
            perceive["litellm_params"]["extra_body"]["chat_template_kwargs"][
                "enable_thinking"
            ],
            False,
        )


if __name__ == "__main__":
    unittest.main()
