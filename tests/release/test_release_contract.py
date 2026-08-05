from __future__ import annotations

import re
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ReleaseContractTest(unittest.TestCase):
    def test_required_release_files_exist(self) -> None:
        for relative in (
            "LICENSE",
            "NOTICE",
            ".github/workflows/first-party.yml",
            "scripts/doctor.sh",
            "scripts/test-first-party.sh",
            "scripts/submission_check.py",
            "scripts/build_submission_video.py",
            "deploy/mac/product-stack.sh",
            "deploy/windows/dejaview.ps1",
            "deploy/windows/dejaview.cmd",
            "deploy/windows/README.md",
            "docs/submission/PROJECT_SPECIFICATION.md",
            "docs/submission/DejaView-Project-Specification.docx",
            "docs/submission/DejaView-Track2-Presentation.pptx",
            "docs/assets/demo/dejaview-p34-six-act-20260802-en-3m.mp4",
            "docs/assets/demo/dejaview-p34-six-act-20260802-en-3m.srt",
        ):
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).is_file(), relative)

    def test_make_exposes_operator_entry_points(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        for target in ("setup", "doctor", "product-up", "product-down", "product-status", "test", "submission-check"):
            with self.subTest(target=target):
                self.assertRegex(makefile, rf"(?m)^{re.escape(target)}:")

    def test_ci_uses_the_same_first_party_test_entry_point(self) -> None:
        workflow = ROOT / ".github/workflows/first-party.yml"
        self.assertTrue(workflow.is_file())
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("make test", text)
        self.assertIn("make submission-check", text)
        self.assertEqual(
            text.count("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1"),
            2,
        )
        self.assertEqual(
            text.count("astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2"),
            2,
        )
        self.assertNotRegex(text, r"uses:\s+(?:actions/checkout|astral-sh/setup-uv)@v\d+")

    def test_first_party_suite_declares_release_dependencies_and_all_launchers(self) -> None:
        suite = (ROOT / "scripts/test-first-party.sh").read_text(encoding="utf-8")
        self.assertIn("deploy/mac/tests", suite)
        self.assertIn("--with pyyaml", suite)
        self.assertRegex(suite, r"uv run --project services/ocrd --locked")

    def test_release_docs_do_not_publish_ephemeral_cloud_coordinates(self) -> None:
        public_docs = [
            ROOT / "README.md",
            ROOT / "README.zh.md",
            ROOT / "STATUS.md",
            ROOT / "TASKBOARD.json",
            ROOT / "deploy/server/DEPLOY.md",
            ROOT / "deploy/windows/README.md",
            ROOT / "deploy/windows/dejaview.ps1",
            ROOT / "docs/AGENT_KICKOFF_PROMPT.md",
            ROOT / "docs/EXECUTION_HANDBOOK.md",
            ROOT / "docs/benchmarks.md",
            ROOT / "docs/verification-log.md",
            ROOT / "docs/submission/PROJECT_SPECIFICATION.md",
            ROOT / "services/memoryd/scripts/seed_fixtures.py",
        ]
        forbidden = re.compile(
            r"36\.150\.116\."
            r"|\bu-\d{4,}-[0-9a-f]+\b"
            r"|\broot@\d{1,3}(?:\.\d{1,3}){3}\b"
        )
        for path in public_docs:
            with self.subTest(path=path.name):
                self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")))

    def test_gateway_launchers_are_loopback_only(self) -> None:
        for relative in (
            "deploy/mac/llama-launch/gateway.sh",
            "deploy/server/llama-launch/gateway.sh",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(relative=relative):
                self.assertNotIn("--host 0.0.0.0", text)
                self.assertIn("--host 127.0.0.1", text)
                self.assertNotIn('--host "$HOST"', text)
                self.assertNotIn("--detailed_debug", text)

    def test_gateway_host_environment_cannot_override_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            uvx = root / "uvx"
            uvx.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
            uvx.chmod(0o755)
            mac = subprocess.run(
                [ROOT / "deploy/mac/llama-launch/gateway.sh"],
                env=os.environ
                | {
                    "PATH": f"{root}:{os.environ['PATH']}",
                    "DEJAVIEW_GATEWAY_HOST": "0.0.0.0",
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(mac.returncode, 0, mac.stdout + mac.stderr)
            mac_args = mac.stdout.splitlines()
            self.assertEqual(mac_args[mac_args.index("--host") + 1], "127.0.0.1")

            venv = root / "venv" / "bin"
            venv.mkdir(parents=True)
            litellm = venv / "litellm"
            litellm.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8"
            )
            litellm.chmod(0o755)
            server = subprocess.run(
                [ROOT / "deploy/server/llama-launch/gateway.sh"],
                env=os.environ
                | {
                    "LLITELLM_VENV": str(root / "venv"),
                    "DEJAVIEW_RUNTIME_DIR": str(root / "runtime"),
                    "DEJAVIEW_GATEWAY_HOST": "0.0.0.0",
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(server.returncode, 0, server.stdout + server.stderr)
            server_args = server.stdout.splitlines()
            self.assertEqual(
                server_args[server_args.index("--host") + 1], "127.0.0.1"
            )

    def test_managed_stack_scripts_never_use_broad_pkill(self) -> None:
        for relative in (
            "deploy/mac/llama-launch/dev-stack.sh",
            "deploy/server/llama-launch/server-stack.sh",
            "deploy/mac/product-stack.sh",
        ):
            path = ROOT / relative
            if not path.exists():
                self.fail(f"missing {relative}")
            with self.subTest(relative=relative):
                self.assertNotIn("pkill", path.read_text(encoding="utf-8"))

    def test_shell_release_entry_points_parse(self) -> None:
        scripts = [
            ROOT / "deploy/mac/setup-honcho.sh",
            ROOT / "deploy/mac/product-stack.sh",
            ROOT / "scripts/doctor.sh",
            ROOT / "scripts/test-first-party.sh",
        ]
        for path in scripts:
            with self.subTest(path=path.name):
                result = subprocess.run(
                    ["/bin/bash", "-n", path], capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
