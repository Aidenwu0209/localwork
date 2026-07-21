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
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agentd.config import Settings
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


def create_app(*, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="DejaView agentd", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "model": settings.model_name}

    @app.get("/v1/models")
    async def list_models() -> dict:
        return {"object": "list", "data": [
            {"id": settings.model_name, "object": "model", "owned_by": "dejaview"}
        ]}

    @app.post("/v1/chat/completions")
    async def chat(req: ChatRequest) -> JSONResponse:
        if req.stream:
            # Acknowledge but fall back to non-streaming (Phase 2 will add SSE).
            pass

        # Build the conversation: system prompt + the user's messages.
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in req.messages:
            messages.append(m.model_dump(exclude_none=True))

        gateway_base = settings.gateway_url.rstrip("/")
        if not gateway_base.endswith("/v1"):
            gateway_base = gateway_base + "/v1"

        round_idx = 0
        finish_reason = "stop"
        while round_idx < MAX_TOOL_ROUNDS:
            round_idx += 1
            # Ask the brain.
            brain_body: dict[str, Any] = {
                "model": settings.brain_model,
                "messages": messages,
                "tools": SPECS,
                "tool_choice": "auto",
            }
            if req.temperature is not None:
                brain_body["temperature"] = req.temperature
            if req.max_tokens is not None:
                brain_body["max_tokens"] = req.max_tokens

            try:
                with httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
                    r = client.post(f"{gateway_base}/chat/completions", json=brain_body)
                    r.raise_for_status()
                    brain_resp = r.json()
            except httpx.HTTPStatusError as exc:
                raise HTTPException(502, f"brain error: {exc.response.text[:300]}") from exc
            except (httpx.ReadTimeout, httpx.ConnectError) as exc:
                raise HTTPException(504, f"brain unreachable: {exc}") from exc

            choice = brain_resp["choices"][0]
            msg = choice["message"]
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                # Final answer.
                content = msg.get("content") or ""
                return _chat_response(settings, content, finish_reason="stop")

            # Append the assistant message (with tool_calls) to the conversation,
            # then execute each tool call and append a tool result message.
            messages.append({
                "role": "assistant",
                "content": msg.get("content"),
                "tool_calls": tool_calls,
            })
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except json.JSONDecodeError:
                    args = {}
                log.info("tool call: %s args=%s", name, args)
                try:
                    result = dispatch(settings, name, args)
                    log.info("tool result: %s -> %s", name, str(result)[:120])
                except Exception as exc:
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                    log.warning("tool %s failed: %s", name, exc)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })
            # Loop back to the brain with the tool results.

        # Exceeded the round cap — return whatever the last brain message was.
        return _chat_response(
            settings,
            "(agentd: reached tool-call round cap; please rephrase.)",
            finish_reason="length",
        )

    return app


def _chat_response(settings: Settings, content: str, *, finish_reason: str) -> JSONResponse:
    import time, uuid
    return JSONResponse({
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": settings.model_name,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })
