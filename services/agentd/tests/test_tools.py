from __future__ import annotations

import unittest
from pathlib import Path
from typing import Self
from unittest.mock import patch

from agentd.config import Settings
from agentd.router import EmbeddingResult, RouteMetadata
from agentd.tools import fetch_screenshot, query_user_model, search_timeline


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"content": "synthetic preference"}


class _Client:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(self, url: str, *, json: dict) -> _Response:
        self.posts.append((url, json))
        return _Response()


class _EmbeddingRouter:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed(self, query: str) -> EmbeddingResult:
        self.queries.append(query)
        return EmbeddingResult(
            embedding=[0.5] * 1024,
            route=RouteMetadata(
                backend="local_metal",
                physical_model="embed",
                logical_model="embed",
                degraded=True,
                reason="remote_timeout",
                latency_ms=3,
            ),
        )


class QueryUserModelTest(unittest.TestCase):
    def test_demo_can_select_a_dedicated_honcho_namespace(self) -> None:
        settings = Settings(
            gateway_url="http://127.0.0.1:14000/v1",
            timeline_db_url=(
                "postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview_demo"
            ),
            honcho_url="http://127.0.0.1:8100",
            data_root=Path("/tmp/dejaview-p34-data"),
        )
        client = _Client()
        with patch("agentd.tools.httpx.Client", return_value=client):
            result = query_user_model(
                settings,
                question="Which setup?",
                session_id="p3-4-synthetic",
                workspace_id="dejaview-p34",
                peer_id="demo-owner",
            )

        chat_url, chat_payload = client.posts[-1]
        self.assertEqual(
            chat_url,
            ("http://127.0.0.1:8100/v3/workspaces/dejaview-p34/peers/demo-owner/chat"),
        )
        self.assertEqual(chat_payload["session_id"], "p3-4-synthetic")
        self.assertEqual(result["workspace_id"], "dejaview-p34")
        self.assertEqual(result["peer_id"], "demo-owner")

    def test_semantic_timeline_search_uses_the_injected_shared_router(self) -> None:
        settings = Settings(
            gateway_url="http://synthetic-legacy/v1",
            timeline_db_url="postgresql://synthetic/dejaview",
            honcho_url="http://synthetic-honcho",
            data_root=Path("/tmp/agentd-tools-synthetic"),
        )
        router = _EmbeddingRouter()
        with patch("agentd.tools._semantic", return_value=[]):
            result = search_timeline(
                settings,
                query="synthetic semantic query",
                mode="semantic",
                router=router,  # type: ignore[arg-type]
            )

        self.assertEqual(router.queries, ["synthetic semantic query"])
        self.assertEqual(result["count"], 0)

    def test_fetch_screenshot_returns_only_opaque_evidence_metadata(self) -> None:
        settings = Settings(
            gateway_url="http://synthetic/v1",
            timeline_db_url="postgresql://synthetic/dejaview",
            honcho_url="http://synthetic-honcho",
            data_root=Path("/tmp/agentd-tools-synthetic"),
        )
        row = (
            42,
            None,
            "VS Code",
            "PRIVATE TITLE",
            "/tmp/PRIVATE-PATH.webp",
            [{"text": "PRIVATE OCR", "bbox": [1, 2, 3, 4]}],
        )
        cursor = unittest.mock.MagicMock()
        cursor.fetchone.return_value = row
        connection = unittest.mock.MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
        with patch("agentd.tools.psycopg.connect", return_value=connection):
            result = fetch_screenshot(settings, event_id=42, highlight_text="PRIVATE")

        self.assertEqual(
            result,
            {
                "event_id": 42,
                "found": True,
                "app": "VS Code",
                "evidence_available": True,
                "evidence_reference": "event:42",
                "highlights": [{"bbox": [1, 2, 3, 4]}],
            },
        )
        self.assertNotIn("PRIVATE", repr(result))


if __name__ == "__main__":
    unittest.main()
