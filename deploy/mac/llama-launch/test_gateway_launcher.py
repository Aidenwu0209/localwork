from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class GatewayLauncherTest(unittest.TestCase):
    def test_launches_the_verified_litellm_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            uvx = Path(tmp) / "uvx"
            uvx.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
            uvx.chmod(0o755)
            env = os.environ | {"PATH": f"{tmp}:{os.environ['PATH']}"}

            result = subprocess.run(
                [HERE / "gateway.sh"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

        self.assertEqual(
            result.stdout.splitlines()[:3],
            ["--from", "litellm[proxy]==1.93.0", "litellm"],
        )


if __name__ == "__main__":
    unittest.main()
