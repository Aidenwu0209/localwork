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
        curl.write_text(
            "#!/bin/sh\n"
            "for url; do :; done\n"
            "case \"${DEJAVIEW_TEST_HEALTH_MODE:-current}:$url\" in\n"
            "  legacy:*) printf '%s\\n' '{\"legacy\":true}'; exit 0 ;;\n"
            "  *:*/v1/models)\n"
            '    [ "${DEJAVIEW_TEST_REQUIRE_PRIVACY_START:-0}" = 1 ] && [ ! -f "$DEJAVIEW_TEST_PRIVACY_READY" ] && exit 1\n'
            "    printf '%s\\n' '{\"object\":\"list\",\"data\":[{\"id\":\"sentinel\",\"owned_by\":\"dejaview-local\"}]}' ;;\n"
            "  *:8006/health) printf '%s\\n' '{\"status\":\"ok\",\"backend\":\"paddleocr\"}' ;;\n"
            "  *:8090/health) printf '%s\\n' '{\"status\":\"ok\",\"service\":\"memoryd\"}' ;;\n"
            "  *:8101/health) printf '%s\\n' '{\"status\":\"ok\",\"service\":\"agentd\"}' ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        curl.chmod(0o755)
        lsof = bin_dir / "lsof"
        lsof.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        lsof.chmod(0o755)
        return os.environ | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DEJAVIEW_RUNTIME_DIR": str(tmp / "run"),
            "DEJAVIEW_SKIP_INFRA": "1",
            "DEJAVIEW_SKIP_GATEWAY_CHECK": "1",
            "DEJAVIEW_SKIP_PRIVACY_STACK": "1",
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

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("NOT_READY", result.stdout)
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

    def _privacy_environment(self, tmp: Path) -> tuple[dict[str, str], Path, Path]:
        env = self._environment(tmp)
        privacy_log = tmp / "privacy.log"
        uv_log = tmp / "uv.log"
        privacy_ready = tmp / "privacy-ready"
        dev_stack = tmp / "dev-stack.sh"
        dev_stack.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$*" >> "$PRIVACY_LOG"\n'
            '[ "$1" = up ] && touch "$DEJAVIEW_TEST_PRIVACY_READY"\n'
            '[ "$1" = down ] && rm -f "$DEJAVIEW_TEST_PRIVACY_READY"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        dev_stack.chmod(0o755)
        uv = tmp / "bin" / "uv"
        uv.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$*" >> "$UV_LOG"\n'
            "while :; do sleep 1; done\n",
            encoding="utf-8",
        )
        uv.chmod(0o755)
        env |= {
            "DEJAVIEW_SKIP_PRIVACY_STACK": "0",
            "DEJAVIEW_DEV_STACK_SCRIPT": str(dev_stack),
            "PRIVACY_LOG": str(privacy_log),
            "UV_LOG": str(uv_log),
            "DEJAVIEW_TEST_REQUIRE_PRIVACY_START": "1",
            "DEJAVIEW_TEST_PRIVACY_READY": str(privacy_ready),
        }
        return env, privacy_log, uv_log

    def test_product_up_owns_privacy_stack_before_services_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            env, privacy_log, uv_log = self._privacy_environment(tmp)

            first = subprocess.run([SCRIPT, "up"], env=env, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(privacy_log.read_text(encoding="utf-8").splitlines(), ["up sentinel"])
            self.assertTrue(uv_log.read_text(encoding="utf-8").strip())
            self.assertTrue((tmp / "run" / "privacy.product-owned").is_file())

            second = subprocess.run([SCRIPT, "up"], env=env, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(privacy_log.read_text(encoding="utf-8").splitlines(), ["up sentinel"])

            down = subprocess.run([SCRIPT, "down"], env=env, capture_output=True, text=True)
            self.assertEqual(down.returncode, 0, down.stdout + down.stderr)
            self.assertEqual(privacy_log.read_text(encoding="utf-8").splitlines(), ["up sentinel", "down"])

    def test_product_up_rolls_back_owned_privacy_stack_after_service_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            env, privacy_log, _ = self._privacy_environment(tmp)
            uv = tmp / "bin" / "uv"
            uv.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
            uv.chmod(0o755)

            result = subprocess.run([SCRIPT, "up"], env=env, capture_output=True, text=True)

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(privacy_log.read_text(encoding="utf-8").splitlines(), ["up sentinel", "down"])
            self.assertFalse((tmp / "run" / "privacy.product-owned").exists())

    def test_product_down_does_not_stop_a_preexisting_privacy_stack(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            env, privacy_log, _ = self._privacy_environment(tmp)

            result = subprocess.run([SCRIPT, "down"], env=env, capture_output=True, text=True)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(privacy_log.exists(), "unowned privacy stack was stopped")

    def test_status_rejects_legacy_http_200_payload(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            env = self._environment(tmp)
            up = subprocess.run([SCRIPT, "up"], env=env, capture_output=True, text=True)
            self.assertEqual(up.returncode, 0, up.stdout + up.stderr)
            try:
                legacy = subprocess.run(
                    [SCRIPT, "status"],
                    env=env | {"DEJAVIEW_TEST_HEALTH_MODE": "legacy"},
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(legacy.returncode, 0, legacy.stdout + legacy.stderr)
                self.assertIn("NOT_READY", legacy.stdout)
            finally:
                subprocess.run([SCRIPT, "down"], env=env, capture_output=True, text=True)

    def test_status_refuses_a_pid_record_from_another_source_revision(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            env = self._environment(tmp)
            up = subprocess.run([SCRIPT, "up"], env=env, capture_output=True, text=True)
            self.assertEqual(up.returncode, 0, up.stdout + up.stderr)
            pidfile = tmp / "run" / "ocrd.pid"
            original = pidfile.read_text(encoding="utf-8")
            try:
                parts = original.strip().split("|")
                self.assertGreaterEqual(len(parts), 5, original)
                parts[-1] = "other-revision"
                pidfile.write_text("|".join(parts) + "\n", encoding="utf-8")
                status = subprocess.run([SCRIPT, "status"], env=env, capture_output=True, text=True)
                self.assertNotEqual(status.returncode, 0, status.stdout + status.stderr)
                self.assertIn("NOT_READY", status.stdout)
            finally:
                pidfile.write_text(original, encoding="utf-8")
                subprocess.run([SCRIPT, "down"], env=env, capture_output=True, text=True)

    def test_status_reports_ready_only_for_owned_current_services_with_contract_health(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            env = self._environment(tmp)
            up = subprocess.run([SCRIPT, "up"], env=env, capture_output=True, text=True)
            self.assertEqual(up.returncode, 0, up.stdout + up.stderr)
            try:
                status = subprocess.run([SCRIPT, "status"], env=env, capture_output=True, text=True)
                self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
                self.assertIn("READY", status.stdout)
            finally:
                subprocess.run([SCRIPT, "down"], env=env, capture_output=True, text=True)

    def test_status_requires_the_privacy_gateway_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            env, _, _ = self._privacy_environment(tmp)
            up = subprocess.run([SCRIPT, "up"], env=env, capture_output=True, text=True)
            self.assertEqual(up.returncode, 0, up.stdout + up.stderr)
            try:
                (tmp / "privacy-ready").unlink()
                status = subprocess.run([SCRIPT, "status"], env=env, capture_output=True, text=True)
                self.assertNotEqual(status.returncode, 0, status.stdout + status.stderr)
                self.assertIn("NOT_READY", status.stdout)
            finally:
                subprocess.run([SCRIPT, "down"], env=env, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
