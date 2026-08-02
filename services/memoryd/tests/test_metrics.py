from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from memoryd.config import Settings
from memoryd.metrics import MemoryMetrics
from memoryd.models import IngestAck
from memoryd.pipeline import Pipeline
from memoryd.server import create_app
from memoryd.stages import (
    GatewayEmbed,
    GatewayPerceive,
    GatewaySentinel,
    OcrdClient,
    RealNovelty,
    StubEmbed,
    StubNovelty,
    StubOcr,
    StubPerceive,
    StubSentinel,
)
from memoryd.storage import TimelineStore


class MemoryMetricsTest(unittest.TestCase):
    def test_frame_outcomes_and_timeline_counter(self) -> None:
        metrics = MemoryMetrics()
        metrics.observe_frame(IngestAck(accepted=True, event_id=42))
        metrics.observe_frame(IngestAck(accepted=True, merged_into=42))
        metrics.observe_frame(IngestAck(accepted=False))

        rendered = metrics.render_prometheus()
        self.assertIn('dejaview_memory_ingest_total{outcome="created"} 1', rendered)
        self.assertIn('dejaview_memory_ingest_total{outcome="merged"} 1', rendered)
        self.assertIn('dejaview_memory_ingest_total{outcome="blocked"} 1', rendered)
        self.assertIn("dejaview_timeline_events_total 1", rendered)

    def test_unclassified_accept_is_not_counted(self) -> None:
        metrics = MemoryMetrics()
        metrics.observe_frame(IngestAck(accepted=True))
        rendered = metrics.render_prometheus()
        self.assertIn("dejaview_timeline_events_total 0", rendered)

    def test_metrics_route_is_prometheus_text(self) -> None:
        client = TestClient(create_app(pipeline=object()))
        response = client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["content-type"],
            "text/plain; version=0.0.4; charset=utf-8",
        )
        self.assertIn("dejaview_timeline_events_total 0", response.text)

    def test_health_exposes_demo_pipeline_identity_without_credentials(self) -> None:
        settings = Settings(
            gateway_url=(
                "https://gateway-user:gateway-secret@Example.COM:4443/v1"
                "?api_key=do-not-return"
            ),
            ocr_url="http://127.0.0.1:8006",
            timeline_db_url=("postgresql://user:secret@127.0.0.1:5433/dejaview_demo"),
            redis_url="redis://127.0.0.1:6380/0",
            data_root=Path("/tmp/dejaview-p34-data").resolve(),
            honcho_flush_event_count=20,
            honcho_flush_seconds=300,
        )
        pipeline = Pipeline(
            sentinel=GatewaySentinel(settings.gateway_url),
            ocr=OcrdClient(settings.ocr_url),
            novelty=RealNovelty(settings.gateway_url),
            perceive=GatewayPerceive(settings.gateway_url),
            embed=GatewayEmbed(settings.gateway_url),
            store=TimelineStore(settings.timeline_db_url, settings.data_root),
        )
        with patch.dict("os.environ", {"MEMORYD_REAL_PIPELINE": "0"}):
            response = TestClient(create_app(settings=settings, pipeline=pipeline)).get(
                "/health"
            )
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "pipeline": "real",
                "gateway_origin": "https://example.com:4443",
                "database": "dejaview_demo",
                "data_root": str(Path("/tmp/dejaview-p34-data").resolve()),
            },
        )
        self.assertNotIn("secret", response.text)
        self.assertNotIn("gateway-user", response.text)
        self.assertNotIn("api_key", response.text)

    def test_health_does_not_trust_real_pipeline_environment_flag(self) -> None:
        settings = Settings(
            gateway_url="http://127.0.0.1:4000/v1",
            ocr_url="http://127.0.0.1:8006",
            timeline_db_url=("postgresql://user:secret@127.0.0.1:5433/dejaview_demo"),
            redis_url="redis://127.0.0.1:6380/0",
            data_root=Path("/tmp/dejaview-p34-data").resolve(),
            honcho_flush_event_count=20,
            honcho_flush_seconds=300,
        )
        pipeline = Pipeline(
            sentinel=StubSentinel(),
            ocr=StubOcr(),
            novelty=StubNovelty(),
            perceive=StubPerceive(),
            embed=StubEmbed(),
            store=TimelineStore(settings.timeline_db_url, settings.data_root),
        )
        with patch.dict("os.environ", {"MEMORYD_REAL_PIPELINE": "1"}):
            response = TestClient(create_app(settings=settings, pipeline=pipeline)).get(
                "/health"
            )
        self.assertEqual(response.json()["pipeline"], "stub")


if __name__ == "__main__":
    unittest.main()
