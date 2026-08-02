from __future__ import annotations

import os
import signal
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/mac/product-stack.sh"


class ProductStackTest(unittest.TestCase):
    def _environment(self, tmp: Path) -> dict[str, str]:
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        uv = bin_dir / "uv"
        uv.write_text("#!/bin/sh\nwhile :; do sleep 1; done\n", encoding="utf-8")
        uv.chmod(0o755)
        curl = bin_dir / "curl"
        curl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        curl.chmod(0o755)
        return os.environ | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DEJAVIEW_RUNTIME_DIR": str(tmp / "run"),
            "DEJAVIEW_SKIP_INFRA": "1",
            "DEJAVIEW_SKIP_GATEWAY_CHECK": "1",
            "DEJAVIEW_SERVICE_TIMEOUT": "2",
            "DEJAVIEW_POLL_SECONDS": "0.05",
        }

    def test_up_is_idempotent_and_down_stops_only_managed_services(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            env = self._environment(tmp)
            first = subprocess.run([SCRIPT, "up"], env=env, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            second = subprocess.run([SCRIPT, "up"], env=env, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn("already running", second.stdout)
            result = subprocess.run([SCRIPT, "down"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(list((tmp / "run").glob("*.pid")))

    def test_stale_pidfile_never_kills_an_unowned_process(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            env = self._environment(tmp)
            runtime = tmp / "run"
            runtime.mkdir()
            sleeper = subprocess.Popen(["sleep", "30"])
            (runtime / "ocrd.pid").write_text(f"{sleeper.pid}\n", encoding="utf-8")
            try:
                result = subprocess.run([SCRIPT, "down"], env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIsNone(sleeper.poll(), "unowned process was killed")
                self.assertIn("refusing", result.stderr)
            finally:
                sleeper.send_signal(signal.SIGTERM)
                sleeper.wait(timeout=5)

    def test_runtime_directory_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            env = self._environment(tmp)
            result = subprocess.run(
                [SCRIPT, "status"], env=env, capture_output=True, text=True
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                stat.S_IMODE((tmp / "run").stat().st_mode),
                0o700,
            )

    def test_live_port_never_hides_a_dead_managed_process(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            env = self._environment(tmp)
            uv = tmp / "bin" / "uv"
            uv.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
            uv.chmod(0o755)

            result = subprocess.run(
                [SCRIPT, "up"], env=env, capture_output=True, text=True
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("DejaView product ready", result.stdout)
            self.assertFalse(list((tmp / "run").glob("*.pid")))

    def test_matching_manual_command_is_not_owned_without_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            env = self._environment(tmp)
            runtime = tmp / "run"
            runtime.mkdir()
            manual = subprocess.Popen(
                [
                    tmp / "bin" / "uv",
                    "run",
                    "--project",
                    ROOT / "services" / "ocrd",
                    "python",
                    "-m",
                    "ocrd",
                ],
                env=env,
            )
            (runtime / "ocrd.pid").write_text(
                f"{manual.pid}\n", encoding="utf-8"
            )
            try:
                result = subprocess.run(
                    [SCRIPT, "down"], env=env, capture_output=True, text=True
                )
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIsNone(manual.poll(), "manual same-command process was killed")
            finally:
                if manual.poll() is None:
                    manual.send_signal(signal.SIGTERM)
                manual.wait(timeout=5)

    def _infra_environment(
        self, tmp: Path, *, preexisting_data: bool = False
    ) -> tuple[dict[str, str], Path]:
        env = self._environment(tmp)
        log = tmp / "docker.log"
        docker = tmp / "bin" / "docker"
        docker.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
            'case "$*" in\n'
            '  *compose.data.yml*" ps -q")\n'
            '    [ "${PREEXIST_DATA:-0}" = 1 ] && printf "existing-data\\n"\n'
            '    ;;\n'
            '  *compose.honcho.yml*" ps -q") ;;\n'
            'esac\n'
            "exit 0\n",
            encoding="utf-8",
        )
        docker.chmod(0o755)
        uv = tmp / "bin" / "uv"
        uv.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
        uv.chmod(0o755)
        curl = tmp / "bin" / "curl"
        curl.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        curl.chmod(0o755)
        env |= {
            "DEJAVIEW_SKIP_INFRA": "0",
            "DEJAVIEW_SERVICE_TIMEOUT": "0",
            "DOCKER_LOG": str(log),
            "PREEXIST_DATA": "1" if preexisting_data else "0",
        }
        return env, log

    def test_failed_product_up_rolls_back_new_compose_stacks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            env, log = self._infra_environment(tmp)

            result = subprocess.run(
                [SCRIPT, "up"], env=env, capture_output=True, text=True
            )
            commands = log.read_text(encoding="utf-8")

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("compose.honcho.yml down", commands)
            self.assertIn("compose.data.yml down", commands)

    def test_failed_product_up_keeps_preexisting_compose_stack(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            env, log = self._infra_environment(tmp, preexisting_data=True)

            result = subprocess.run(
                [SCRIPT, "up"], env=env, capture_output=True, text=True
            )
            commands = log.read_text(encoding="utf-8")

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("compose.honcho.yml down", commands)
            self.assertNotIn("compose.data.yml down", commands)


if __name__ == "__main__":
    unittest.main()
