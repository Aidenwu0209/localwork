"""agentd OpenAI-compatible出口 (handbook §6.5).

Exposes `/v1/chat/completions` (model=`dejaview`) that Open WebUI plugs into
directly. The request is forwarded to the brain (logical name `brain` at the
gateway) with agentd's four tools attached; we run the tool-calling loop
locally (call brain -> execute any tool_calls -> feed results back -> repeat)
until the brain returns a final answer. The system prompt enforces the answer
discipline: every memory reference must carry a `[event#id HH:MM app]` citation
that the UI renders as a clickable screenshot link.

Non-streaming first (M7.2 acceptance is one end-to-end answer with citations);
streaming is a Phase 2 polish.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agentd.config import Settings
from agentd.product import ProductStoreProtocol, install_product_routes
from agentd.router import BothBackendsFailed, ComputeFailure, ComputeRouter, RouteMetadata
from agentd.tools import SPECS, dispatch

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are DejaView, the user's personal digital memory. You answer questions about the user's captured screen activity, their working habits and preferences, and documents they've imported.

You have four tools:
- search_timeline: search captured activity (semantic for concepts, exact for error codes/PR numbers/URLs, hybrid default). Always bound a fuzzy question with a time range if the user mentioned one.
- query_user_model: ask the Honcho user-psychology model about preferences, habits, working style. Use this for "based on what you know about me" questions — NOT for factual event lookups.
- search_kb: search imported documents/repositories.
- fetch_screenshot: pull screenshot evidence for a specific event id, optionally highlighting text.

ANSWER DISCIPLINE (mandatory):
- Every claim that references a captured memory MUST carry an inline citation in exactly this form: [event#<id> <HH:MM> <app>]. Example: "You were debugging a ROCM-4042 error [event#142 14:32 Terminal]."
- Only cite events the tools actually returned. Never invent ids or timestamps.
- If no tool result is relevant, say so plainly — do not fabricate.
- Prefer calling fetch_screenshot on the top cited event so the user gets visual evidence.

Be concise. Use the tools; do not guess."""

MAX_TOOL_ROUNDS = 6  # cap the loop so a confused brain can't spin forever
_EVENT_MARKER = re.compile(r"\[event#[^\]]*\]")
_CITATION_MARKER = re.compile(
    r"\[event#(?P<event_id>\d+) (?P<hhmm>[0-2]\d:[0-5]\d) (?P<app>[^\]]+)\]"
)
_EVIDENCE_INSUFFICIENT = "I don't have sufficient verified evidence to answer that safely."


def _safe_url_origin(value: str) -> str:
    """Return only scheme/host/port, never URL credentials or query data."""
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        return "invalid"
    try:
        port = parsed.port
    except ValueError:
        return "invalid"
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port_suffix = f":{port}" if port is not None else ""
    return f"{parsed.scheme.lower()}://{host}{port_suffix}"


class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ChatRequest(BaseModel):
    model: str = "dejaview"
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    # OpenAI clients send stream=true by default; we acknowledge but answer
    # non-streaming (a single JSON response). Open WebUI handles both.
    stream: bool | None = None


