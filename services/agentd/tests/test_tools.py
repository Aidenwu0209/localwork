from __future__ import annotations

import unittest
from pathlib import Path
from typing import Self
from unittest.mock import patch

from agentd.config import Settings
from agentd.tools import query_user_model


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


if __name__ == "__main__":
    unittest.main()
