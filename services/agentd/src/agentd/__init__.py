"""DejaView agentd — the brain's OpenAI-compatible出口 (handbook §6.5).

Exposes /v1/chat/completions (model=dejaview) and runs a tool-calling loop
against the four memory tools (search_timeline / query_user_model / search_kb /
fetch_screenshot). The brain itself is reached via the LiteLLM gateway's
logical `brain` name (server: ThinkingCap-27B; Mac dev: E4B dual-mapped).
"""

from agentd.server import create_app

__all__ = ["create_app", "main"]


def main() -> None:
    import uvicorn

    uvicorn.run(
        "agentd:create_app",
        factory=True,
        host="127.0.0.1",
        port=8101,
        reload=False,
    )
