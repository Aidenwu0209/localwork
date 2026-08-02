"""Radeon-first OpenAI-compatible compute routing for agentd."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from agentd.config import Settings


_FAST_TRACK_ROLES = frozenset({"fast"})


@dataclass(frozen=True)
class RouteMetadata:
    backend: str
    physical_model: str
    logical_model: str
    degraded: bool
    reason: str
    latency_ms: int

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "backend": self.backend,
            "physical_model": self.physical_model,
            "logical_model": self.logical_model,
            "degraded": self.degraded,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RouteResult:
    content: str
    message: dict[str, Any]
    route: RouteMetadata


class ComputeFailure(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class BothBackendsFailed(RuntimeError):
    def __init__(self, remote_reason: str, local_reason: str) -> None:
        self.reasons = (remote_reason, local_reason)
        super().__init__(f"compute failed: {remote_reason}, {local_reason}")


@dataclass
class _CircuitState:
    failures: int = 0
    open_until: float = 0.0


@dataclass(frozen=True)
class _BackendFailure(Exception):
    reason: str
    retryable: bool


class ComputeRouter:
    """Route one verified inference request to Radeon, then Local Metal."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[..., Any] = httpx.Client,
        clock: Callable[[], float] = time.monotonic,
        circuit_failure_threshold: int = 2,
        circuit_cooldown_seconds: float = 30.0,
        timeout_seconds: float = 300.0,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._clock = clock
        self._threshold = circuit_failure_threshold
        self._cooldown = circuit_cooldown_seconds
        self._timeout = timeout_seconds
        self._circuits: dict[str, _CircuitState] = {}

    def chat(
        self,
        logical_model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> RouteResult:
        now = self._clock()
        circuit = self._circuits.setdefault(logical_model, _CircuitState())
        if circuit.open_until > now:
            return self._local_or_raise(
                logical_model,
                messages,
                "remote_circuit_open",
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )

        try:
            result = self._call_backend(
                backend="radeon",
                gateway_url=self._settings.compute_radeon_gateway_url,
                logical_model=logical_model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
        except _BackendFailure as failure:
            if not failure.retryable:
                raise ComputeFailure(failure.reason) from None
            circuit.failures += 1
            if circuit.failures >= self._threshold:
                circuit.open_until = now + self._cooldown
            return self._local_or_raise(
                logical_model,
                messages,
                failure.reason,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )

        circuit.failures = 0
        circuit.open_until = 0.0
        return result

    def _local_or_raise(
        self,
        logical_model: str,
        messages: list[dict[str, Any]],
        remote_reason: str,
        **kwargs: Any,
    ) -> RouteResult:
        try:
            return self._call_backend(
                backend="local_metal",
                gateway_url=self._settings.local_gateway_url,
                logical_model=logical_model,
                messages=messages,
                fallback_reason=remote_reason,
                **kwargs,
            )
        except _BackendFailure as failure:
            raise BothBackendsFailed(remote_reason, failure.reason) from None

    def _call_backend(
        self,
        *,
        backend: str,
        gateway_url: str,
        logical_model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float | None,
        max_tokens: int | None,
        response_format: dict[str, Any] | None,
        fallback_reason: str | None = None,
    ) -> RouteResult:
        physical_model = "perceive" if backend == "local_metal" and logical_model == "brain" else logical_model
        body: dict[str, Any] = {"model": physical_model, "messages": messages}
        if tools is not None:
            body.update({"tools": tools, "tool_choice": "auto"})
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if response_format is not None:
            body["response_format"] = response_format
        if logical_model in _FAST_TRACK_ROLES:
            body["chat_template_kwargs"] = {"enable_thinking": False}

        started = self._clock()
        base = gateway_url.rstrip("/").removesuffix("/v1")
        try:
            with self._client_factory(timeout=httpx.Timeout(self._timeout, connect=10.0)) as client:
                response = client.post(f"{base}/v1/chat/completions", json=body)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _BackendFailure(*_status_failure(backend, exc.response.status_code)) from None
        except httpx.ReadTimeout:
            raise _BackendFailure(f"{_reason_prefix(backend)}_timeout", True) from None
        except httpx.ConnectError:
            raise _BackendFailure(f"{_reason_prefix(backend)}_connection_error", True) from None
        except httpx.TimeoutException:
            raise _BackendFailure(f"{_reason_prefix(backend)}_timeout", True) from None
        except httpx.HTTPError:
            raise _BackendFailure(f"{_reason_prefix(backend)}_transport_error", True) from None

        try:
            product = response.json()
        except (TypeError, ValueError):
            raise _BackendFailure(f"{_reason_prefix(backend)}_invalid_json", True) from None
        message, content = _validated_message(product, backend)
        latency_ms = max(0, int((self._clock() - started) * 1000))
        return RouteResult(
            content=content,
            message=message,
            route=RouteMetadata(
                backend=backend,
                physical_model=physical_model,
                logical_model=logical_model,
                degraded=backend == "local_metal",
                reason=fallback_reason or "primary_ok",
                latency_ms=latency_ms,
            ),
        )


def _reason_prefix(backend: str) -> str:
    return "remote" if backend == "radeon" else "local"


def _status_failure(backend: str, status_code: int) -> tuple[str, bool]:
    prefix = _reason_prefix(backend)
    if status_code == 400:
        return "caller_invalid_request", False
    if status_code == 401:
        return "authentication_failed", False
    if status_code in {403, 451}:
        return "policy_rejected", False
    if status_code == 404:
        return f"{prefix}_missing_model", True
    if status_code == 429:
        return f"{prefix}_rate_limited", True
    if status_code in {502, 503, 504}:
        return f"{prefix}_http_{status_code}", True
    return f"{prefix}_http_error", True


def _validated_message(product: object, backend: str) -> tuple[dict[str, Any], str]:
    try:
        choices = product["choices"]  # type: ignore[index]
        choice = choices[0]
        message = choice["message"]
    except (IndexError, KeyError, TypeError):
        raise _BackendFailure(f"{_reason_prefix(backend)}_invalid_response_shape", True) from None
    if not isinstance(message, dict):
        raise _BackendFailure(f"{_reason_prefix(backend)}_invalid_response_shape", True)
    content = message.get("content")
    tool_calls = message.get("tool_calls")
    if content is not None and not isinstance(content, str):
        raise _BackendFailure(f"{_reason_prefix(backend)}_invalid_response_shape", True)
    if tool_calls is not None and not isinstance(tool_calls, list):
        raise _BackendFailure(f"{_reason_prefix(backend)}_invalid_response_shape", True)
    if not isinstance(content, str) and not tool_calls:
        raise _BackendFailure(f"{_reason_prefix(backend)}_invalid_response_shape", True)
    return message, content or ""