def create_app(
    *,
    settings: Settings | None = None,
    router: ComputeRouter | None = None,
    product_store: ProductStoreProtocol | None = None,
    product_client_factory: Callable[..., Any] = httpx.Client,
    product_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> FastAPI:
    settings = settings or Settings.from_env()
    router = router or ComputeRouter(settings)
    app = FastAPI(title="DejaView agentd", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "agentd",
            "model": settings.model_name,
            "brain_model": settings.brain_model,
            "gateway_origin": _safe_url_origin(settings.gateway_url),
            "radeon_gateway_origin": _safe_url_origin(
                settings.compute_radeon_gateway_url
            ),
            "local_gateway_origin": _safe_url_origin(settings.local_gateway_url),
            "honcho_origin": _safe_url_origin(settings.honcho_url),
            "database": urlsplit(settings.timeline_db_url).path.removeprefix("/"),
            "data_root": str(settings.data_root),
        }

    @app.get("/v1/models")
    async def list_models() -> dict:
        return {
            "object": "list",
            "data": [
                {"id": settings.model_name, "object": "model", "owned_by": "dejaview"}
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat(req: ChatRequest) -> JSONResponse:
        if req.stream:
            # Acknowledge but fall back to non-streaming (Phase 2 will add SSE).
            pass

        # Build the conversation: system prompt + the user's messages.
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in req.messages:
            messages.append(m.model_dump(exclude_none=True))

        round_idx = 0
        citation_allowlist: dict[int, str] = {}
        last_route: RouteMetadata | None = None
        while round_idx < MAX_TOOL_ROUNDS:
            round_idx += 1
            try:
                product = router.chat(
                    settings.brain_model,
                    messages,
                    tools=SPECS,
                    temperature=req.temperature,
                    max_tokens=req.max_tokens,
                )
            except BothBackendsFailed as exc:
                raise HTTPException(
                    503,
                    detail={"code": "compute_unavailable", "reasons": list(exc.reasons)},
                ) from exc
            except ComputeFailure as exc:
                raise HTTPException(
                    502, detail={"code": "compute_rejected", "reason": exc.reason}
                ) from exc

            last_route = product.route
            msg = product.message
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                citations = _validated_citations(product.content, citation_allowlist)
                if citations is not None:
                    return _chat_response(
                        settings,
                        product.content,
                        finish_reason="stop",
                        route=product.route,
                        citations=citations,
                    )
                correction_messages = [
                    *messages,
                    {"role": "assistant", "content": product.content},
                    {
                        "role": "user",
                        "content": (
                            "Your previous answer had citations that were not returned by "
                            "this request's tools. Return a corrected answer using only "
                            "the exact allowed [event#id HH:MM app] citations, or omit "
                            "memory claims."
                        ),
                    },
                ]
                try:
                    corrected = router.chat(
                        settings.brain_model,
                        correction_messages,
                        tools=SPECS,
                        temperature=req.temperature,
                        max_tokens=req.max_tokens,
                    )
                except BothBackendsFailed as exc:
                    raise HTTPException(
                        503,
                        detail={"code": "compute_unavailable", "reasons": list(exc.reasons)},
                    ) from exc
                except ComputeFailure as exc:
                    raise HTTPException(
                        502,
                        detail={"code": "compute_rejected", "reason": exc.reason},
                    ) from exc
                corrected_citations = _validated_citations(
                    corrected.content, citation_allowlist
                )
                if (
                    not corrected.message.get("tool_calls")
                    and corrected.content.strip()
                    and corrected_citations is not None
                ):
                    return _chat_response(
                        settings,
                        corrected.content,
                        finish_reason="stop",
                        route=corrected.route,
                        citations=corrected_citations,
                    )
                return _chat_response(
                    settings,
                    _EVIDENCE_INSUFFICIENT,
                    finish_reason="stop",
                    route=corrected.route,
                    citations=[],
                )

            # Append the assistant message (with tool_calls) to the conversation,
            # then execute each tool call and append a tool result message.
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.get("content"),
                    "tool_calls": tool_calls,
                }
            )
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                try:
                    args = (
                        json.loads(raw_args)
                        if isinstance(raw_args, str)
                        else (raw_args or {})
                    )
                except json.JSONDecodeError:
                    args = {}
                log.info("tool_call tool=%s", name)
                try:
                    result = dispatch(settings, name, args, router=router)
                    count = result.get("count") if isinstance(result, dict) else None
                    log.info("tool_result tool=%s status=success count=%s", name, count)
                except Exception:  # noqa: BLE001 - isolate tool failures
                    result = {"error": {"code": "tool_failed"}}
                    log.warning("tool_result tool=%s status=failed", name)
                citation_allowlist.update(_citation_labels(result))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )
            # Loop back to the brain with the tool results.

        # Exceeded the round cap — return whatever the last brain message was.
        if last_route is None:
            raise HTTPException(503, detail={"code": "compute_unavailable", "reasons": []})
        return _chat_response(
            settings,
            "(agentd: reached tool-call round cap; please rephrase.)",
            finish_reason="length",
            route=last_route,
            citations=[],
        )

    async def product_ask_chat(question: str) -> JSONResponse:
        return await chat(
            ChatRequest(
                messages=[ChatMessage(role="user", content=question)],
                temperature=0,
                max_tokens=700,
                stream=False,
            )
        )

    install_product_routes(
        app,
        settings,
        store=product_store,
        client_factory=product_client_factory,
        clock=product_clock,
        ask_chat=product_ask_chat,
    )
    return app


def _citation_labels(result: object) -> dict[int, str]:
    """Extract only event metadata returned by this request's successful tools."""
    if not isinstance(result, dict) or "error" in result:
        return {}
    candidates: list[object] = list(result.get("hits", []))
    if "event_id" in result:
        candidates.append(
            {"id": result.get("event_id"), "ts": result.get("ts"), "app": result.get("app")}
        )
    labels: dict[int, str] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        event_id = candidate.get("id")
        ts = candidate.get("ts")
        app = candidate.get("app")
        if isinstance(event_id, bool) or not isinstance(event_id, int):
            continue
        if not isinstance(ts, str) or not isinstance(app, str) or not app.strip():
            continue
        try:
            hhmm = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%H:%M")
        except ValueError:
            continue
        labels[event_id] = f"{hhmm} {app}"
    return labels


def _validated_citations(content: str, allowlist: dict[int, str]) -> list[dict[str, int | str]] | None:
    markers = _EVENT_MARKER.findall(content)
    parsed = list(_CITATION_MARKER.finditer(content))
    if len(markers) != len(parsed):
        return None
    citations: list[dict[str, int | str]] = []
    for marker in parsed:
        event_id = int(marker.group("event_id"))
        label = f"{marker.group('hhmm')} {marker.group('app')}"
        if allowlist.get(event_id) != label:
            return None
        citations.append({"event_id": event_id, "label": label})
    return citations


def _chat_response(
    settings: Settings,
    content: str,
    *,
    finish_reason: str,
    route: RouteMetadata,
    citations: list[dict[str, int | str]],
) -> JSONResponse:
    import time
    import uuid

    return JSONResponse(
        {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": settings.model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "dejaview": {**route.as_dict(), "latency_ms": route.latency_ms, "citations": citations},
        }
    )
