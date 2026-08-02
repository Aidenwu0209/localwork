from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "llama-launch"


def write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    state = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="],
        text=True,
        capture_output=True,
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def reap(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=1)


class LauncherFixture:
    def __init__(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.launch = self.root / "llama-launch"
        self.runtime = self.root / "runtime"
        self.bin = self.root / "bin"
        self.launch.mkdir()
        self.bin.mkdir()
        shutil.copy2(SOURCE / "server-stack.sh", self.launch / "server-stack.sh")
        shutil.copy2(SOURCE / "gateway.sh", self.launch / "gateway.sh")
        write_executable(
            self.launch / "sentinel.sh",
            "#!/bin/bash\n"
            "trap 'sleep \"${TERM_DELAY:-0}\"; exit 0' TERM INT\n"
            "while :; do sleep 0.05; done\n",
        )
        write_executable(
            self.launch / "gateway.sh",
            "#!/bin/bash\n"
            "trap 'exit 0' TERM INT\n"
            "while :; do sleep 0.05; done\n",
        )
        write_executable(
            self.bin / "rocm-smi",
            "#!/bin/sh\nexit 0\n",
        )
        self.env = os.environ | {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "DEJAVIEW_RUNTIME_DIR": str(self.runtime),
            "DEJAVIEW_ROLE_READY_TIMEOUT": "0.15",
            "DEJAVIEW_GATEWAY_READY_TIMEOUT": "0.15",
            "DEJAVIEW_POLL_SECONDS": "0.02",
            "DEJAVIEW_STOP_TIMEOUT": "2",
        }

    def set_curl(self, body: str) -> None:
        write_executable(self.bin / "curl", "#!/bin/bash\n" + body)

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.launch / "server-stack.sh", *args],
            env=self.env,
            text=True,
            capture_output=True,
            timeout=5,
        )

    def close(self) -> None:
        subprocess.run(
            [self.launch / "server-stack.sh", "down"],
            env=self.env,
            text=True,
            capture_output=True,
            timeout=5,
        )
        self.tempdir.cleanup()


class ServerStackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = LauncherFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_http_error_is_not_ready_and_failed_role_is_cleaned_up(self) -> None:
        self.fixture.set_curl(
            'saw_fail=0\n'
            'for arg in "$@"; do\n'
            '  case "$arg" in -*f*) saw_fail=1 ;; esac\n'
            'done\n'
            '# Simulate an HTTP 500: curl only reports failure when -f is used.\n'
            '[ "$saw_fail" -eq 1 ] && exit 22\n'
            'exit 0\n'
        )

        result = self.fixture.run("up", "sentinel")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("sentinel NOT ready", result.stdout)
        self.assertFalse((self.fixture.runtime / "dejaview-sentinel.pid").exists())

    def test_each_health_probe_has_a_request_timeout(self) -> None:
        marker = self.fixture.root / "curl-max-time.seen"
        self.fixture.env["CURL_ARGS_MARKER"] = str(marker)
        self.fixture.set_curl(
            'for arg in "$@"; do\n'
            '  [ "$arg" = "--max-time" ] && : > "$CURL_ARGS_MARKER"\n'
            'done\n'
            'exit 22\n'
        )

        result = self.fixture.run("up", "sentinel")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(marker.exists(), "health probe omitted curl --max-time")

    def test_gateway_readiness_failure_is_nonzero_and_cleans_this_start(self) -> None:
        self.fixture.set_curl(
            'url="${!#}"\n'
            'case "$url" in\n'
            '  *:8003/*) exit 0 ;;\n'
            '  *:4000/*) exit 22 ;;\n'
            '  *) exit 22 ;;\n'
            'esac\n'
        )

        result = self.fixture.run("up", "sentinel")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("gateway NOT ready", result.stdout)
        self.assertFalse((self.fixture.runtime / "dejaview-sentinel.pid").exists())
        self.assertFalse((self.fixture.runtime / "dejaview-gateway.pid").exists())

    def test_later_launcher_failure_cleans_roles_started_by_this_command(self) -> None:
        self.fixture.set_curl("exit 0\n")

        result = self.fixture.run("up", "sentinel", "fast")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no executable launcher for role 'fast'", result.stderr)
        self.assertFalse((self.fixture.runtime / "dejaview-sentinel.pid").exists())

    def test_role_is_not_ready_when_managed_process_dies_behind_live_port(self) -> None:
        self.fixture.set_curl(
            'pidfile="$DEJAVIEW_RUNTIME_DIR/dejaview-sentinel.pid"\n'
            'pid="$(cut -d "|" -f 1 "$pidfile")"\n'
            'kill -9 "$pid"\n'
            'sleep 0.05\n'
            'exit 0\n'
        )

        result = self.fixture.run("up", "sentinel")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("sentinel NOT ready", result.stdout)
        self.assertFalse((self.fixture.runtime / "dejaview-sentinel.pid").exists())

    def test_gateway_is_not_ready_when_managed_process_dies_behind_live_port(self) -> None:
        self.fixture.set_curl(
            'url="${!#}"\n'
            'case "$url" in\n'
            '  *:8003/*) exit 0 ;;\n'
            '  *:4000/*)\n'
            '    pidfile="$DEJAVIEW_RUNTIME_DIR/dejaview-gateway.pid"\n'
            '    pid="$(cut -d "|" -f 1 "$pidfile")"\n'
            '    kill -9 "$pid"\n'
            '    sleep 0.05\n'
            '    exit 0\n'
            '    ;;\n'
            '  *) exit 22 ;;\n'
            'esac\n'
        )

        result = self.fixture.run("up", "sentinel")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("gateway NOT ready", result.stdout)
        self.assertFalse((self.fixture.runtime / "dejaview-sentinel.pid").exists())
        self.assertFalse((self.fixture.runtime / "dejaview-gateway.pid").exists())

    def test_down_rejects_pidfile_for_unrelated_process(self) -> None:
        sleeper = subprocess.Popen(["sleep", "30"])
        self.addCleanup(reap, sleeper)
        self.fixture.runtime.mkdir(parents=True)
        (self.fixture.runtime / "dejaview-sentinel.pid").write_text(
            f"{sleeper.pid} forged sentinel\n", encoding="utf-8"
        )

        result = self.fixture.run("down", "sentinel")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(process_alive(sleeper.pid), "down killed an unrelated process")
        self.assertFalse((self.fixture.runtime / "dejaview-sentinel.pid").exists())

    def test_down_waits_for_owned_process_to_exit(self) -> None:
        write_executable(
            self.fixture.launch / "sentinel.sh",
            "#!/usr/bin/python3\n"
            "import os, signal, sys, time\n"
            "from pathlib import Path\n"
            "def stop(_signum, _frame):\n"
            "    time.sleep(float(os.environ.get('TERM_DELAY', '0')))\n"
            "    sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, stop)\n"
            "Path(os.environ['TERM_READY_FILE']).touch()\n"
            "while True:\n"
            "    time.sleep(0.05)\n",
        )
        self.fixture.set_curl("exit 0\n")
        self.fixture.env["TERM_DELAY"] = "0.3"
        ready_file = self.fixture.root / "term-handler.ready"
        self.fixture.env["TERM_READY_FILE"] = str(ready_file)
        started = self.fixture.run("up", "sentinel")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        deadline = time.monotonic() + 2
        while not ready_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(ready_file.exists(), "TERM handler did not become ready")
        pidfile = self.fixture.runtime / "dejaview-sentinel.pid"
        pid = int(pidfile.read_text(encoding="utf-8").split("|", maxsplit=1)[0])

        before_down = time.monotonic()
        result = self.fixture.run("down", "sentinel")
        elapsed = time.monotonic() - before_down

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreaterEqual(elapsed, 0.25)
        self.assertFalse(process_alive(pid))
        self.assertFalse(pidfile.exists())

    def test_isolated_down_does_not_scan_global_tmp_pidfiles(self) -> None:
        sleeper = subprocess.Popen(["sleep", "30"])
        self.addCleanup(reap, sleeper)
        global_pidfile = Path("/tmp") / f"dejaview-p317-{os.getpid()}.pid"
        global_pidfile.write_text(f"{sleeper.pid}\n", encoding="utf-8")
        self.addCleanup(lambda: global_pidfile.unlink(missing_ok=True))

        result = self.fixture.run("down")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(process_alive(sleeper.pid), "down scanned outside its runtime dir")


class GatewayLauncherSafetyTest(unittest.TestCase):
    def test_gateway_defaults_to_loopback_and_writes_config_in_runtime_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            runtime = root / "runtime"
            bin_dir.mkdir()
            write_executable(
                bin_dir / "litellm",
                "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
            )
            result = subprocess.run(
                [SOURCE / "gateway.sh"],
                env=os.environ
                | {
                    "LLITELLM_VENV": str(root),
                    "DEJAVIEW_RUNTIME_DIR": str(runtime),
                },
                text=True,
                capture_output=True,
                timeout=5,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = result.stdout.splitlines()
        self.assertEqual(args[args.index("--host") + 1], "127.0.0.1")
        config = Path(args[args.index("--config") + 1])
        self.assertEqual(config.parent, runtime)

    def test_gateway_host_cannot_be_overridden_to_all_interfaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            write_executable(
                bin_dir / "litellm",
                "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
            )
            result = subprocess.run(
                [SOURCE / "gateway.sh"],
                env=os.environ
                | {
                    "LLITELLM_VENV": str(root),
                    "DEJAVIEW_RUNTIME_DIR": str(root / "runtime"),
                    "DEJAVIEW_GATEWAY_HOST": "0.0.0.0",
                },
                text=True,
                capture_output=True,
                timeout=5,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = result.stdout.splitlines()
        self.assertEqual(args[args.index("--host") + 1], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
