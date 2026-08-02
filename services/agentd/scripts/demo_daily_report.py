#!/usr/bin/env python3
"""Visible Planner → Retriever → Writer → Reviewer demo over synthetic events."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from agentd.config import Settings
from agentd.router import BothBackendsFailed, ComputeFailure, ComputeRouter

DEFAULT_DSN = "postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview_demo"
CITATION_RE = re.compile(r"\[event#(\d+) ([0-2]\d:[0-5]\d) ([^\]]+)\]")
MAX_MODEL_ATTEMPTS = 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date",
        default=datetime.now(tz=ZoneInfo("Asia/Kuching")).date().isoformat(),
    )
    parser.add_argument("--device-id", default="demo-p34")
    parser.add_argument(
        "--radeon-gateway-url",
        default=os.environ.get(
            "RADEON_GATEWAY_URL",
            os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000/v1"),
        ),
    )
    parser.add_argument(
        "--local-gateway-url",
        default=os.environ.get("LOCAL_GATEWAY_URL", "http://127.0.0.1:4000/v1"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/assets/demo/daily-report.md"),
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=Path("docs/assets/demo/daily-report-audit.json"),
    )
    args = parser.parse_args()
    if not args.device_id.startswith("demo-"):
        parser.error("device-id must start with 'demo-' to prevent real-data export")
    try:
        args.report_date = date.fromisoformat(args.date)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def _router_chat(
    router: ComputeRouter,
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    json_mode: bool = False,
) -> tuple[str, dict[str, Any]]:
    result = router.chat(
        model,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0,
        max_tokens=max_tokens,
        response_format={"type": "json_object"} if json_mode else None,
    )
    return result.content, {**result.route.as_dict(), "latency_ms": result.route.latency_ms}


def _parse_json_object(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model did not return a JSON object")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object")
    return value


def _validate_plan_response(value: dict[str, Any]) -> str | None:
    sections = value.get("sections")
    if (
        not isinstance(sections, list)
        or not 2 <= len(sections) <= 4
        or any(
            not isinstance(section, str) or not section.strip() for section in sections
        )
    ):
        return "sections must contain 2-4 nonempty strings"
    if not isinstance(value.get("focus"), str) or not value["focus"].strip():
        return "focus must be a nonempty string"
    return None


def _validate_review_response(value: dict[str, Any]) -> str | None:
    decision = str(value.get("decision", "")).lower()
    issues = value.get("issues")
    if decision not in {"pass", "reject"}:
        return "decision must be pass or reject"
    if not isinstance(issues, list):
        return "issues must be a list"
    if decision == "pass" and issues:
        return "a pass decision cannot contain issues"
    if decision == "reject" and not issues:
        return "a reject decision must identify at least one issue"
    return None


def _router_json_with_retry(
    router: ComputeRouter,
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    validate: Callable[[dict[str, Any]], str | None],
    route_metadata: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], int]:
    """Require a real model JSON response, retrying one formatting contradiction."""

    failures: list[str] = []
    feedback = ""
    for attempt in range(1, MAX_MODEL_ATTEMPTS + 1):
        raw, route = _router_chat(
            router,
            model=model,
            system=system,
            user=user + feedback,
            max_tokens=max_tokens,
            json_mode=True,
        )
        if route_metadata is not None:
            route_metadata.append(route)
        try:
            value = _parse_json_object(raw)
            problem = validate(value)
            if problem is None:
                return value, attempt
        except json.JSONDecodeError:
            problem = "model returned invalid JSON"
        except (TypeError, ValueError) as exc:
            problem = str(exc)
        failures.append(f"attempt {attempt}: {problem}")
        feedback = (
            "\n\nYour previous response was invalid because: "
            f"{problem}. Do not refuse and do not explain. Return only the "
            "corrected JSON object requested above."
        )
    raise ValueError("; ".join(failures))


def _retrieve_events(
    dsn: str,
    *,
    device_id: str,
    report_date: date,
    timezone_name: str,
) -> list[dict[str, Any]]:
    tz = ZoneInfo(timezone_name)
    start = datetime.combine(report_date, time.min, tz)
    end = datetime.combine(report_date, time.max, tz)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, ts, app, window_title, activity, ocr_text
            FROM timeline_events
            WHERE device_id = %s AND ts BETWEEN %s AND %s
            ORDER BY ts
            """,
            (device_id, start, end),
        )
        rows = cur.fetchall()
    return [
        {
            "id": row[0],
            "ts": row[1].astimezone(tz).isoformat(),
            "hhmm": row[1].astimezone(tz).strftime("%H:%M"),
            "app": row[2] or "Unknown",
            "window_title": row[3] or "",
            "activity": row[4] or "",
            "ocr_excerpt": (row[5] or "")[:240],
        }
        for row in rows
    ]


