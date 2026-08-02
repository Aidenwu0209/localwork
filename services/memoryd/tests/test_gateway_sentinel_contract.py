"""Outbound GatewaySentinel request contract tests."""

from __future__ import annotations

import asyncio

from memoryd.stages import GatewaySentinel


class FixtureResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "choices": [
                {"message": {"content": '{"category":"normal","confidence":1.0}'}}
            ]
        }


class RecordingClient:
    request: dict[str, object] | None = None

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    def __enter__(self) -> "RecordingClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, url: str, *, json: dict[str, object]) -> FixtureResponse:
        type(self).request = {"url": url, "body": json}
        return FixtureResponse()


def test_gateway_sentinel_requests_strict_category_confidence_schema(monkeypatch: object) -> None:
    monkeypatch.setattr("memoryd.stages.httpx.Client", RecordingClient)
    verdict = asyncio.run(
        GatewaySentinel("http://gateway.test/v1").classify(b"\x89PNG\r\n\x1a\nsynthetic")
    )
    assert verdict.decision == "allow"

    request = RecordingClient.request
    assert request is not None
    body = request["body"]
    assert isinstance(body, dict)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}

    response_format = body["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert isinstance(json_schema, dict)
    assert json_schema["strict"] is True
    schema = json_schema["schema"]
    assert isinstance(schema, dict)
    assert schema["type"] == "object"
    assert schema["required"] == ["category", "confidence"]
    assert schema["additionalProperties"] is False
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert properties["category"] == {
        "type": "string",
        "enum": [
            "password_prompt",
            "banking_finance",
            "private_chat",
            "id_document",
            "adult",
            "normal",
        ],
    }
    assert properties["confidence"] == {
        "type": "number",
        "minimum": 0.0,
        "maximum": 1.0,
    }
