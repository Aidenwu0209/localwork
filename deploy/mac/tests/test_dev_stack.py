from __future__ import annotations

import os
import shutil
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


class DevStackFixture:
    def __init__(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.launch = self.root / "llama-launch"
        self.runtime = self.root / "runtime"
        self.bin = self.root / "bin"
        self.launch.mkdir()
        self.bin.mkdir()
        shutil.copy2(SOURCE / "dev-stack.sh", self.launch / "dev-stack.sh")
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
            [self.launch / "dev-stack.sh", *args],
            env=self.env,
            text=True,
            capture_output=True,
            timeout=5,
        )

    def close(self) -> None:
        subprocess.run(
            [self.launch / "dev-stack.sh", "down"],
            env=self.env,
            text=True,
            capture_output=True,
            timeout=5,
        )
        self.tempdir.cleanup()


class DevStackSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = DevStackFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_http_error_is_not_ready_and_started_role_is_cleaned(self) -> None:
        self.fixture.set_curl(
            'saw_fail=0\n'
            'for arg in "$@"; do\n'
            '  case "$arg" in -*f*) saw_fail=1 ;; esac\n'
            'done\n'
            '[ "$saw_fail" -eq 1 ] && exit 22\n'
            'exit 0\n'
        )

        result = self.fixture.run("up", "sentinel")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("sentinel NOT ready", result.stdout)
        self.assertFalse((self.fixture.runtime / "dejaview-sentinel.pid").exists())

    def test_gateway_failure_is_nonzero_and_cleans_this_start(self) -> None:
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

    def test_down_rejects_pidfile_for_unrelated_process(self) -> None:
        sleeper = subprocess.Popen(["sleep", "30"])
        self.addCleanup(reap, sleeper)
        self.fixture.runtime.mkdir(parents=True)
        (self.fixture.runtime / "dejaview-sentinel.pid").write_text(
            f"{sleeper.pid}|forged|sentinel\n", encoding="utf-8"
        )

        result = self.fixture.run("down")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(process_alive(sleeper.pid), "down killed an unrelated process")

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
        result = self.fixture.run("down")
        elapsed = time.monotonic() - before_down

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreaterEqual(elapsed, 0.25)
        self.assertFalse(process_alive(pid))

    def test_isolated_down_does_not_scan_global_tmp_pidfiles(self) -> None:
        sleeper = subprocess.Popen(["sleep", "30"])
        self.addCleanup(reap, sleeper)
        global_pidfile = Path("/tmp") / f"dejaview-p317-dev-{os.getpid()}.pid"
        global_pidfile.write_text(f"{sleeper.pid}\n", encoding="utf-8")
        self.addCleanup(lambda: global_pidfile.unlink(missing_ok=True))

        result = self.fixture.run("down")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(process_alive(sleeper.pid), "down scanned outside its runtime dir")


if __name__ == "__main__":
    unittest.main()