def _validate_report(
    report: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    event_map = {int(event["id"]): event for event in events}
    citations = CITATION_RE.findall(report)
    cited_ids = [int(citation[0]) for citation in citations]
    invalid_ids = sorted(set(cited_ids) - set(event_map))
    metadata_mismatches: list[dict[str, Any]] = []
    for raw_id, cited_time, cited_app in citations:
        event = event_map.get(int(raw_id))
        if event is None:
            continue
        if cited_time != event["hhmm"] or cited_app != event["app"]:
            metadata_mismatches.append(
                {
                    "event_id": int(raw_id),
                    "cited": {"time": cited_time, "app": cited_app},
                    "expected": {"time": event["hhmm"], "app": event["app"]},
                }
            )
    factual_lines = [
        line.strip()
        for line in report.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and line.strip() != "---"
    ]
    uncited_lines = [line for line in factual_lines if CITATION_RE.search(line) is None]
    return {
        "citation_count": len(citations),
        "cited_event_ids": cited_ids,
        "invalid_event_ids": invalid_ids,
        "metadata_mismatches": metadata_mismatches,
        "factual_line_count": len(factual_lines),
        "uncited_factual_lines": uncited_lines,
        "pass": (
            bool(citations)
            and not invalid_ids
            and not metadata_mismatches
            and bool(factual_lines)
            and not uncited_lines
        ),
    }


def _normalize_citations(report: str) -> tuple[str, bool]:
    """Repair the common `[event12 ...]` omission without changing any id."""

    normalized = re.sub(r"\[event(?!#)(\d+)\s", r"[event#\1 ", report)
    return normalized, normalized != report


def _review_event_projection(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Give Reviewer only the grounded fields it is responsible for checking."""

    return [
        {
            "id": event["id"],
            "hhmm": event["hhmm"],
            "app": event["app"],
            "activity": event["activity"],
        }
        for event in events
    ]


def _router_for_args(args: argparse.Namespace, dsn: str) -> ComputeRouter:
    return ComputeRouter(
        Settings(
            gateway_url=args.radeon_gateway_url,
            radeon_gateway_url=args.radeon_gateway_url,
            local_gateway_url=args.local_gateway_url,
            timeline_db_url=dsn,
            honcho_url="http://127.0.0.1:8100",
            data_root=Path("/tmp/dejaview-p34-data"),
        )
    )


def _compute_failure_message(exc: ComputeFailure | BothBackendsFailed) -> str:
    if isinstance(exc, BothBackendsFailed):
        return "compute paths failed: " + ", ".join(exc.reasons)
    return "compute rejected: " + exc.reason


def _daily_failure_message(exc: ValueError | ComputeFailure | BothBackendsFailed) -> str:
    if isinstance(exc, (ComputeFailure, BothBackendsFailed)):
        return _compute_failure_message(exc)
    return "invalid model output"


def main() -> int:
    args = _parse_args()
    if os.environ.get("DEJAVIEW_DEMO_MODE") != "1":
        raise SystemExit("DEJAVIEW_DEMO_MODE=1 is required")
    dsn = os.environ.get("TIMELINE_DB_URL", DEFAULT_DSN)
    if dsn != DEFAULT_DSN:
        raise SystemExit("TIMELINE_DB_URL must target local database dejaview_demo")
    timezone_name = os.environ.get("TZ", "Asia/Kuching")
    router = _router_for_args(args, dsn)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        if cur.fetchone()[0] != "dejaview_demo":
            raise SystemExit("connected database is not dejaview_demo")
        cur.execute(
            "SELECT count(*) FROM timeline_events WHERE device_id NOT LIKE 'demo-%%'"
        )
        if int(cur.fetchone()[0]) != 0:
            raise SystemExit("demo database contains a non-demo device")

    print("[Planner] fast: designing today's report sections...", flush=True)
    planner_routes: list[dict[str, Any]] = []
    try:
        plan, planner_attempts = _router_json_with_retry(
            router,
            model="fast",
            system=(
                "You are the Planner agent for a private digital-memory daily "
                "report. You are not being asked to know the events yet: the "
                "Retriever supplies them in the next stage. Never refuse. Return "
                "strict JSON only for this synthetic demo."
            ),
            user=(
                f"Plan generic section labels for a concise report dated "
                f"{args.report_date.isoformat()}. Assert no event facts. "
                'Return exactly {"sections":["...","..."],"focus":"..."} '
                "with 2-4 nonempty sections and one nonempty focus."
            ),
            max_tokens=160,
            validate=_validate_plan_response,
            route_metadata=planner_routes,
        )
    except (ValueError, ComputeFailure, BothBackendsFailed) as exc:
        raise SystemExit(
            "Planner failed after real-model retries: " + _daily_failure_message(exc)
        ) from exc
    if planner_attempts > 1:
        print(
            f"[Planner] recovered valid JSON on attempt {planner_attempts}",
            flush=True,
        )
    print(f"[Planner] sections: {', '.join(map(str, plan['sections']))}", flush=True)

    print("[Retriever] timeline: loading demo-p34 events...", flush=True)
    events = _retrieve_events(
        dsn,
        device_id=args.device_id,
        report_date=args.report_date,
        timezone_name=timezone_name,
    )
    if not events:
        raise SystemExit("Retriever found no synthetic demo events")
    print(f"[Retriever] {len(events)} grounded events", flush=True)
    for event in events:
        print(
            f"  event#{event['id']} {event['hhmm']} {event['app']}: "
            f"{event['activity']}",
            flush=True,
        )

    print("[Writer] brain: drafting a cited daily report...", flush=True)
    writer_raw = ""
    validation: dict[str, Any] = {}
    citations_normalized = False
    writer_attempts = 0
    writer_routes: list[dict[str, Any]] = []
    repair: dict[str, Any] | None = None
    for writer_attempts in range(1, MAX_MODEL_ATTEMPTS + 1):
        try:
            writer_raw, writer_route = _router_chat(
                router,
                model="brain",
                system=(
                    "You are the Writer agent. Write concise Markdown using only "
                    "headings and factual bullets from the supplied synthetic events. "
                    "Every non-heading line must end with a citation exactly like "
                    "[event#12 09:30 VS Code]. Never invent an id, time, app, or fact."
                ),
                user=json.dumps(
                    {
                        "date": args.report_date.isoformat(),
                        "plan": plan,
                        "events": events,
                        "repair_previous_validation": repair,
                    },
                    ensure_ascii=False,
                ),
                max_tokens=700,
            )
        except (ComputeFailure, BothBackendsFailed) as exc:
            raise SystemExit("Writer failed: " + _compute_failure_message(exc)) from exc
        writer_routes.append(writer_route)
        writer_raw = writer_raw.strip()
        writer_raw, normalized_this_attempt = _normalize_citations(writer_raw)
        citations_normalized = citations_normalized or normalized_this_attempt
        validation = _validate_report(writer_raw, events)
        if validation["pass"]:
            break
        repair = validation
        if writer_attempts < MAX_MODEL_ATTEMPTS:
            print(
                "[Writer] deterministic citation gate requested one real-model retry",
                flush=True,
            )
    if not validation["pass"]:
        raise SystemExit("Writer citation validation failed after real-model retries")
    print(
        f"[Writer] draft grounded by {validation['citation_count']} citations",
        flush=True,
    )

    print("[Reviewer] fast: checking provenance and scope...", flush=True)
    review_events = _review_event_projection(events)
    reviewer_routes: list[dict[str, Any]] = []
    try:
        review, reviewer_attempts = _router_json_with_retry(
            router,
            model="fast",
            system=(
                "You are the Reviewer agent. Independently review the report "
                "against the supplied synthetic events. A factual bullet is "
                "supported when it faithfully paraphrases an event activity and "
                "its citation matches that event's id, hhmm, and app. The citation "
                "is the timestamp evidence; the activity string does not need to "
                "repeat its id or time. Review only text present in the report, "
                "not omitted source fields. The deterministic validation already "
                "checks citation syntax and metadata. Reject only an unsupported "
                "report fact or a concrete event mismatch. If you reject, name at "
                "least one concrete issue and quote the affected report fact. "
                "Return exactly one JSON object with only `decision` (`pass` or "
                "`reject`) and `issues`."
            ),
            user=json.dumps(
                {
                    "report": writer_raw,
                    "events": review_events,
                    "deterministic_validation": validation,
                },
                ensure_ascii=False,
            ),
            max_tokens=100,
            validate=_validate_review_response,
            route_metadata=reviewer_routes,
        )
    except (ValueError, ComputeFailure, BothBackendsFailed) as exc:
        raise SystemExit(
            "Reviewer failed after real-model retries: " + _daily_failure_message(exc)
        ) from exc
    if reviewer_attempts > 1:
        print(
            f"[Reviewer] resolved contradictory JSON on attempt {reviewer_attempts}",
            flush=True,
        )
    if str(review.get("decision", "")).lower() != "pass" or review.get("issues") != []:
        raise SystemExit("Reviewer rejected report")
    print("[Reviewer] PASS — citations resolve to retrieved event ids", flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.audit_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(writer_raw.rstrip() + "\n", encoding="utf-8")
    args.audit_output.write_text(
        json.dumps(
            {
                "date": args.report_date.isoformat(),
                "device_id": args.device_id,
                "plan": plan,
                "events": events,
                "validation": validation,
                "citations_normalized": citations_normalized,
                "model_attempts": {
                    "planner": planner_attempts,
                    "writer": writer_attempts,
                    "reviewer": reviewer_attempts,
                },
                "review": review,
                "routes": {
                    "planner": planner_routes,
                    "writer": writer_routes,
                    "reviewer": reviewer_routes,
                },
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[Done] report: {args.output.resolve()}", flush=True)
    print(f"[Done] audit:  {args.audit_output.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
