from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "windows" / "dejaview.ps1"
CMD = ROOT / "deploy" / "windows" / "dejaview.cmd"


class WindowsContractTest(unittest.TestCase):
    def test_windows_entrypoint_is_release_tracked_and_uses_logical_ssh_alias(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("radeon-cloud", text)
        self.assertTrue(
            all(address == "127.0.0.1" for address in re.findall(r"(?:\d{1,3}\.){3}\d{1,3}", text))
        )
        self.assertNotIn("root@", text)
        self.assertIn("BatchMode=yes", text)
        self.assertIn("ExitOnForwardFailure=yes", text)

    def test_windows_lifecycle_never_uses_broad_process_termination(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "Stop-Process" in line:
                self.assertIn("-Id", line)
        self.assertNotIn("taskkill /IM", text)
        self.assertNotIn("Stop-Process -Name", text)
        self.assertIn("refusing to overwrite a live unowned process record", text)
        self.assertIn("if ($tunnelStarted)", text)

    def test_cmd_wrapper_uses_process_scoped_execution_policy(self) -> None:
        text = CMD.read_text(encoding="utf-8")
        self.assertIn("-ExecutionPolicy Bypass", text)
        self.assertIn('"%~dp0dejaview.ps1" %*', text)
        self.assertNotIn("Set-ExecutionPolicy", text)

    def test_capture_pyobjc_dependencies_are_darwin_only(self) -> None:
        text = (ROOT / "clients" / "capture" / "pyproject.toml").read_text(encoding="utf-8")
        for package in ("pyobjc-core", "pyobjc-framework-cocoa", "pyobjc-framework-quartz"):
            line = next(line for line in text.splitlines() if package in line)
            self.assertIn("sys_platform == 'darwin'", line)

    @unittest.skipUnless(os.name == "nt", "requires Windows user32")
    def test_windows_capture_backend_imports_and_enumerates_without_pyobjc(self) -> None:
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(ROOT / 'clients' / 'capture' / 'src')!r}); "
            "from capture.windows import list_windows; "
            "assert isinstance(list_windows(), list)"
        )
        subprocess.run([sys.executable, "-c", code], check=True)

    def test_capture_python_sources_parse(self) -> None:
        for path in (ROOT / "clients" / "capture" / "src" / "capture").glob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


if __name__ == "__main__":
    unittest.main()
