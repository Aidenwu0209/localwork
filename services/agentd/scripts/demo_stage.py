#!/usr/bin/env python3
"""Local-only BFF for the auditable six-act P3.4 recording stage."""

from __future__ import annotations

import asyncio
import json
import math
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import psycopg
from agentd.config import Settings
from agentd.tools import query_user_model
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image

DEVICE_ID = "demo-p34"
DEFAULT_DSN = "postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview_demo"
DEFAULT_DATA_ROOT = Path("/tmp/dejaview-p34-data").resolve()
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
DAILY_SCRIPT = SCRIPT_DIR / "demo_daily_report.py"
BANK_FIXTURE = REPO_ROOT / "tests/assets/sentinel/banking_01.png"
SAFE_FIXTURES = [
    (
        REPO_ROOT / "tests/assets/sentinel/normal_code_01.png",
        "VS Code",
        "Synthetic ROCm kernel review",
    ),
    (
        REPO_ROOT / "tests/assets/sentinel/normal_web_01.png",
        "Chrome",
        "Synthetic local-first architecture notes",
    ),
    (
        REPO_ROOT / "tests/assets/sentinel/normal_doc_01.png",
        "Preview",
        "Synthetic benchmark checklist",
    ),
]
REMOTE_GATEWAY = "http://127.0.0.1:14000/v1"
LOCAL_GATEWAY = "http://127.0.0.1:4000/v1"
AGENTD_URL = "http://127.0.0.1:8101"
MEMORYD_URL = "http://127.0.0.1:8090"
OCRD_URL = "http://127.0.0.1:8006"
HONCHO_SESSION = "p3-4-synthetic"
HONCHO_WORKSPACE = "dejaview-p34"
HONCHO_PEER = "demo-owner"
TIMEZONE = ZoneInfo("Asia/Kuching")
CITATION_RE = re.compile(r"\[event#(\d+)\s+([0-2]\d:[0-5]\d)\s+([^\]]+)\]")
CONNECTIVITY_CACHE_TTL_SECONDS = 5.0
# Local Metal may need a second real-model formatting attempt. Keep the stage
# fail-closed, but leave enough backend budget for the measured on-device path;
# silent waits are cut from the final <=5 minute video rather than faked.
DAILY_BACKEND_TIMEOUT_SECONDS = 180.0
REMOTE_FORWARD_SPECS = {
    "14000:127.0.0.1:4000",
    "18001:127.0.0.1:8001",
    "18002:127.0.0.1:8002",
    "18003:127.0.0.1:8003",
    "18004:127.0.0.1:8004",
    "18005:127.0.0.1:8005",
    "19393:127.0.0.1:9393",
}
REMOTE_MODEL_METRIC_PORTS = {
    "brain": 18001,
    "perceive": 18002,
    "sentinel": 18003,
    "embed": 18004,
    "fast": 18005,
}
LOCAL_ROLE_IDENTITIES = {
    "fast": (Path("/tmp/dejaview-fast.pid"), 8005),
    "perceive": (Path("/tmp/dejaview-perceive.pid"), 8002),
}
LOCAL_GATEWAY_PIDFILE = Path("/tmp/dejaview-gateway.pid")

_connectivity_cache: tuple[float, dict[str, Any]] | None = None
_connectivity_lock = asyncio.Lock()


def _dsn() -> str:
    return os.environ.get("TIMELINE_DB_URL", DEFAULT_DSN)


def _data_root() -> Path:
    return Path(os.environ.get("DATA_ROOT", DEFAULT_DATA_ROOT)).expanduser().resolve()


def _command_tokens(command_line: str) -> list[str]:
    try:
        return shlex.split(command_line)
    except ValueError:
        return []


def _flag_values(tokens: list[str], flag: str) -> list[str]:
    values: list[str] = []
    for index, token in enumerate(tokens):
        if token == flag and index + 1 < len(tokens):
            values.append(tokens[index + 1])
        elif token.startswith(flag) and token != flag:
            values.append(token[len(flag) :])
    return values


def _ssh_tunnel_command_matches(command_line: str) -> bool:
    """Require the exact formal radeon-cloud tunnel and all proof forwards."""
    tokens = _command_tokens(command_line)
    if not tokens or Path(tokens[0]).name != "ssh":
        return False
    return (
        "-N" in tokens
        and "radeon-cloud" in tokens
        and REMOTE_FORWARD_SPECS.issubset(set(_flag_values(tokens, "-L")))
    )


def _llama_role_command_matches(
    command_line: str,
    *,
    role: str,
    port: int,
) -> bool:
    tokens = _command_tokens(command_line)
    return (
        bool(tokens)
        and any(Path(token).name == "llama-server" for token in tokens)
        and role in _flag_values(tokens, "--alias")
        and str(port) in _flag_values(tokens, "--port")
    )


def _gateway_command_matches(command_line: str) -> bool:
    tokens = _command_tokens(command_line)
    return (
        bool(tokens)
        and any("litellm" in token for token in tokens)
        and "4000" in _flag_values(tokens, "--port")
        and bool(_flag_values(tokens, "--config"))
    )


def _read_live_pid_command(pidfile: Path) -> tuple[int, str]:
    try:
        raw_pid = pidfile.read_text(encoding="utf-8").strip()
        pid = int(raw_pid)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"missing or invalid dev-stack pidfile: {pidfile}") from exc
    if pid <= 1:
        raise RuntimeError(f"unsafe dev-stack PID in {pidfile}: {pid}")
    try:
        os.kill(pid, 0)
    except OSError as exc:
        raise RuntimeError(f"stale dev-stack pidfile: {pidfile} -> {pid}") from exc
    process = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        check=True,
        capture_output=True,
        text=True,
    )
    command_line = process.stdout.strip()
    if not command_line:
        raise RuntimeError(f"no live command for dev-stack PID {pid}")
    return pid, command_line


