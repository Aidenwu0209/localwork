from __future__ import annotations

import unittest
from pathlib import Path

from agentd.config import Settings
from agentd.server import create_app
from fastapi.testclient import TestClient


class AgentHealthTest(unittest.TestCase):
    def test_health_exposes_sanitized_runtime_identity(self) -> None:
        settings = Settings(
            gateway_url=(
                "https://gateway-user:gateway-secret@Example.COM:4443/v1"
                "?api_key=do-not-return"
            ),
            radeon_gateway_url="https://radeon-user:radeon-secret@Radeon.EXAMPLE:4444/v1",
            local_gateway_url="http://local-user:local-secret@127.0.0.1:4000/v1",
            timeline_db_url=(
                "postgresql://database-user:database-secret@127.0.0.1:5433/"
                "dejaview_demo?sslmode=disable"
            ),
            honcho_url=(
                "http://honcho-user:honcho-secret@127.0.0.1:8100/private"
                "?token=do-not-return"
            ),
            data_root=Path("/tmp/dejaview-p34-data").resolve(),
            model_name="dejaview",
            brain_model="brain",
        )

        response = TestClient(create_app(settings=settings)).get("/health")

        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "service": "agentd",
                "model": "dejaview",
                "brain_model": "brain",
                "gateway_origin": "https://example.com:4443",
                "radeon_gateway_origin": "https://radeon.example:4444",
                "local_gateway_origin": "http://127.0.0.1:4000",
                "honcho_origin": "http://127.0.0.1:8100",
                "database": "dejaview_demo",
                "data_root": str(Path("/tmp/dejaview-p34-data").resolve()),
            },
        )
        for secret in (
            "gateway-user",
            "gateway-secret",
            "database-user",
            "database-secret",
            "honcho-user",
            "honcho-secret",
            "api_key",
            "token",
        ):
            self.assertNotIn(secret, response.text)


if __name__ == "__main__":
    unittest.main()
