from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/doctor.sh"


class DoctorTest(unittest.TestCase):
    def test_passes_prerequisites_without_printing_environment_values(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bin_dir = Path(raw)
            for name, body in {
                "docker": "#!/bin/sh\nexit 0\n",
                "curl": "#!/bin/sh\nexit 1\n",
            }.items():
                path = bin_dir / name
                path.write_text(body, encoding="utf-8")
                path.chmod(0o755)
            marker = "must-not-appear-in-doctor-output"
            env = os.environ | {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "DEJAVIEW_TEST_SECRET": marker,
            }
            result = subprocess.run([SCRIPT], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(marker, result.stdout + result.stderr)

    def test_missing_required_tool_is_blocking_and_named(self) -> None:
        # GitHub-hosted Linux runners expose /usr/bin/docker (and its Compose
        # plugin) even when the deliberately reduced PATH hides uv and Node.
        # Put a deterministic failing docker shim first so this test verifies
        # the doctor's error contract instead of depending on runner images.
        with tempfile.TemporaryDirectory() as raw:
            bin_dir = Path(raw)
            docker = bin_dir / "docker"
            docker.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
            docker.chmod(0o755)
            env = os.environ | {"PATH": f"{bin_dir}:/usr/bin:/bin"}
            result = subprocess.run([SCRIPT], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uv missing", result.stderr)
        self.assertIn("node missing", result.stderr)
        self.assertIn("Docker Compose plugin unavailable", result.stderr)


if __name__ == "__main__":
    unittest.main()
