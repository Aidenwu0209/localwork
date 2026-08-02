"""Synthetic contract tests for Radeon-first agent compute routing."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import httpx
import pytest

from agentd.config import Settings
from agentd.router import BothBackendsFailed, ComputeFailure, ComputeRouter


def settings() -> Settings:
    return Settings(
        gateway_url="https://legacy.example/v1",
        radeon_gateway_url="https://radeon.example/v1",
        local_gateway_url="https://local.example/v1",
        timeline_db_url="postgresql://synthetic/dejaview",
        honcho_url="http://honcho.example",
        data_root=Path("/tmp/agentd-router-synthetic"),
    )


class FixtureResponse:
    def __init__(self, status_code: int = 200, body: object | None = None) -> None:
        self.status_code = status_code
        self._body = (
            {"choices": [{"message": {"content": "synthetic answer"}}]}
            if body is None
            else body
        )

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://synthetic.invalid/v1/chat/completions")
            raise httpx.HTTPStatusError("synthetic upstream", request=request, response=httpx.Response(self.status_code, request=request))

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class ScriptedClient:
    def __init__(self, outcomes: dict[str, list[object]]) -> None:
        self._outcomes = defaultdict(list, outcomes)
        self.posts: list[tuple[str, dict[str, object]]] = []

    def __call__(self, *, timeout: object) -> "ScriptedClient":
        return self

    def __enter__(self) -> "ScriptedClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, url: str, *, json: dict[str, object]) -> FixtureResponse:
        backend = "radeon" if "radeon.example" in url else "local"
        self.posts.append((backend, json))
        outcome = self._outcomes[backend].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, FixtureResponse)
        return outcome


def test_remote_success_reports_truthful_primary_route_metadata() -> None:
    client = ScriptedClient({"radeon": [FixtureResponse()], "local": []})
    router = ComputeRouter(settings(), client_factory=client, clock=lambda: 10.0)

    result = router.chat("brain", [{"role": "user", "content": "synthetic"}])

    assert result.content == "synthetic answer"
    assert result.route.as_dict() == {
        "backend": "radeon",
        "physical_model": "brain",
        "logical_model": "brain",
        "degraded": False,
        "reason": "primary_ok",
    }
    assert client.posts == [("radeon", client.posts[0][1])]
    assert client.posts[0][1]["model"] == "brain"


def test_remote_timeout_uses_local_perceive_for_brain() -> None:
    client = ScriptedClient(
        {"radeon": [httpx.ReadTimeout("synthetic timeout")], "local": [FixtureResponse()]}
    )
    router = ComputeRouter(settings(), client_factory=client, clock=lambda: 10.0)

    result = router.chat("brain", [{"role": "user", "content": "synthetic"}])

    assert result.route.as_dict() == {
        "backend": "local_metal",
        "physical_model": "perceive",
        "logical_model": "brain",
        "degraded": True,
        "reason": "remote_timeout",
    }
    assert [backend for backend, _body in client.posts] == ["radeon", "local"]
    assert client.posts[1][1]["model"] == "perceive"


@pytest.mark.parametrize(
    "remote_outcome",
    [
        FixtureResponse(429),
        FixtureResponse(502),
        FixtureResponse(503),
        FixtureResponse(504),
        FixtureResponse(404),
        FixtureResponse(body=ValueError("synthetic invalid json")),
        FixtureResponse(body={"choices": []}),
    ],
)
def test_retryable_or_invalid_remote_product_uses_local(remote_outcome: object) -> None:
    client = ScriptedClient({"radeon": [remote_outcome], "local": [FixtureResponse()]})
    router = ComputeRouter(settings(), client_factory=client, clock=lambda: 10.0)

    result = router.chat("fast", [{"role": "user", "content": "synthetic"}])

    assert result.route.backend == "local_metal"
    assert result.route.degraded is True
    assert client.posts[1][1]["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize("status_code", [400, 401, 403])
def test_nonretryable_remote_status_never_crosses_to_local(status_code: int) -> None:
    client = ScriptedClient({"radeon": [FixtureResponse(status_code)], "local": [FixtureResponse()]})
    router = ComputeRouter(settings(), client_factory=client, clock=lambda: 10.0)

    with pytest.raises(ComputeFailure) as failure:
        router.chat("brain", [{"role": "user", "content": "synthetic"}])

    assert failure.value.reason in {"caller_invalid_request", "authentication_failed", "policy_rejected"}
    assert [backend for backend, _body in client.posts] == ["radeon"]


def test_dual_failure_exposes_only_stable_sanitized_reasons() -> None:
    client = ScriptedClient(
        {
            "radeon": [httpx.ConnectError("https://user:secret@radeon.example")],
            "local": [httpx.ReadTimeout("https://local-secret@example")],
        }
    )
    router = ComputeRouter(settings(), client_factory=client, clock=lambda: 10.0)

    with pytest.raises(BothBackendsFailed) as failure:
        router.chat("brain", [{"role": "user", "content": "synthetic"}])

    assert failure.value.reasons == ("remote_connection_error", "local_timeout")
    assert "secret" not in str(failure.value)
    assert "user" not in str(failure.value)


def test_remote_circuit_is_role_scoped_and_recovers_after_cooldown() -> None:
    now = [0.0]
    client = ScriptedClient(
        {
            "radeon": [
                httpx.ReadTimeout("first"),
                httpx.ReadTimeout("second"),
                FixtureResponse(),
                FixtureResponse(),
            ],
            "local": [FixtureResponse(), FixtureResponse(), FixtureResponse()],
        }
    )
    router = ComputeRouter(
        settings(),
        client_factory=client,
        clock=lambda: now[0],
        circuit_failure_threshold=2,
        circuit_cooldown_seconds=30.0,
    )

    assert router.chat("brain", [{"role": "user", "content": "one"}]).route.reason == "remote_timeout"
    assert router.chat("brain", [{"role": "user", "content": "two"}]).route.reason == "remote_timeout"
    assert router.chat("brain", [{"role": "user", "content": "three"}]).route.reason == "remote_circuit_open"
    assert [backend for backend, _body in client.posts] == [
        "radeon", "local", "radeon", "local", "local"
    ]

    fast = router.chat("fast", [{"role": "user", "content": "role scoped"}])
    assert fast.route.backend == "radeon"

    now[0] = 30.0
    recovered = router.chat("brain", [{"role": "user", "content": "four"}])
    assert recovered.route.as_dict()["backend"] == "radeon"
    assert recovered.route.reason == "primary_ok"
