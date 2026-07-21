#!/usr/bin/env python3
"""M2.6: light up the Honcho memory link end-to-end.

Pipeline: ingest 20 synthetic messages as one peer's session -> wait for the
deriver queue to drain (it produces atomic facts via the `perceive` gateway
model) -> ask 3 dialectic questions via /peers/{id}/chat (the `brain` model) and
print the answers. Handbook §6.4 acceptance baseline.

Run against a live Honcho stack (compose.honcho.yml up) with the inference
gateway serving `perceive` (deriver) and `brain` (dialectic). Both are dev-mapped
to the E4B instance in deploy/server/litellm.yaml.

Usage:
    python3 seed_honcho.py            # full pipeline
    python3 seed_honcho.py --questions-only   # skip ingest, just ask
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

HONCHO = "http://127.0.0.1:8100"
WORKSPACE_NAME = "dejaview"
PEER_NAME = "owner"
# M6.2 synthetic persona (Jordan Lee) — 30 messages; we ingest the first 20.
MESSAGES_FILE = Path(__file__).resolve().parents[3] / "tests/assets/messages/synthetic_messages.json"

QUESTIONS = [
    "What does this person do for work?",
    "What tools or technologies does this person prefer?",
    "What is this person currently working on?",
]


def _post(client: httpx.Client, path: str, **kwargs) -> dict:
    """POST and raise with the response body on error."""
    r = client.post(f"{HONCHO}{path}", **kwargs)
    if r.status_code >= 400:
        raise RuntimeError(f"{path} -> {r.status_code}: {r.text[:300]}")
    return r.json()


def _ensure_workspace(client: httpx.Client) -> str:
    # Honcho v3 wants a client-supplied id; reuse the same id on reruns.
    try:
        ws = _post(client, "/v3/workspaces", json={"id": WORKSPACE_NAME, "name": WORKSPACE_NAME})
        return ws.get("id", WORKSPACE_NAME)
    except RuntimeError as exc:
        if "409" in str(exc) or "already" in str(exc).lower():
            return WORKSPACE_NAME
        raise


def _ensure_peer(client: httpx.Client, workspace_id: str) -> str:
    try:
        p = _post(client, f"/v3/workspaces/{workspace_id}/peers",
                  json={"id": PEER_NAME, "name": PEER_NAME})
        return p.get("id", PEER_NAME)
    except RuntimeError as exc:
        if "409" in str(exc) or "already" in str(exc).lower():
            return PEER_NAME
        raise


def _ensure_session(client: httpx.Client, workspace_id: str) -> str:
    # Honcho v3 requires a client-supplied session id (handbook §6.2:
    # "session=按天"). Use a stable id so reruns append to the same session.
    session_id = "m2_6-seed"
    s = _post(client, f"/v3/workspaces/{workspace_id}/sessions",
              json={"id": session_id})
    return s.get("id", session_id)


def _queue_idle(client: httpx.Client, workspace_id: str, timeout: float = 180.0) -> bool:
    """Wait until the deriver queue is empty (all messages processed)."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        r = client.get(f"{HONCHO}/v3/workspaces/{workspace_id}/queue/status")
        status = r.json()
        last = status
        # status shape: {"queued": N, ...} or {"messages": {"queued": N}}
        queued = (status.get("messages") or {}).get("queued") if isinstance(status.get("messages"), dict) else status.get("queued")
        if queued is None:
            queued = status.get("total_in_queue", 0)
        if int(queued or 0) == 0:
            return True
        time.sleep(3)
    print(f"  queue did not drain within {timeout}s; last status: {last}", file=sys.stderr)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions-only", action="store_true")
    ap.add_argument("--n", type=int, default=20, help="messages to ingest (max 30)")
    args = ap.parse_args()

    with open(MESSAGES_FILE) as f:
        msgs = json.load(f)[: args.n]
    print(f"loaded {len(msgs)} synthetic messages from {MESSAGES_FILE.name}")

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        # health
        h = client.get(f"{HONCHO}/health")
        if h.status_code != 200:
            print(f"Honcho not healthy: {h.status_code}", file=sys.stderr)
            return 1

        ws = _ensure_workspace(client)
        peer = _ensure_peer(client, ws)
        sess = _ensure_session(client, ws)
        print(f"workspace={ws} peer={peer} session={sess}")

        if not args.questions_only:
            # ingest messages (batch up to 100)
            batch = [{"content": m["content"], "peer_id": peer} for m in msgs]
            print(f"ingesting {len(batch)} messages...")
            t0 = time.monotonic()
            res = _post(client, f"/v3/workspaces/{ws}/sessions/{sess}/messages",
                        json={"messages": batch})
            dt = time.monotonic() - t0
            print(f"  ingested in {dt:.1f}s: {json.dumps(res)[:200]}")

            # wait for deriver: give it a moment to enqueue, then poll.
            print("waiting for deriver queue to drain (perceive extracts facts)...")
            time.sleep(5)  # let the queue populate before the first check
            t0 = time.monotonic()
            ok = _queue_idle(client, ws, timeout=300.0)
            dt = time.monotonic() - t0
            print(f"  queue idle={ok} after {dt:.1f}s")

            # check conclusions produced
            concl = _post(client, f"/v3/workspaces/{ws}/conclusions/list",
                          json={"peer_id": peer, "session_id": sess})
            results = concl.get("results", concl) if isinstance(concl, dict) else concl
            print(f"  conclusions produced: {len(results) if isinstance(results, list) else '?'}")
            # show a few
            if isinstance(results, list):
                for c in results[:3]:
                    cond = c.get("condition") or c.get("conclusion") or c.get("text") or str(c)[:120]
                    print(f"    - {str(cond)[:120]}")

        # dialectic questions
        print("\n=== dialectic questions (brain) ===")
        for q in QUESTIONS:
            print(f"\nQ: {q}")
            t0 = time.monotonic()
            try:
                ans = _post(client, f"/v3/workspaces/{ws}/peers/{peer}/chat",
                            json={"query": q, "session_id": sess},
                            timeout=httpx.Timeout(120.0, connect=10.0))
                dt = time.monotonic() - t0
                reply = ans.get("message") or ans.get("content") or ans.get("response") or str(ans)[:400]
                print(f"A ({dt:.1f}s): {str(reply)[:500]}")
            except Exception as exc:
                print(f"  FAILED: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
