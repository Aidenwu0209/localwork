from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/mac/setup-honcho.sh"
HONCHO = ROOT / "third_party/honcho"
PIN = "340175ad5f8b49b73007481eef1885ffe99ac768"


class HonchoSetupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run([SCRIPT], cwd=ROOT, check=True, text=True)

    def test_exact_pin_and_patch_stack_are_idempotent(self) -> None:
        first = subprocess.run(
            [SCRIPT, "--check"], cwd=ROOT, check=False, capture_output=True, text=True
        )
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(
            subprocess.check_output(["git", "-C", HONCHO, "rev-parse", "HEAD"], text=True).strip(),
            PIN,
        )
        rerun = subprocess.run(
            [SCRIPT], cwd=ROOT, check=False, capture_output=True, text=True
        )
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        self.assertIn("exact patch stack verified", rerun.stdout)

    def test_unexpected_submodule_file_fails_closed(self) -> None:
        marker = HONCHO / ".dejaview-unexpected-test-file"
        marker.write_text("synthetic test pollution\n", encoding="utf-8")
        try:
            result = subprocess.run(
                [SCRIPT, "--check"], cwd=ROOT, check=False, capture_output=True, text=True
            )
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("unexpected Honcho changes", result.stderr)
        finally:
            marker.unlink(missing_ok=True)

    def test_check_mode_never_initializes_a_missing_submodule(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            script = root / "deploy" / "mac" / "setup-honcho.sh"
            script.parent.mkdir(parents=True)
            shutil.copy2(SCRIPT, script)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            calls = root / "git.calls"
            git = bin_dir / "git"
            git.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" >> "$GIT_CALLS"\n'
                "exit 0\n",
                encoding="utf-8",
            )
            git.chmod(0o755)
            result = subprocess.run(
                [script, "--check"],
                env=os.environ
                | {
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "GIT_CALLS": str(calls),
                },
                capture_output=True,
                text=True,
            )

            called = calls.read_text(encoding="utf-8") if calls.exists() else ""
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("submodule update", called)


if __name__ == "__main__":
    unittest.main()
