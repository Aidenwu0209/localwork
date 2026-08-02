"""Question routing, evidence allowlist, and degraded-response contracts."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agentd.config import Settings
from agentd.router import BothBackendsFailed, RouteMetadata, RouteResult
from agentd.server import create_app


def settings() -> Settings:
    return Settings(
        gateway_url="http://synthetic-legacy/v1",
        radeon_gateway_url="http://synthetic-radeon/v1",
        local_gateway_url="http://synthetic-local/v1",
        timeline_db_url="postgresql://synthetic/dejaview",
        honcho_url="http://synthetic-honcho",
        data_root=Path("/tmp/agentd-chat-synthetic"),
    )


def route(*, backend: str = "radeon", reason: str = "primary_ok") -> RouteMetadata:
    return RouteMetadata(
        backend=backend,
        physical_model="brain" if backend == "radeon" else "perceive",
        logical_model="brain",
        degraded=backend == "local_metal",
        reason=reason,
        latency_ms=12,
    )


def result(message: dict, *, backend: str = "radeon", reason: str = "primary_ok") -> RouteResult:
    return RouteResult(content=message.get("content") or "", message=message, route=route(backend=backend, reason=reason))


class ScriptedRouter:
    def __init__(self, responses: list[RouteResult | Exception]) -> None:
        self._responses = iter(responses)
        self.calls: list[dict] = []

    def chat(self, logical_model: str, messages: list[dict], **kwargs: object) -> RouteResult:
        self.calls.append({"logical_model": logical_model, "messages": messages, **kwargs})
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


TOOL_CALL = {
    "content": None,
    "tool_calls": [
        {
            "id": "synthetic-tool",
            "function": {"name": "search_timeline", "arguments": '{"query":"synthetic"}'},
        }
    ],
}
TOOL_RESULT = {
    "hits": [
        {
            "id": 42,
            "ts": "2026-08-03T09:18:00+08:00",
            "app": "Terminal",
        }
    ]
}


def post_chat(router: ScriptedRouter):
    app = create_app(settings=settings(), router=router)
    return TestClient(app).post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "synthetic question"}]},
    )


def test_tool_loop_returns_actual_final_route_and_allowlisted_citation() -> None:
    router = ScriptedRouter(
        [
            result(TOOL_CALL),
            result(
                {"content": "Synthetic answer [event#42 09:18 Terminal]"},
                backend="local_metal",
                reason="remote_timeout",
            ),
        ]
    )
    with patch("agentd.server.dispatch", return_value=TOOL_RESULT):
        response = post_chat(router)

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "Synthetic answer [event#42 09:18 Terminal]"
    assert body["dejaview"] == {
        "backend": "local_metal",
        "physical_model": "perceive",
        "logical_model": "brain",
        "degraded": True,
        "reason": "remote_timeout",
        "latency_ms": 12,
        "citations": [{"event_id": 42, "label": "09:18 Terminal"}],
    }
    assert len(router.calls) == 2
    assert all(call["tools"] is not None for call in router.calls)


def test_invalid_citation_label_gets_one_router_correction_attempt() -> None:
    router = ScriptedRouter(
        [
            result(TOOL_CALL),
            result({"content": "Wrong label [event#42 09:18 Invented]"}),
            result({"content": "Corrected [event#42 09:18 Terminal]"}),
        ]
    )
    with patch("agentd.server.dispatch", return_value=TOOL_RESULT):
        response = post_chat(router)

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Corrected [event#42 09:18 Terminal]"
    assert len(router.calls) == 3
    assert "citation" in str(router.calls[-1]["messages"][-1]["content"]).lower()


def test_second_invalid_product_returns_safe_uncited_answer() -> None:
    router = ScriptedRouter(
        [
            result(TOOL_CALL),
            result({"content": "Wrong id [event#99 09:18 Terminal]"}),
            result({"content": "Still wrong [event#99 09:18 Terminal]"}),
        ]
    )
    with patch("agentd.server.dispatch", return_value=TOOL_RESULT):
        response = post_chat(router)

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "I don't have sufficient verified evidence to answer that safely."
    assert body["dejaview"]["citations"] == []
    assert len(router.calls) == 3


def test_correction_that_returns_a_tool_call_returns_safe_uncited_answer() -> None:
    router = ScriptedRouter(
        [
            result(TOOL_CALL),
            result({"content": "Wrong id [event#99 09:18 Terminal]"}),
            result(TOOL_CALL, backend="local_metal", reason="remote_timeout"),
        ]
    )
    with patch("agentd.server.dispatch", return_value=TOOL_RESULT):
        response = post_chat(router)

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == (
        "I don't have sufficient verified evidence to answer that safely."
    )
    assert body["dejaview"]["citations"] == []
    assert len(router.calls) == 3


def test_tool_dispatch_receives_the_same_router_used_for_brain_calls() -> None:
    router = ScriptedRouter(
        [
            result(TOOL_CALL),
            result({"content": "Synthetic answer [event#42 09:18 Terminal]"}),
        ]
    )
    with patch("agentd.server.dispatch", return_value=TOOL_RESULT) as dispatch:
        response = post_chat(router)

    assert response.status_code == 200
    assert dispatch.call_args.kwargs["router"] is router


def test_tool_logs_never_include_arguments_or_result_content(caplog) -> None:
    secret = "SYNTHETIC-PRIVATE-OCR-TITLE-PATH"
    tool_call = {
        "content": None,
        "tool_calls": [
            {
                "id": "synthetic-tool",
                "function": {
                    "name": "search_timeline",
                    "arguments": '{"query":"' + secret + '"}',
                },
            }
        ],
    }
    router = ScriptedRouter([result(tool_call), result({"content": "No memory claim."})])
    with caplog.at_level(logging.INFO, logger="agentd.server"), patch(
        "agentd.server.dispatch", return_value={"answer": secret}
    ):
        response = post_chat(router)

    assert response.status_code == 200
    assert secret not in caplog.text
    assert "tool=search_timeline" in caplog.text


def test_unknown_model_tool_name_is_sanitized_before_logging(caplog) -> None:
    secret = "SYNTHETIC-PRIVATE-OCR-TITLE-PATH"
    tool_call = {
        "content": None,
        "tool_calls": [
            {
                "id": "private-call-id",
                "function": {"name": secret, "arguments": "{}"},
            }
        ],
    }
    router = ScriptedRouter([result(tool_call), result({"content": "No memory claim."})])

    with caplog.at_level(logging.INFO, logger="agentd.server"):
        response = post_chat(router)

    assert response.status_code == 200
    assert secret not in caplog.text
    assert "private-call-id" not in caplog.text
    assert "tool=unknown_tool" in caplog.text


def test_dual_compute_failure_returns_only_sanitized_reasons() -> None:
    router = ScriptedRouter([BothBackendsFailed("remote_timeout", "local_timeout")])

    response = post_chat(router)

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "compute_unavailable",
            "reasons": ["remote_timeout", "local_timeout"],
        }
    }