def _listener_pids(port: int) -> list[int]:
    lsof = shutil.which("lsof") or "/usr/sbin/lsof"
    process = subprocess.run(
        [
            lsof,
            "-nP",
            f"-iTCP:{port}",
            "-sTCP:LISTEN",
            "-t",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode not in (0, 1):
        raise RuntimeError(f"lsof failed while proving listener :{port}")
    pids: list[int] = []
    for line in process.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid > 1 and pid not in pids:
            pids.append(pid)
    return pids


def _process_command(pid: int) -> str:
    process = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout.strip()


def _process_parent_pid(pid: int) -> int | None:
    process = subprocess.run(
        ["ps", "-p", str(pid), "-o", "ppid="],
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        parent = int(process.stdout.strip())
    except ValueError:
        return None
    return parent if parent > 1 else None


def _pid_is_or_descends_from(pid: int, ancestor: int) -> bool:
    current: int | None = pid
    visited: set[int] = set()
    while current is not None and current not in visited:
        if current == ancestor:
            return True
        visited.add(current)
        current = _process_parent_pid(current)
    return False


def _assert_remote_tunnel() -> int:
    listener_pids = _listener_pids(14000)
    if not listener_pids:
        raise RuntimeError("formal Radeon tunnel has no listener on 127.0.0.1:14000")
    matches = [
        pid
        for pid in listener_pids
        if _ssh_tunnel_command_matches(_process_command(pid))
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "listener :14000 is not the exact radeon-cloud SSH tunnel with "
            "gateway, llama.cpp metrics, and ROCm exporter forwards"
        )
    return matches[0]


def _disconnect_remote_tunnel() -> dict[str, Any]:
    """Stop only the exact attested Radeon SSH tunnel used by this demo."""
    global _connectivity_cache

    tunnel_pid = _assert_remote_tunnel()
    os.kill(tunnel_pid, signal.SIGTERM)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        process = subprocess.run(
            ["ps", "-p", str(tunnel_pid), "-o", "stat="],
            check=False,
            capture_output=True,
            text=True,
        )
        state = process.stdout.strip()
        if process.returncode != 0 or not state or state.startswith("Z"):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("verified Radeon SSH tunnel did not stop")

    _connectivity_cache = None
    return {
        "disconnected": True,
        "method": "verified_ssh_tunnel_termination",
        "tunnel_pid": tunnel_pid,
    }


def _metric_present(payload: str, metric: str, *, value: str | None = None) -> bool:
    pattern = rf"(?m)^{re.escape(metric)}(?:\{{[^}}]*\}})?\s+"
    if value is not None:
        pattern += rf"{re.escape(value)}(?:\.0+)?(?:\s|$)"
    return re.search(pattern, payload) is not None


def _metric_numeric_values(payload: str, metric: str) -> list[float]:
    pattern = re.compile(
        rf"(?m)^{re.escape(metric)}(?:\{{[^}}]*\}})?\s+"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*$"
    )
    return [float(match) for match in pattern.findall(payload)]


def _fetch_metrics(client: httpx.Client, url: str) -> str:
    response = client.get(url)
    response.raise_for_status()
    payload = response.text
    if not payload.strip():
        raise RuntimeError(f"empty metrics payload: {url}")
    return payload


def _model_ids(payload: Any) -> set[str]:
    """Extract OpenAI-compatible model ids without accepting malformed pages."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise TypeError("model identity endpoint returned no OpenAI data list")
    ids = {
        str(item["id"])
        for item in payload["data"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if not ids:
        raise RuntimeError("model identity endpoint returned no model ids")
    return ids


def _fetch_model_ids(client: httpx.Client, url: str) -> set[str]:
    response = client.get(url)
    response.raise_for_status()
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid model identity payload: {url}") from exc
    return _model_ids(payload)


def _assert_remote_runtime(
    *,
    include_brain: bool,
) -> dict[str, Any]:
    """Fail closed unless the formal tunnel proves the full five-role pyramid.

    ``include_brain`` remains in the call contract because Act 6 requests a
    deep-tier smoke separately. Runtime identity itself is intentionally
    invariant: Act 1 and every later check require all five remote roles.
    """
    tunnel_pid = _assert_remote_tunnel()
    roles = set(REMOTE_MODEL_METRIC_PORTS)
    with httpx.Client(timeout=httpx.Timeout(4.0, connect=1.5)) as client:
        rocm_metrics = _fetch_metrics(client, "http://127.0.0.1:19393/metrics")
        required_rocm = (
            _metric_present(
                rocm_metrics,
                "dejaview_rocm_exporter_scrape_success",
                value="1",
            )
            and _metric_present(
                rocm_metrics,
                "dejaview_rocm_gpu_utilization_percent",
            )
            and _metric_present(rocm_metrics, "dejaview_rocm_vram_used_bytes")
            and any(
                value > 0
                for value in _metric_numeric_values(
                    rocm_metrics,
                    "dejaview_rocm_vram_total_bytes",
                )
            )
        )
        if not required_rocm:
            raise RuntimeError("ROCm exporter lacks a successful live GPU/VRAM scrape")

        gateway_ids = _fetch_model_ids(
            client,
            "http://127.0.0.1:14000/v1/models",
        )
        missing_gateway_roles = roles - gateway_ids
        if missing_gateway_roles:
            raise RuntimeError(
                "remote gateway /v1/models lacks logical roles: "
                + ", ".join(sorted(missing_gateway_roles))
            )

        for role in sorted(roles):
            port = REMOTE_MODEL_METRIC_PORTS[role]
            role_ids = _fetch_model_ids(
                client,
                f"http://127.0.0.1:{port}/v1/models",
            )
            if role not in role_ids:
                raise RuntimeError(
                    f"remote :{port} /v1/models does not identify role {role}"
                )
            model_metrics = _fetch_metrics(
                client,
                f"http://127.0.0.1:{port}/metrics",
            )
            if not (
                _metric_present(model_metrics, "llamacpp:requests_processing")
                and _metric_present(
                    model_metrics,
                    "llamacpp:predicted_tokens_seconds",
                )
            ):
                raise RuntimeError(
                    f"remote {role} endpoint lacks live llama.cpp metrics"
                )
    return {
        "ok": True,
        "tunnel_pid": tunnel_pid,
        "rocm": "live",
        "gateway_models": sorted(roles),
        "model_metrics": sorted(roles),
        "brain_smoke_requested": include_brain,
    }


def _assert_local_runtime() -> dict[str, Any]:
    verified: dict[str, int] = {}
    for role, (pidfile, port) in LOCAL_ROLE_IDENTITIES.items():
        pid, command_line = _read_live_pid_command(pidfile)
        if not _llama_role_command_matches(command_line, role=role, port=port):
            raise RuntimeError(
                f"{pidfile} does not identify llama-server --alias {role} --port {port}"
            )
        role_listeners = _listener_pids(port)
        if not role_listeners or not any(
            _pid_is_or_descends_from(listener_pid, pid)
            for listener_pid in role_listeners
        ):
            raise RuntimeError(
                f"verified {role} PID {pid} does not own listener :{port}"
            )
        verified[role] = pid
    gateway_pid, gateway_command = _read_live_pid_command(LOCAL_GATEWAY_PIDFILE)
    if not _gateway_command_matches(gateway_command):
        raise RuntimeError(
            f"{LOCAL_GATEWAY_PIDFILE} does not identify the dev-stack "
            "LiteLLM gateway on :4000"
        )
    gateway_listeners = _listener_pids(4000)
    if not gateway_listeners or not any(
        _pid_is_or_descends_from(pid, gateway_pid) for pid in gateway_listeners
    ):
        raise RuntimeError(
            "listener :4000 is not owned by the verified dev-stack gateway PID"
        )
    return {
        "ok": True,
        "gateway_pid": gateway_pid,
        "role_pids": verified,
    }


def _validate_ocrd_health(payload: dict[str, Any]) -> None:
    if (
        payload.get("status") != "ok"
        or payload.get("backend") != "paddleocr"
        or payload.get("engine_loaded") is not True
    ):
        raise RuntimeError(
            "formal P3.4 requires a warmed ocrd backend=paddleocr (PP-OCRv6); "
            f"received status={payload.get('status')!r}, "
            f"backend={payload.get('backend')!r}, "
            f"engine_loaded={payload.get('engine_loaded')!r}"
        )


async def _runtime_attestations(*, include_brain: bool) -> dict[str, Any]:
    remote_result, local_result = await asyncio.gather(
        asyncio.to_thread(
            _attestation_result,
            "remote",
            include_brain,
        ),
        asyncio.to_thread(
            _attestation_result,
            "local",
            include_brain,
        ),
    )
    return {"remote": remote_result, "local": local_result}


def _attestation_result(
    backend: str,
    include_brain: bool,
) -> dict[str, Any]:
    try:
        if backend == "remote":
            proof = _assert_remote_runtime(include_brain=include_brain)
        elif backend == "local":
            proof = _assert_local_runtime()
        else:
            raise ValueError(f"unknown backend attestation: {backend}")
    except (
        httpx.HTTPError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        TypeError,
    ) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
    return proof


def _validate_honcho_isolation(
    *,
    sessions_page: dict[str, Any],
    peer_card_page: dict[str, Any],
    workspace: dict[str, Any],
) -> None:
    items = sessions_page.get("items")
    if not isinstance(items, list):
        raise TypeError("Honcho sessions/list returned no items page")
    session_ids = {
        str(item.get("id"))
        for item in items
        if isinstance(item, dict) and item.get("id")
    }
    total = sessions_page.get("total", len(items))
    try:
        total_count = int(total)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Honcho sessions/list returned an invalid total") from exc
    if total_count != 1 or session_ids != {HONCHO_SESSION}:
        raise RuntimeError(
            "Honcho demo workspace is not isolated: "
            f"total={total_count}, sessions={sorted(session_ids)}"
        )

    peer_card = peer_card_page.get("peer_card")
    if peer_card not in (None, []):
        raise RuntimeError("Honcho demo peer has a nonempty global peer card")

    peer_card_config = (workspace.get("configuration") or {}).get("peer_card") or {}
    if (
        peer_card_config.get("use") is not False
        or peer_card_config.get("create") is not False
    ):
        raise RuntimeError(
            "Honcho demo workspace must disable global peer-card use and creation"
        )


def _assert_demo_environment() -> None:
    if os.environ.get("DEJAVIEW_DEMO_MODE") != "1":
        raise RuntimeError("DEJAVIEW_DEMO_MODE=1 is required")
    if _dsn() != DEFAULT_DSN:
        raise RuntimeError("TIMELINE_DB_URL must target local database dejaview_demo")
    if _data_root() != DEFAULT_DATA_ROOT:
        raise RuntimeError(f"DATA_ROOT must be {DEFAULT_DATA_ROOT}")
    if os.environ.get("HONCHO_URL", "http://127.0.0.1:8100").rstrip("/") != (
        "http://127.0.0.1:8100"
    ):
        raise RuntimeError("HONCHO_URL must be the isolated local Honcho")
    with httpx.Client(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
        response = client.get(f"{MEMORYD_URL}/health")
        response.raise_for_status()
        memoryd_health = response.json()
        agentd_response = client.get(f"{AGENTD_URL}/health")
        agentd_response.raise_for_status()
        agentd_health = agentd_response.json()
        ocrd_response = client.get(f"{OCRD_URL}/health")
        ocrd_response.raise_for_status()
        ocrd_health = ocrd_response.json()
        honcho_health = client.get("http://127.0.0.1:8100/health")
        honcho_health.raise_for_status()
        sessions_response = client.post(
            (f"http://127.0.0.1:8100/v3/workspaces/{HONCHO_WORKSPACE}/sessions/list"),
            json={},
        )
        sessions_response.raise_for_status()
        peer_card_response = client.get(
            "http://127.0.0.1:8100/v3/workspaces/"
            f"{HONCHO_WORKSPACE}/peers/{HONCHO_PEER}/card"
        )
        peer_card_response.raise_for_status()
        workspace_response = client.post(
            "http://127.0.0.1:8100/v3/workspaces",
            json={"id": HONCHO_WORKSPACE},
        )
        workspace_response.raise_for_status()
    _validate_honcho_isolation(
        sessions_page=sessions_response.json(),
        peer_card_page=peer_card_response.json(),
        workspace=workspace_response.json(),
    )
    _validate_ocrd_health(ocrd_health)
    _assert_remote_runtime(include_brain=False)
    _assert_local_runtime()
    expected_health = {
        "status": "ok",
        "pipeline": "real",
        "gateway_origin": "http://127.0.0.1:14000",
        "database": "dejaview_demo",
        "data_root": str(DEFAULT_DATA_ROOT),
    }
    mismatches = {
        key: {"expected": expected, "actual": memoryd_health.get(key)}
        for key, expected in expected_health.items()
        if memoryd_health.get(key) != expected
    }
    if mismatches:
        raise RuntimeError(f"memoryd is not isolated real demo mode: {mismatches}")
    expected_agentd = {
        "status": "ok",
        "service": "agentd",
        "model": "dejaview",
        "brain_model": "brain",
        "gateway_origin": "http://127.0.0.1:14000",
        "honcho_origin": "http://127.0.0.1:8100",
        "database": "dejaview_demo",
        "data_root": str(DEFAULT_DATA_ROOT),
    }
    agentd_mismatches = {
        key: {"expected": expected, "actual": agentd_health.get(key)}
        for key, expected in expected_agentd.items()
        if agentd_health.get(key) != expected
    }
    if agentd_mismatches:
        raise RuntimeError(
            f"agentd is not isolated Radeon demo mode: {agentd_mismatches}"
        )

    process_list = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    capture_processes = [
        line
        for line in process_list.splitlines()
        if (" -m capture" in line or "/clients/capture/" in line)
        and str(os.getpid()) not in line.split(maxsplit=1)[:1]
    ]
    if capture_processes:
        raise RuntimeError("capture is still running; stop it before demo mode")

    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        if cur.fetchone()[0] != "dejaview_demo":
            raise RuntimeError("connected database is not dejaview_demo")
        cur.execute(
            "SELECT count(*) FROM timeline_events WHERE device_id NOT LIKE 'demo-%%'"
        )
        if int(cur.fetchone()[0]) != 0:
            raise RuntimeError("demo database contains a non-demo device")


def _timeline() -> list[dict[str, Any]]:
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, ts, app, window_title, activity, topics
            FROM timeline_events
            WHERE device_id = %s
            ORDER BY ts DESC
            """,
            (DEVICE_ID,),
        )
        rows = cur.fetchall()
    return [
        {
            "id": row[0],
            "ts": row[1].astimezone(TIMEZONE).isoformat(),
            "time": row[1].astimezone(TIMEZONE).strftime("%H:%M"),
            "date": row[1].astimezone(TIMEZONE).strftime("%b %d"),
            "app": row[2] or "Unknown",
            "window_title": row[3] or "",
            "activity": row[4] or "",
            "topics": row[5] or [],
        }
        for row in rows
    ]


def _embedding_is_real(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        values = [float(value) for value in raw.strip("[]").split(",")]
    except ValueError:
        return False
    return (
        len(values) == 1024
        and all(math.isfinite(value) for value in values)
        and any(abs(value) > 1e-9 for value in values)
    )


def _valid_bbox(block: dict[str, Any], *, width: int, height: int) -> bool:
    bbox = block.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return False
    try:
        x1, y1, x2, y2 = (float(value) for value in bbox)
    except (TypeError, ValueError):
        return False
    return (
        all(math.isfinite(value) for value in (x1, y1, x2, y2))
        and 0 <= x1 < x2 <= width
        and 0 <= y1 < y2 <= height
    )


def _event_evidence(event_id: int) -> dict[str, Any]:
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, ts, app, window_title, url, activity, screenshot_path,
                   ocr_blocks
            FROM timeline_events
            WHERE id = %s AND device_id = %s AND ocr_text ILIKE '%%1842%%'
            """,
            (event_id, DEVICE_ID),
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("agent answer did not cite the synthetic PR event")

    screenshot_path = Path(row[6] or "").expanduser().resolve()
    if (
        not screenshot_path.is_relative_to(_data_root())
        or not screenshot_path.is_file()
    ):
        raise RuntimeError("synthetic evidence is missing or outside DATA_ROOT")
    with Image.open(screenshot_path) as image:
        width, height = image.size
    highlights = [
        block
        for block in (row[7] or [])
        if "1842" in str(block.get("text", "")).casefold()
    ]
    if not highlights or any(
        not _valid_bbox(block, width=width, height=height) for block in highlights
    ):
        raise RuntimeError("synthetic PR OCR has no valid in-image bbox for 1842")
    event_time = row[1].astimezone(TIMEZONE)
    return {
        "event_id": row[0],
        "ts": event_time.isoformat(),
        "time": event_time.strftime("%H:%M"),
        "date": event_time.strftime("%A, %b %d"),
        "app": row[2] or "Unknown",
        "window_title": row[3] or "",
        "url": row[4] or "",
        "activity": row[5] or "",
        "citation": f"[event#{row[0]} {event_time:%H:%M} {row[2] or 'Unknown'}]",
        "image_url": f"/evidence/{row[0]}",
        "image_width": width,
        "image_height": height,
        "highlights": highlights,
    }


def _run_recall() -> dict[str, Any]:
    body = {
        "model": "dejaview",
        "messages": [
            {
                "role": "user",
                "content": "上周三下午看的那个 ROCm PR 是哪个？请附事件引用和截图证据。",
            }
        ],
        "temperature": 0,
        "max_tokens": 500,
        "stream": False,
    }
    with httpx.Client(timeout=httpx.Timeout(360.0, connect=5.0)) as client:
        response = client.post(f"{AGENTD_URL}/v1/chat/completions", json=body)
        response.raise_for_status()
    content = response.json()["choices"][0]["message"].get("content") or ""
    citations = CITATION_RE.findall(content)
    if not citations:
        raise RuntimeError(
            "agent answer contained no exact [event#id HH:MM app] citation"
        )
    cited_ids = [int(citation[0]) for citation in citations]
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, ts, app, ocr_text
            FROM timeline_events
            WHERE id = ANY(%s) AND device_id = %s
            """,
            (cited_ids, DEVICE_ID),
        )
        rows = {row[0]: row for row in cur.fetchall()}
    if set(cited_ids) != set(rows):
        raise RuntimeError("answer included a fabricated or non-demo event id")

    pr_event_id: int | None = None
    for raw_id, cited_time, cited_app in citations:
        event_id = int(raw_id)
        row = rows[event_id]
        expected_time = row[1].astimezone(TIMEZONE).strftime("%H:%M")
        expected_app = row[2] or "Unknown"
        if cited_time != expected_time or cited_app != expected_app:
            raise RuntimeError(
                f"citation metadata mismatch for event#{event_id}: "
                f"{cited_time}/{cited_app} != {expected_time}/{expected_app}"
            )
        if "1842" in (row[3] or ""):
            pr_event_id = event_id
    if pr_event_id is None or "1842" not in content:
        raise RuntimeError("grounded answer did not identify the synthetic PR #1842")

    evidence = _event_evidence(pr_event_id)
    return {
        "answer": content,
        "citation_gate": "all ids, times, and apps resolved to demo-p34",
        "citation_count": len(citations),
        **evidence,
    }


def _evidence_path(event_id: int) -> Path:
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT screenshot_path
            FROM timeline_events
            WHERE id = %s AND device_id = %s
            """,
            (event_id, DEVICE_ID),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="synthetic event not found")
    path = Path(row[0] or "").expanduser().resolve()
    if not path.is_relative_to(_data_root()) or not path.is_file():
        raise HTTPException(status_code=404, detail="synthetic evidence not found")
    return path


def _smoke_payload(model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "Reply OK."}],
        "temperature": 0,
        "max_tokens": 2,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }


async def _gateway_models_ready(
    url: str,
    *,
    include_brain: bool,
) -> dict[str, bool]:
    base = url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    results = {"fast": False, "brain": False}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=1.5)) as client:
        models = ("fast", "brain") if include_brain else ("fast",)
        for model in models:
            try:
                response = await client.post(
                    f"{base}/chat/completions",
                    json=_smoke_payload(model),
                )
                response.raise_for_status()
                if not (response.json().get("choices") or []):
                    break
                results[model] = True
            except (httpx.HTTPError, AttributeError, TypeError, ValueError):
                break
    return results


async def _connectivity(
    *,
    force: bool = False,
    include_daily: bool = False,
) -> dict[str, Any]:
    global _connectivity_cache

    now = time.monotonic()
    if (
        not force
        and _connectivity_cache is not None
        and now - _connectivity_cache[0] < CONNECTIVITY_CACHE_TTL_SECONDS
        and (not include_daily or _connectivity_cache[1].get("daily_checked") is True)
    ):
        return dict(_connectivity_cache[1])

    async with _connectivity_lock:
        now = time.monotonic()
        if (
            not force
            and _connectivity_cache is not None
            and now - _connectivity_cache[0] < CONNECTIVITY_CACHE_TTL_SECONDS
            and (
                not include_daily or _connectivity_cache[1].get("daily_checked") is True
            )
        ):
            return dict(_connectivity_cache[1])

        remote, local, attestations = await asyncio.gather(
            _gateway_models_ready(REMOTE_GATEWAY, include_brain=include_daily),
            _gateway_models_ready(LOCAL_GATEWAY, include_brain=include_daily),
            _runtime_attestations(include_brain=include_daily),
        )
        remote_attested = attestations["remote"].get("ok") is True
        local_attested = attestations["local"].get("ok") is True
        remote_link = remote["fast"] and remote_attested
        local_link = local["fast"] and local_attested
        remote_daily = remote_link and remote["brain"] and remote_attested
        local_daily = local_link and local["brain"] and local_attested
        result = {
            "remote_radeon": remote_link,
            "local_metal": local_link,
            "remote_daily_ready": remote_daily,
            "local_daily_ready": local_daily,
            "probes": {
                "remote": remote,
                "local": local,
                "attestation": attestations,
            },
            "daily_checked": include_daily,
            "mode": (
                "radeon"
                if remote_link
                else ("local_fallback" if local_link else "offline")
            ),
            "daily_mode": (
                "radeon"
                if remote_daily
                else (
                    "local_fallback"
                    if local_daily
                    else ("offline" if include_daily else "unchecked")
                )
            ),
            "checked_at": datetime.now(TIMEZONE).isoformat(),
        }
        _connectivity_cache = (time.monotonic(), result)
        return dict(result)


def _run_memory_growth() -> dict[str, Any]:
    for fixture, _, _ in SAFE_FIXTURES:
        if not fixture.is_file():
            raise RuntimeError(f"safe fixture missing: {fixture}")

    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT screenshot_path
            FROM timeline_events
            WHERE device_id = %s
              AND url IS DISTINCT FROM 'https://code.demo-acme.io/rocm-lab/pull/1842'
            """,
            (DEVICE_ID,),
        )
        old_paths = [
            Path(row[0]).expanduser().resolve() for row in cur.fetchall() if row[0]
        ]
        cur.execute(
            """
            DELETE FROM timeline_events
            WHERE device_id = %s
              AND url IS DISTINCT FROM 'https://code.demo-acme.io/rocm-lab/pull/1842'
            """,
            (DEVICE_ID,),
        )
        cur.execute("DELETE FROM sentinel_audit WHERE device_id = %s", (DEVICE_ID,))
        conn.commit()
    for path in old_paths:
        if path.is_relative_to(_data_root()) and path.is_file():
            path.unlink()

    now = datetime.now(TIMEZONE)
    acknowledgements: list[dict[str, Any]] = []
    with httpx.Client(timeout=httpx.Timeout(360.0, connect=5.0)) as client:
        for index, (fixture, app_name, window_title) in enumerate(SAFE_FIXTURES):
            meta = {
                "device_id": DEVICE_ID,
                "ts": (
                    now.replace(microsecond=0) + timedelta(seconds=index)
                ).isoformat(),
                "app": app_name,
                "window_title": window_title,
                "url": f"https://synthetic.demo.invalid/window/{index + 1}",
                "trigger": "change",
            }
            with fixture.open("rb") as image:
                response = client.post(
                    f"{MEMORYD_URL}/v1/ingest/frame",
                    files={"file": (fixture.name, image, "image/png")},
                    data={"meta": json.dumps(meta)},
                )
            response.raise_for_status()
            ack = response.json()
            if ack.get("accepted") is not True or not ack.get("event_id"):
                raise RuntimeError(
                    f"safe fixture did not create an event: {fixture.name}: {ack}"
                )
            acknowledgements.append({"fixture": fixture.name, **ack})

    created_ids = [int(ack["event_id"]) for ack in acknowledgements]
    if len(set(created_ids)) != len(SAFE_FIXTURES):
        raise RuntimeError("memory growth acknowledgements reused an event id")
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, ts, app, window_title, activity, ocr_text, ocr_blocks,
                   screenshot_path, embedding::text
            FROM timeline_events
            WHERE id = ANY(%s) AND device_id = %s
            ORDER BY id
            """,
            (created_ids, DEVICE_ID),
        )
        rows = cur.fetchall()
        cur.execute(
            """
            SELECT category, decision
            FROM sentinel_audit
            WHERE device_id = %s
            ORDER BY id
            """,
            (DEVICE_ID,),
        )
        audit_rows = cur.fetchall()
    if {int(row[0]) for row in rows} != set(created_ids):
        raise RuntimeError(
            f"memory growth produced {len(rows)}/{len(SAFE_FIXTURES)} events"
        )
    if len(audit_rows) != len(SAFE_FIXTURES) or any(
        category != "normal" or decision != "allow" for category, decision in audit_rows
    ):
        raise RuntimeError("safe fixtures did not pass three real sentinel audits")

    expected_titles = {title for _, _, title in SAFE_FIXTURES}
    actual_titles = {row[3] for row in rows}
    if actual_titles != expected_titles:
        raise RuntimeError(
            "memory growth did not preserve all three synthetic window titles: "
            f"{sorted(str(title) for title in actual_titles)}"
        )
    events: list[dict[str, Any]] = []
    for row in rows:
        screenshot = Path(row[7] or "").expanduser().resolve()
        activity = (row[4] or "").strip()
        ocr_text = (row[5] or "").strip()
        if not activity or "stub activity" in activity.casefold():
            raise RuntimeError("perceive did not return a real activity")
        if not screenshot.is_relative_to(_data_root()) or not screenshot.is_file():
            raise RuntimeError("memoryd wrote a screenshot outside demo DATA_ROOT")
        with Image.open(screenshot) as image:
            width, height = image.size
        text_blocks = [
            block
            for block in (row[6] or [])
            if isinstance(block, dict) and str(block.get("text", "")).strip()
        ]
        if (
            not ocr_text
            or not isinstance(row[6], list)
            or not text_blocks
            or any(
                not _valid_bbox(block, width=width, height=height)
                for block in text_blocks
            )
        ):
            raise RuntimeError("ocrd returned missing or invalid in-image text bboxes")
        if not _embedding_is_real(row[8]):
            raise RuntimeError("embed returned a missing, zero, or wrong-size vector")
        events.append(
            {
                "id": row[0],
                "ts": row[1].astimezone(TIMEZONE).isoformat(),
                "app": row[2] or "Unknown",
                "window_title": row[3],
                "activity": activity,
                "ocr_chars": len(ocr_text),
                "ocr_blocks": len(row[6]),
                "screenshot": str(screenshot),
                "embedding_dims": 1024,
            }
        )
    return {
        "pass": True,
        "pipeline": "sentinel → OCR → novelty → perceive → embed → timeline",
        "created": len(events),
        "acks": acknowledgements,
        "events": events,
    }


def _run_sentinel() -> dict[str, Any]:
    if not BANK_FIXTURE.is_file():
        raise RuntimeError(f"bank fixture missing: {BANK_FIXTURE}")
    now = datetime.now(TIMEZONE)
    device_id = f"demo-p34-act3-{now:%Y%m%d%H%M%S%f}"
    before_files = set(_data_root().glob(f"screenshots/*/*/*/{device_id}_*"))
    meta = {
        "device_id": device_id,
        "ts": now.isoformat(),
        "app": "Synthetic Acme Bank",
        "window_title": "Bank sign-in fixture",
        "url": "https://bank.demo.invalid/sign-in",
        "trigger": "change",
    }
    with (
        BANK_FIXTURE.open("rb") as fixture,
        httpx.Client(timeout=httpx.Timeout(360.0, connect=5.0)) as client,
    ):
        response = client.post(
            f"{MEMORYD_URL}/v1/ingest/frame",
            files={"file": (BANK_FIXTURE.name, fixture, "image/png")},
            data={"meta": json.dumps(meta)},
        )
        response.raise_for_status()
    ack = response.json()

    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, ts, category, decision, confidence
            FROM sentinel_audit
            WHERE device_id = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (device_id,),
        )
        audit = cur.fetchone()
        cur.execute(
            "SELECT count(*) FROM sentinel_audit WHERE device_id = %s",
            (device_id,),
        )
        audit_rows = int(cur.fetchone()[0])
        cur.execute(
            "SELECT count(*) FROM timeline_events WHERE device_id = %s",
            (device_id,),
        )
        timeline_rows = int(cur.fetchone()[0])
    after_files = set(_data_root().glob(f"screenshots/*/*/*/{device_id}_*"))
    new_files = sorted(str(path) for path in after_files - before_files)
    if audit is None:
        raise RuntimeError("memoryd returned without a sentinel_audit row")
    passed = (
        ack.get("accepted") is False
        and (ack.get("sentinel") or {}).get("decision") == "block"
        and (ack.get("sentinel") or {}).get("category") == audit[2]
        and audit[3] == "block"
        and audit[2] != "normal"
        and audit_rows == 1
        and timeline_rows == 0
        and not new_files
    )
    return {
        "pass": passed,
        "device_id": device_id,
        "ack": ack,
        "audit": {
            "id": audit[0],
            "ts": audit[1].astimezone(TIMEZONE).isoformat(),
            "category": audit[2],
            "decision": audit[3],
            "confidence": audit[4],
        },
        "timeline_rows": timeline_rows,
        "audit_rows": audit_rows,
        "new_screenshot_files": new_files,
    }


def _run_preference() -> dict[str, Any]:
    settings = Settings.from_env()
    with httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        response = client.post(
            (
                "http://127.0.0.1:8100/v3/workspaces/"
                f"{HONCHO_WORKSPACE}/conclusions/list"
            ),
            json={"filters": {"session_id": HONCHO_SESSION}},
        )
        response.raise_for_status()
        page = response.json()
    conclusions = page.get("items") or []
    if any(item.get("session_id") != HONCHO_SESSION for item in conclusions):
        raise RuntimeError("Honcho returned a conclusion outside the synthetic session")
    conclusion_count = len(conclusions)
    if conclusion_count <= 0:
        raise RuntimeError("Honcho synthetic session has no derived conclusions")
    question = (
        "Based on the synthetic persona's established habits, would they prefer "
        "a cloud-hosted black box or a local, inspectable, config-driven pipeline? "
        "Explain using only the user model."
    )
    result = query_user_model(
        settings,
        question=question,
        session_id=HONCHO_SESSION,
        workspace_id=HONCHO_WORKSPACE,
        peer_id=HONCHO_PEER,
    )
    if not (result.get("answer") or "").strip():
        raise RuntimeError("Honcho returned no user-model answer")
    return {
        "derived_conclusions": conclusion_count,
        "conclusion_session": HONCHO_SESSION,
        **result,
    }


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _iter_process_output(
    process: subprocess.Popen[str],
    *,
    timeout_seconds: float,
) -> Iterator[str]:
    if process.stdout is None:
        raise RuntimeError("daily-report process has no stdout pipe")

    output_queue: queue.Queue[str | object] = queue.Queue()
    stream_end = object()

    def pump_stdout() -> None:
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                output_queue.put(raw_line)
        finally:
            output_queue.put(stream_end)

    reader = threading.Thread(
        target=pump_stdout,
        name="p34-daily-output",
        daemon=True,
    )
    reader.start()
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"daily report exceeded {timeout_seconds:.0f}s backend budget"
            )
        try:
            item = output_queue.get(timeout=min(0.25, remaining))
        except queue.Empty:
            continue
        if item is stream_end:
            return
        yield str(item).rstrip()


def _daily_stream(candidates: list[tuple[str, str]]) -> Iterator[str]:
    today = datetime.now(TIMEZONE).date().isoformat()
    failures: list[str] = []
    for backend, gateway_url in candidates:
        yield _sse({"type": "backend", "backend": backend})
        with tempfile.TemporaryDirectory(prefix="dejaview-p34-") as temp_dir:
            output = Path(temp_dir) / "daily-report.md"
            audit_output = Path(temp_dir) / "daily-report-audit.json"
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(DAILY_SCRIPT),
                    "--date",
                    today,
                    "--device-id",
                    DEVICE_ID,
                    "--gateway-url",
                    gateway_url,
                    "--output",
                    str(output),
                    "--audit-output",
                    str(audit_output),
                ],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            captured: list[str] = []
            try:
                try:
                    for line in _iter_process_output(
                        process,
                        timeout_seconds=DAILY_BACKEND_TIMEOUT_SECONDS,
                    ):
                        if not line:
                            continue
                        captured.append(line)
                        if not line.startswith("[Done]"):
                            yield _sse({"type": "trace", "line": line})
                except TimeoutError as exc:
                    _terminate_process(process)
                    failures.append(f"{backend}: {exc}")
                    yield _sse(
                        {
                            "type": "trace",
                            "line": f"[Fallback] {backend} timed out; trying next path",
                        }
                    )
                    continue
                try:
                    return_code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _terminate_process(process)
                    failures.append(
                        f"{backend}: process did not exit after stdout closed"
                    )
                    yield _sse(
                        {
                            "type": "trace",
                            "line": (
                                f"[Fallback] {backend} did not exit; trying next path"
                            ),
                        }
                    )
                    continue
            finally:
                _terminate_process(process)
            if return_code != 0:
                failure = "\n".join(captured[-12:]) or "daily report failed"
                failures.append(f"{backend}: {failure}")
                yield _sse(
                    {
                        "type": "trace",
                        "line": f"[Fallback] {backend} failed; trying next path",
                    }
                )
                continue
            try:
                report = output.read_text(encoding="utf-8")
                audit = json.loads(audit_output.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                failures.append(f"{backend}: invalid result artifacts: {exc}")
                yield _sse(
                    {
                        "type": "trace",
                        "line": (
                            f"[Fallback] {backend} produced invalid proof artifacts; "
                            "trying next path"
                        ),
                    }
                )
                continue
            yield _sse(
                {
                    "type": "result",
                    "report": report,
                    "audit": audit,
                }
            )
            yield _sse({"type": "done"})
            return
    yield _sse(
        {
            "type": "error",
            "message": "\n".join(failures) or "no healthy daily-report backend",
        }
    )
    yield _sse({"type": "done"})


app = FastAPI(
    title="DejaView P3.4 demo stage",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.on_event("startup")
async def demo_environment_guard() -> None:
    await asyncio.to_thread(_assert_demo_environment)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(
        SCRIPT_DIR / "demo_stage.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/demo_stage.css")
async def stylesheet() -> FileResponse:
    return FileResponse(
        SCRIPT_DIR / "demo_stage.css",
        media_type="text/css",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/demo_stage.js")
async def javascript() -> FileResponse:
    return FileResponse(
        SCRIPT_DIR / "demo_stage.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/connectivity")
async def connectivity() -> dict[str, Any]:
    return await _connectivity()


@app.post("/api/connectivity/disconnect")
async def disconnect_remote_compute() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(_disconnect_remote_tunnel)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:1600]) from exc


@app.get("/api/timeline")
async def timeline() -> dict[str, Any]:
    return {"device_id": DEVICE_ID, "events": await asyncio.to_thread(_timeline)}


@app.post("/api/memory-growth")
async def memory_growth() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(_run_memory_growth)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:1600]) from exc


@app.post("/api/sentinel")
async def sentinel() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(_run_sentinel)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:1600]) from exc


@app.post("/api/recall")
async def recall() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(_run_recall)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:1600]) from exc


@app.get("/evidence/{event_id}")
async def evidence(event_id: int) -> FileResponse:
    path = await asyncio.to_thread(_evidence_path, event_id)
    return FileResponse(path, media_type="image/png")


@app.post("/api/preference")
async def preference() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(_run_preference)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:1600]) from exc


@app.get("/api/daily/stream")
async def daily_stream() -> StreamingResponse:
    state = await _connectivity(force=True, include_daily=True)
    candidates: list[tuple[str, str]] = []
    if state["remote_daily_ready"]:
        candidates.append(("Radeon ROCm", REMOTE_GATEWAY))
    if state["local_daily_ready"]:
        candidates.append(("Local Metal fallback", LOCAL_GATEWAY))
    if not candidates:
        raise HTTPException(
            status_code=503,
            detail="neither gateway completed real fast+brain inference smokes",
        )
    return StreamingResponse(
        _daily_stream(candidates),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8120, log_level="warning")


if __name__ == "__main__":
    main()
