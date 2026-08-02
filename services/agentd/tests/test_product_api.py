"""Daily-product API contracts: display safety, containment, and fail-closed state."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from agentd.config import Settings
from agentd.router import RouteMetadata, RouteResult
from agentd.server import create_app


def settings(data_root: Path) -> Settings:
    return Settings(
        gateway_url="http://synthetic-legacy/v1",
        radeon_gateway_url="http://synthetic-radeon/v1",
        local_gateway_url="http://synthetic-local/v1",
        timeline_db_url="postgresql://user:secret@synthetic/dejaview",
        honcho_url="http://synthetic-honcho",
        data_root=data_root,
    )


class FakeStore:
    def __init__(self) -> None:
        self.timeline_calls: list[dict[str, Any]] = []
        self.event: dict[str, Any] | None = None

    def database_ready(self) -> bool:
        return True

    def list_timeline(self, **kwargs: Any) -> dict[str, Any]:
        self.timeline_calls.append(kwargs)
        return {
            "items": [
                {
                    "id": 42,
                    "ts": "2026-08-03T09:18:00+08:00",
                    "end_ts": None,
                    "kind": "frame",
                    "app": "VS Code",
                    "activity": "Reviewed a synthetic kernel change",
                    "topics": ["ROCm"],
                    "window_title": "PRIVATE TITLE",
                    "url": "https://private.invalid",
                    "ocr_text": "PRIVATE OCR",
                    "screenshot_path": "/private/screenshot.webp",
                }
            ],
            "next_position": ("2026-08-03T09:18:00+08:00", 42),
        }

    def get_evidence(self, event_id: int) -> dict[str, Any] | None:
        if self.event is None or self.event.get("id") != event_id:
            return None
        return dict(self.event)

    def privacy_summary(self) -> dict[str, Any]:
        return {
            "total": 9,
            "allowed": 6,
            "blocked": 3,
            "categories": {"normal": 6, "private_chat": 3},
            "reasons": {"classified_normal": 6, "sensitive_category": 3},
            "private_pixels": "MUST NOT LEAK",
        }


class FakeResponse:
    def __init__(
        self,
        body: object,
        *,
        status_code: int = 200,
        text: str | None = None,
    ) -> None:
        self._body = body
        self.status_code = status_code
        self.text = text if text is not None else ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://synthetic.invalid")
            raise httpx.HTTPStatusError(
                "PRIVATE UPSTREAM BODY",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )

    def json(self) -> object:
        return self._body


class FakeLocalClient:
    def __init__(
        self,
        *,
        offline: bool = False,
        metrics_text: str = "dejaview_capture_last_heartbeat_unixtime 0.0\n",
        extra_profile_field: bool = False,
    ) -> None:
        self.offline = offline
        self.metrics_text = metrics_text
        self.extra_profile_field = extra_profile_field
        self.posts: list[tuple[str, object]] = []

    def __call__(self, **_kwargs: object) -> "FakeLocalClient":
        return self

    def __enter__(self) -> "FakeLocalClient":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, url: str) -> FakeResponse:
        if self.offline:
            raise httpx.ConnectError("PRIVATE LOCAL URL")
        if url.endswith("/health"):
            return FakeResponse({"status": "ok", "accepting_frames": True})
        if url.endswith("/metrics"):
            return FakeResponse({}, text=self.metrics_text)
        if url.endswith("/v1/profile/status"):
            body = {
                "enabled": True,
                "paused": False,
                "pending": 2,
                "failed": 1,
                "last_success": "2026-08-03T08:00:00+00:00",
                "covered_session_start": "dejaview-2026-08-02",
                "covered_session_end": "dejaview-2026-08-03",
            }
            if self.extra_profile_field:
                body["payload"] = "PRIVATE OCR"
            return FakeResponse(body)
        raise AssertionError(url)

    def post(self, url: str, *, json: object | None = None) -> FakeResponse:
        if self.offline:
            raise httpx.ConnectError("PRIVATE LOCAL URL")
        self.posts.append((url, json))
        if url.endswith("/v1/profile/pause"):
            body = {"enabled": False, "paused": True}
            if self.extra_profile_field:
                body["payload"] = "PRIVATE"
            return FakeResponse(body)
        if url.endswith("/v1/profile/resume"):
            body = {"enabled": True, "paused": False}
            if self.extra_profile_field:
                body["payload"] = "PRIVATE"
            return FakeResponse(body)
        if url.endswith("/v3/workspaces/dejaview/peers/owner/chat"):
            return FakeResponse({"content": "A local synthetic profile answer."})
        raise AssertionError(url)


class ScriptedRouter:
    def __init__(self) -> None:
        self.responses = iter(
            [
                RouteResult(
                    content="",
                    message={
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "search_timeline",
                                    "arguments": '{"query":"synthetic"}',
                                },
                            }
                        ],
                    },
                    route=RouteMetadata(
                        backend="radeon",
                        physical_model="brain",
                        logical_model="brain",
                        degraded=False,
                        reason="primary_ok",
                        latency_ms=4,
                    ),
                ),
                RouteResult(
                    content="Reviewed it [event#42 09:18 VS Code]",
                    message={"content": "Reviewed it [event#42 09:18 VS Code]"},
                    route=RouteMetadata(
                        backend="local_metal",
                        physical_model="perceive",
                        logical_model="brain",
                        degraded=True,
                        reason="remote_timeout",
                        latency_ms=12,
                    ),
                ),
            ]
        )

    def chat(self, *_args: object, **_kwargs: object) -> RouteResult:
        return next(self.responses)


def client(
    tmp_path: Path,
    *,
    store: FakeStore | None = None,
    local_client: FakeLocalClient | None = None,
    router: object | None = None,
    product_clock=None,
) -> TestClient:
    store = store or FakeStore()
    local_client = local_client or FakeLocalClient()
    app = create_app(
        settings=settings(tmp_path),
        router=router,  # type: ignore[arg-type]
        product_store=store,
        product_client_factory=local_client,
        product_clock=product_clock
        or (lambda: datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)),
    )
    return TestClient(app)


def mutation_headers(app: TestClient, *, origin: str = "http://testserver") -> dict[str, str]:
    session = app.get("/api/session")
    assert session.status_code == 200
    return {
        "origin": origin,
        "x-dejaview-csrf": session.json()["csrf_token"],
    }


def capability_from_timeline(app: TestClient) -> str:
    item = app.get("/api/timeline?limit=1").json()["items"][0]
    return parse_qs(urlsplit(item["evidence"]["url"]).query)["cap"][0]


def test_status_is_unknown_without_capture_heartbeat_and_exposes_no_local_coordinates(
    tmp_path: Path,
) -> None:
    response = client(tmp_path).get("/api/status")

    assert response.status_code == 200
    body = response.json()
    assert body["overall"] == "unknown"
    assert body["capture"]["state"] == "unknown"
    assert body["compute"]["state"] == "unknown"
    assert body["data_sovereignty"] == "local_only"
    for forbidden in ("data_root", "postgresql", "synthetic-honcho", str(tmp_path)):
        assert forbidden not in response.text


def test_status_fails_closed_when_local_components_are_offline(tmp_path: Path) -> None:
    response = client(tmp_path, local_client=FakeLocalClient(offline=True)).get(
        "/api/status"
    )

    assert response.status_code == 200
    assert response.json()["overall"] == "offline"
    assert response.json()["components"]["memoryd"]["state"] == "offline"
    assert "PRIVATE" not in response.text


def test_status_distinguishes_stale_and_future_capture_heartbeat(tmp_path: Path) -> None:
    stale = FakeLocalClient(
        metrics_text="dejaview_capture_last_heartbeat_unixtime 1785749400.0\n"
    )
    stale_response = client(tmp_path, local_client=stale).get("/api/status")
    assert stale_response.json()["capture"]["state"] == "stale"
    assert stale_response.json()["overall"] == "degraded"

    future = FakeLocalClient(
        metrics_text="dejaview_capture_last_heartbeat_unixtime 1785753000.0\n"
    )
    future_response = client(tmp_path, local_client=future).get("/api/status")
    assert future_response.json()["capture"]["state"] == "unknown"
    assert future_response.json()["overall"] == "unknown"


def test_timeline_is_bounded_cursor_paginated_and_display_safe(tmp_path: Path) -> None:
    store = FakeStore()
    app = client(tmp_path, store=store)
    response = app.get(
        "/api/timeline?limit=2&app=VS%20Code&date_from=2026-08-01"
        "&date_to=2026-08-03&query=kernel"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [
        {
            "id": 42,
            "ts": "2026-08-03T09:18:00+08:00",
            "end_ts": None,
            "kind": "frame",
            "app": "VS Code",
            "activity": "Reviewed a synthetic kernel change",
            "topics": ["ROCm"],
            "evidence": {
                "available": True,
                "url": body["items"][0]["evidence"]["url"],
            },
        }
    ]
    evidence_url = body["items"][0]["evidence"]["url"]
    assert evidence_url.startswith("/api/evidence/42?cap=")
    assert isinstance(body["next_cursor"], str) and body["next_cursor"]
    assert "PRIVATE" not in response.text
    assert "screenshot_path" not in response.text

    second = app.get(
        f"/api/timeline?limit=2&app=VS%20Code&date_from=2026-08-01"
        f"&date_to=2026-08-03&query=kernel&cursor={body['next_cursor']}"
    )
    assert second.status_code == 200
    assert store.timeline_calls[-1]["position"] == (
        "2026-08-03T09:18:00+08:00",
        42,
    )

    wrong_filters = app.get(f"/api/timeline?limit=2&cursor={body['next_cursor']}")
    assert wrong_filters.status_code == 422
    assert wrong_filters.json() == {
        "detail": {"code": "invalid_timeline_query"}
    }


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=51", "cursor=not-a-cursor", "device_id=private-device"],
)
def test_timeline_rejects_unbounded_or_unallowlisted_filters(
    tmp_path: Path, query: str
) -> None:
    response = client(tmp_path).get(f"/api/timeline?{query}")
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_timeline_query"}}


def test_ask_returns_structured_citations_and_actual_backend(tmp_path: Path) -> None:
    tool_result = {
        "hits": [
            {
                "id": 42,
                "ts": "2026-08-03T09:18:00+08:00",
                "app": "VS Code",
            }
        ]
    }
    with patch("agentd.server.dispatch", return_value=tool_result):
        app = client(tmp_path, router=ScriptedRouter())
        response = app.post(
            "/api/ask",
            json={"question": "What did I review?"},
            headers=mutation_headers(app),
        )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "answer": "Reviewed it [event#42 09:18 VS Code]",
        "citations": [
            {
                "event_id": 42,
                "label": "09:18 VS Code",
                "evidence_url": body["citations"][0]["evidence_url"],
            }
        ],
        "compute": {
            "backend": "local_metal",
            "physical_model": "perceive",
            "logical_model": "brain",
            "degraded": True,
            "reason": "remote_timeout",
            "latency_ms": 12,
        },
        "evidence_insufficient": False,
    }
    assert body["citations"][0]["evidence_url"].startswith(
        "/api/evidence/42?cap="
    )


def test_evidence_metadata_and_image_never_expose_paths_or_ocr(
    tmp_path: Path,
) -> None:
    image = tmp_path / "screenshots" / "2026" / "08" / "event-42.webp"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"synthetic-image")
    store = FakeStore()
    store.event = {
        "id": 42,
        "ts": "2026-08-03T09:18:00+08:00",
        "app": "VS Code",
        "activity": "Reviewed a synthetic kernel change",
        "topics": ["ROCm"],
        "ocr_blocks": [{"text": "PRIVATE OCR", "bbox": [1, 2, 3, 4], "conf": 0.9}],
        "ocr_text": "PRIVATE OCR",
        "screenshot_path": str(image),
    }
    app = client(tmp_path, store=store)
    capability = capability_from_timeline(app)

    metadata = app.get(f"/api/evidence/42?cap={capability}")
    assert metadata.status_code == 200
    assert metadata.json() == {
        "event_id": 42,
        "ts": "2026-08-03T09:18:00+08:00",
        "app": "VS Code",
        "activity": "Reviewed a synthetic kernel change",
        "topics": ["ROCm"],
        "highlights": [{"bbox": [1, 2, 3, 4]}],
        "image": {
            "available": True,
            "url": f"/api/evidence/42/image?cap={capability}",
        },
    }
    assert "PRIVATE" not in metadata.text
    assert str(tmp_path) not in metadata.text
    image_response = app.get(f"/api/evidence/42/image?cap={capability}")
    assert image_response.status_code == 200
    assert image_response.content == b"synthetic-image"
    assert image_response.headers["cache-control"] == "private, no-store"
    assert image_response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("unsafe_kind", ["outside", "traversal", "symlink"])
def test_evidence_image_denies_outside_traversal_and_symlink_paths(
    tmp_path: Path, unsafe_kind: str
) -> None:
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    outside = tmp_path / "outside.webp"
    outside.write_bytes(b"not-authorized")
    if unsafe_kind == "outside":
        stored = outside
    elif unsafe_kind == "traversal":
        stored = screenshots / ".." / "outside.webp"
    else:
        stored = screenshots / "linked.webp"
        stored.symlink_to(outside)
    store = FakeStore()
    store.event = {
        "id": 42,
        "ts": "2026-08-03T09:18:00+08:00",
        "app": "VS Code",
        "activity": "Synthetic",
        "topics": [],
        "ocr_blocks": [],
        "screenshot_path": str(stored),
    }

    app = client(tmp_path, store=store)
    capability = capability_from_timeline(app)
    response = app.get(f"/api/evidence/42/image?cap={capability}")

    assert response.status_code == 404
    assert response.json() == {
        "detail": {"code": "evidence_image_unavailable"}
    }
    assert "outside" not in response.text


def test_blocked_or_missing_event_has_no_evidence_authority(tmp_path: Path) -> None:
    response = client(tmp_path).get("/api/evidence/999")
    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "evidence_not_found"}}


def test_evidence_capability_rejects_tampering_and_wrong_event(tmp_path: Path) -> None:
    store = FakeStore()
    store.event = {
        "id": 42,
        "ts": "2026-08-03T09:18:00+08:00",
        "app": "VS Code",
        "activity": "Synthetic",
        "topics": [],
        "ocr_blocks": [],
        "screenshot_path": None,
    }
    app = client(tmp_path, store=store)
    capability = capability_from_timeline(app)

    tampered = capability[:-1] + ("A" if capability[-1] != "A" else "B")
    for url in (
        f"/api/evidence/42?cap={tampered}",
        f"/api/evidence/43?cap={capability}",
        "/api/evidence/42",
    ):
        response = app.get(url)
        assert response.status_code == 404
        assert response.json() == {"detail": {"code": "evidence_not_found"}}


def test_evidence_capability_expires(tmp_path: Path) -> None:
    now = [datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)]
    store = FakeStore()
    store.event = {
        "id": 42,
        "ts": "2026-08-03T09:18:00+08:00",
        "app": "VS Code",
        "activity": "Synthetic",
        "topics": [],
        "ocr_blocks": [],
        "screenshot_path": None,
    }
    app = client(tmp_path, store=store, product_clock=lambda: now[0])
    capability = capability_from_timeline(app)
    now[0] = datetime(2026, 8, 3, 10, 6, tzinfo=timezone.utc)

    response = app.get(f"/api/evidence/42?cap={capability}")
    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "evidence_not_found"}}


def test_privacy_summary_contains_counts_only(tmp_path: Path) -> None:
    response = client(tmp_path).get("/api/privacy/summary")
    assert response.status_code == 200
    assert response.json() == {
        "total": 9,
        "allowed": 6,
        "blocked": 3,
        "categories": {"normal": 6, "private_chat": 3},
        "reasons": {"classified_normal": 6, "sensitive_category": 3},
        "blocked_pixels_exposed": 0,
    }
    assert "PRIVATE" not in response.text


def test_profile_status_and_controls_are_whitelisted_and_require_confirmation(
    tmp_path: Path,
) -> None:
    local = FakeLocalClient()
    app = client(tmp_path, local_client=local)
    headers = mutation_headers(app)

    status = app.get("/api/profile/status")
    assert status.status_code == 200
    assert status.json() == {
        "enabled": True,
        "paused": False,
        "pending": 2,
        "failed": 1,
        "last_success": "2026-08-03T08:00:00+00:00",
        "covered_session_start": "dejaview-2026-08-02",
        "covered_session_end": "dejaview-2026-08-03",
    }
    assert "PRIVATE" not in status.text

    rejected = app.post(
        "/api/profile/pause", json={"confirm": False}, headers=headers
    )
    assert rejected.status_code == 422
    assert rejected.json() == {"detail": {"code": "confirmation_required"}}
    assert local.posts == []
    paused = app.post("/api/profile/pause", json={"confirm": True}, headers=headers)
    assert paused.json() == {"enabled": False, "paused": True}
    resumed = app.post("/api/profile/resume", json={"confirm": True}, headers=headers)
    assert resumed.json() == {"enabled": True, "paused": False}


def test_profile_query_returns_local_answer_without_echoing_question(tmp_path: Path) -> None:
    app = client(tmp_path)
    response = app.post(
        "/api/profile/query",
        json={"question": "PRIVATE SYNTHETIC QUESTION"},
        headers=mutation_headers(app),
    )

    assert response.status_code == 200
    assert response.json() == {
        "answer": "A local synthetic profile answer.",
        "provenance": {
            "source": "honcho_local_projection",
            "workspace": "dejaview",
            "peer": "owner",
        },
    }
    assert "PRIVATE SYNTHETIC QUESTION" not in response.text


@pytest.mark.parametrize(
    ("headers", "content_type"),
    [
        ({}, "application/json"),
        ({"origin": "https://attacker.invalid", "x-dejaview-csrf": "wrong"}, "application/json"),
        ({"origin": "http://testserver", "x-dejaview-csrf": "wrong"}, "application/json"),
        ({"origin": "http://testserver", "x-dejaview-csrf": "wrong"}, "application/x-www-form-urlencoded"),
    ],
)
def test_mutations_reject_cross_site_form_and_missing_csrf_without_upstream_calls(
    tmp_path: Path, headers: dict[str, str], content_type: str
) -> None:
    local = FakeLocalClient()
    app = client(tmp_path, local_client=local)
    response = app.post(
        "/api/profile/pause",
        content='{"confirm":true}',
        headers={**headers, "content-type": content_type},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "same_origin_required"}}
    assert local.posts == []


def test_profile_proxy_errors_are_stable_and_sanitized(tmp_path: Path) -> None:
    response = client(tmp_path, local_client=FakeLocalClient(offline=True)).get(
        "/api/profile/status"
    )
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "profile_unavailable"}}
    assert "PRIVATE" not in response.text


def test_profile_proxy_rejects_extra_upstream_fields(tmp_path: Path) -> None:
    response = client(
        tmp_path, local_client=FakeLocalClient(extra_profile_field=True)
    ).get("/api/profile/status")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "profile_unavailable"}}
    assert "PRIVATE" not in response.text
