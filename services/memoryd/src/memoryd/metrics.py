"""Small, dependency-free Prometheus metrics for the local memory pipeline."""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memoryd.models import IngestAck


class MemoryMetrics:
    """Track frame-ingest outcomes without adding a Prometheus dependency."""

    _OUTCOMES = ("stored", "merged", "blocked")

    def __init__(self) -> None:
        self._lock = Lock()
        self._ingest_counts = {outcome: 0 for outcome in self._OUTCOMES}
        self._timeline_events = 0

    def observe_frame(self, ack: IngestAck) -> None:
        outcome = ack.processing_state

        with self._lock:
            self._ingest_counts[outcome] += 1
            if outcome == "stored":
                self._timeline_events += 1

    def render_prometheus(self) -> str:
        with self._lock:
            counts = dict(self._ingest_counts)
            timeline_events = self._timeline_events

        lines = [
            "# HELP dejaview_memory_ingest_total Frame ingests by final outcome.",
            "# TYPE dejaview_memory_ingest_total counter",
        ]
        lines.extend(
            f'dejaview_memory_ingest_total{{outcome="{outcome}"}} {counts[outcome]}'
            for outcome in self._OUTCOMES
        )
        lines.extend(
            [
                "# HELP dejaview_timeline_events_total New timeline events persisted.",
                "# TYPE dejaview_timeline_events_total counter",
                f"dejaview_timeline_events_total {timeline_events}",
            ]
        )
        return "\n".join(lines) + "\n"
