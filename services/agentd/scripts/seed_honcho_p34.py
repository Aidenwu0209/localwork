#!/usr/bin/env python3
"""Seed and verify an isolated synthetic Honcho profile for the P3.4 video."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
import psycopg

HONCHO_URL = os.environ.get("HONCHO_URL", "http://127.0.0.1:8100").rstrip("/")
DEMO_DSN = "postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview_demo"
WORKSPACE = "dejaview-p34"
PEER = "demo-owner"
SESSION = "p3-4-synthetic"
PEER_CARD_DISABLED = {"peer_card": {"use": False, "create": False}}
MESSAGES = [
    "I keep source documents beside my notes so I can inspect where every claim came from.",
    "I review configuration changes in git and ask for exact diffs before I apply them.",
    "Before a database or model change, I want an audit trail and a tested rollback path.",
    "My project files stay on my laptop; I rent a GPU only for short compute bursts.",
    "When a remote machine disappears, I expect my saved work to remain available.",
    "I accept a slower travel mode if the workflow still runs without a network.",
    "Benchmark claims should include raw runs, medians, hardware context, and screenshots.",
    "I use a privacy gate before any screenshot enters my searchable work history.",
]


def _post(
    client: httpx.Client,
    path: str,
    payload: dict[str, Any],
    *,
    allow_conflict: bool = False,
) -> dict[str, Any]:
    response = client.post(f"{HONCHO_URL}{path}", json=payload)
    if allow_conflict and response.status_code == 409:
        return {}
    response.raise_for_status()
    value = response.json()
    return value if isinstance(value, dict) else {"value": value}


def _wait_for_deriver(client: httpx.Client, timeout: float = 360.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"{HONCHO_URL}/v3/workspaces/{WORKSPACE}/queue/status")
        response.raise_for_status()
        value = response.json()
        last = value if isinstance(value, dict) else {}
        pending = int(last.get("pending_work_units") or 0)
        in_progress = int(last.get("in_progress_work_units") or 0)
        if pending + in_progress == 0:
            return last
        time.sleep(2)
    raise TimeoutError(f"Honcho deriver queue did not drain: {last}")


def _assert_unseeded(sessions_page: dict[str, Any]) -> None:
    sessions = sessions_page.get("items")
    if not isinstance(sessions, list):
        raise TypeError("Honcho sessions/list returned no items page")
    try:
        session_total = int(sessions_page.get("total", len(sessions)))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Honcho sessions/list returned an invalid total") from exc
    if session_total != 0 or sessions:
        existing = [item.get("id") for item in sessions if isinstance(item, dict)]
        raise RuntimeError(
            "Honcho synthetic seed refuses to append to an existing session; "
            f"found total={session_total}, sessions={existing}. "
            "Run make demo-data-reset first."
        )


def main() -> int:
    if os.environ.get("DEJAVIEW_DEMO_MODE") != "1":
        raise SystemExit("DEJAVIEW_DEMO_MODE=1 is required")
    if HONCHO_URL != "http://127.0.0.1:8100":
        raise SystemExit("HONCHO_URL must be the isolated local Honcho")
    if os.environ.get("TIMELINE_DB_URL", DEMO_DSN) != DEMO_DSN:
        raise SystemExit("TIMELINE_DB_URL must target local database dejaview_demo")
    with psycopg.connect(DEMO_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        if cur.fetchone()[0] != "dejaview_demo":
            raise SystemExit("connected database is not dejaview_demo")
    with httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
        health = client.get(f"{HONCHO_URL}/health")
        health.raise_for_status()
        workspace = _post(
            client,
            "/v3/workspaces",
            {
                "id": WORKSPACE,
                "name": WORKSPACE,
                "configuration": PEER_CARD_DISABLED,
            },
            allow_conflict=True,
        )
        peer_card_config = (workspace.get("configuration") or {}).get("peer_card") or {}
        if (
            peer_card_config.get("use") is not False
            or peer_card_config.get("create") is not False
        ):
            raise RuntimeError(
                "existing demo workspace does not disable global peer cards; "
                "run make demo-data-reset"
            )
        _post(
            client,
            f"/v3/workspaces/{WORKSPACE}/peers",
            {"id": PEER, "name": PEER},
            allow_conflict=True,
        )
        sessions_response = client.post(
            f"{HONCHO_URL}/v3/workspaces/{WORKSPACE}/sessions/list",
            json={},
        )
        sessions_response.raise_for_status()
        sessions_page = sessions_response.json()
        _assert_unseeded(sessions_page)
        peer_card_response = client.get(
            f"{HONCHO_URL}/v3/workspaces/{WORKSPACE}/peers/{PEER}/card"
        )
        peer_card_response.raise_for_status()
        if peer_card_response.json().get("peer_card") not in (None, []):
            raise RuntimeError("demo peer has a nonempty global peer card")
        _post(
            client,
            f"/v3/workspaces/{WORKSPACE}/sessions",
            {"id": SESSION, "configuration": PEER_CARD_DISABLED},
            allow_conflict=True,
        )
        _post(
            client,
            f"/v3/workspaces/{WORKSPACE}/sessions/{SESSION}/messages",
            {
                "messages": [
                    {"content": message, "peer_id": PEER} for message in MESSAGES
                ]
            },
        )
        time.sleep(3)
        queue = _wait_for_deriver(client)
        conclusions_response = client.post(
            f"{HONCHO_URL}/v3/workspaces/{WORKSPACE}/conclusions/list",
            json={"filters": {"session_id": SESSION}},
        )
        conclusions_response.raise_for_status()
        conclusions_page = conclusions_response.json()
        conclusions = conclusions_page.get("items") or []
        if any(item.get("session_id") != SESSION for item in conclusions):
            raise RuntimeError("Honcho returned a conclusion outside demo session")
        conclusion_count = len(conclusions)
        if conclusion_count <= 0:
            raise RuntimeError("Honcho queue drained without derived conclusions")
        answer = _post(
            client,
            f"/v3/workspaces/{WORKSPACE}/peers/{PEER}/chat",
            {
                "query": (
                    "Would this person prefer a cloud-hosted black box or a "
                    "local, inspectable, config-driven pipeline? Explain why."
                ),
                "session_id": SESSION,
                "stream": False,
                "reasoning_level": "low",
            },
        )
        peer_card_response = client.get(
            f"{HONCHO_URL}/v3/workspaces/{WORKSPACE}/peers/{PEER}/card"
        )
        peer_card_response.raise_for_status()
        if peer_card_response.json().get("peer_card") not in (None, []):
            raise RuntimeError(
                "Honcho populated a global peer card during demo seeding"
            )

    print(
        json.dumps(
            {
                "workspace": WORKSPACE,
                "peer": PEER,
                "session": SESSION,
                "synthetic_messages": len(MESSAGES),
                "queue": queue,
                "derived_conclusions": conclusion_count,
                "verification_answer": answer.get("content")
                or answer.get("message")
                or answer.get("response"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
