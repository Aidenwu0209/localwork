"""Small, dependency-free Prometheus metrics for the local memory pipeline."""

from __future__ import annotations

import time
from datetime import datetime
from threading import Lock
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from memoryd.models import IngestAck


class MemoryMetrics:
    """Track frame-ingest outcomes without adding a Prometheus dependency."""

    _OUTCOMES = ("stored", "merged", "blocked")
    _CAPTURE_OUTCOMES = ("stored", "merged", "blocked", "failed")

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._lock = Lock()
        self._clock = clock
        self._ingest_counts = {outcome: 0 for outcome in self._OUTCOMES}
        self._timeline_events = 0
        self._capture_heartbeats: dict[str, tuple[datetime, dict[str, int]]] = {}
        self._last_capture_receipt = 0.0
        self._capture_totals = {outcome: 0 for outcome in self._CAPTURE_OUTCOMES}
        self._honcho_projection = {"enabled": 0, "pending": 0, "failed": 0}

    def observe_honcho_projection(self, status: dict[str, object]) -> None:
        """Store aggregate queue health only, never any event or payload text."""
        with self._lock:
            self._honcho_projection = {
                "enabled": 1 if status.get("enabled") is True else 0,
                "pending": max(0, int(status.get("pending") or 0)),
                "failed": max(0, int(status.get("failed") or 0)),
            }

    def observe_capture_heartbeat(
        self, *, device_id: str, client_ts: datetime, counters: dict[str, int]
    ) -> bool:
        with self._lock:
            self._last_capture_receipt = self._clock()
            previous = self._capture_heartbeats.get(device_id)
            if previous is not None and client_ts <= previous[0]:
                return False
            previous_counters = previous[1] if previous is not None else None
            for outcome in self._CAPTURE_OUTCOMES:
                current = counters[outcome]
                last = previous_counters[outcome] if previous_counters is not None else 0
                # A lower value is a client restart, so its new absolute
                # counter contributes from zero without reducing this server
                # counter.
                self._capture_totals[outcome] += current - last if current >= last else current
            self._capture_heartbeats[device_id] = (client_ts, dict(counters))
            return True

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
            capture_counts = dict(self._capture_totals)
            honcho_projection = dict(self._honcho_projection)
            last_heartbeat = self._last_capture_receipt

        lines = [
            "# HELP dejaview_memory_ingest_total Frame ingests by final outcome.",
            "# TYPE dejaview_memory_ingest_total counter",
        ]
        lines.extend(
            f'dejaview_memory_ingest_total{{outcome="{outcome}"}} {counts[outcome]}'
            for outcome in self._OUTCOMES
        )
        lines.extend(
            f'dejaview_capture_frames_total{{outcome="{outcome}"}} {capture_counts[outcome]}'
            for outcome in self._CAPTURE_OUTCOMES
        )
        lines.append(f"dejaview_capture_last_heartbeat_unixtime {last_heartbeat}")
        lines.extend(
            [
                "# HELP dejaview_honcho_projection_enabled Whether local Honcho projection is enabled.",
                "# TYPE dejaview_honcho_projection_enabled gauge",
                f"dejaview_honcho_projection_enabled {honcho_projection['enabled']}",
                "# HELP dejaview_honcho_projection_pending Pending or leased local outbox rows.",
                "# TYPE dejaview_honcho_projection_pending gauge",
                f"dejaview_honcho_projection_pending {honcho_projection['pending']}",
                "# HELP dejaview_honcho_projection_failed Terminal local outbox failures.",
                "# TYPE dejaview_honcho_projection_failed gauge",
                f"dejaview_honcho_projection_failed {honcho_projection['failed']}",
            ]
        )
        lines.extend(
            [
                "# HELP dejaview_timeline_events_total New timeline events persisted.",
                "# TYPE dejaview_timeline_events_total counter",
                f"dejaview_timeline_events_total {timeline_events}",
            ]
        )
        return "\n".join(lines) + "\n"
