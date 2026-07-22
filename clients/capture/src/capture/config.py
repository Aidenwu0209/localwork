"""Configuration loader for the capture client (handbook §5.2).

Reads `capture.yaml` from one of (first hit wins):
  - $CAPTURE_CONFIG env var path
  - ./capture.yaml
  - ~/.config/dejaview/capture.yaml

Missing keys fall back to documented defaults. `device_id` defaults to the
hostname so two installs on different machines never collide without config.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# Native-resolution cap before upload (handbook §5.2: "等比缩至宽 ≤2560px").
MAX_UPLOAD_WIDTH = 2560
# WebP encode quality.
WEBP_QUALITY = 80
# dhash Hamming distance below which two consecutive frames are treated as
# duplicates and the second is dropped (handbook §5.2: "dhash ... 距离 < 10
# 则丢弃"). Set <= 0 to disable dedup entirely.
DEDUP_DISTANCE = 10


def _default_device_id() -> str:
    """Derive a stable device id from the hostname (short form, no .local)."""
    try:
        host = socket.gethostname() or "unknown"
    except OSError:
        host = "unknown"
    # `foo.local` -> `foo`; trim any domain suffix the resolver tacked on.
    return host.split(".")[0] or "unknown"


def _config_search_paths() -> list[Path]:
    candidates = []
    env_path = os.environ.get("CAPTURE_CONFIG")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.append(Path.cwd() / "capture.yaml")
    candidates.append(Path.home() / ".config" / "dejaview" / "capture.yaml")
    return candidates


@dataclass
class CaptureConfig:
    """Effective capture configuration after merging file + defaults."""

    memoryd_url: str = "http://127.0.0.1:8090"
    device_id: str = field(default_factory=_default_device_id)

    # Trigger parameters (handbook §5.2): poll cadence, min gap, periodic fallback.
    poll_interval: float = 3.0      # seconds between active-window checks
    min_capture_interval: float = 3.0  # minimum gap between two uploads
    periodic_interval: float = 30.0    # fallback frame cadence

    # Image processing.
    max_upload_width: int = MAX_UPLOAD_WIDTH
    webp_quality: int = WEBP_QUALITY
    # Dedup: drop a frame whose dhash distance from the previous uploaded frame
    # is below this threshold (handbook §5.2). <= 0 disables dedup.
    dedup_distance: int = DEDUP_DISTANCE
    # Which display to capture: 0 = all monitors combined (default; sees the
    # full desktop layout across every screen), 1 = primary monitor only.
    monitor_index: int = 0

    # Whether to attempt browser URL probing (best effort, osascript).
    probe_url: bool = True

    # Where the config came from (for logging at startup).
    source: str = "defaults"

    @property
    def frame_endpoint(self) -> str:
        return f"{self.memoryd_url.rstrip('/')}/v1/ingest/frame"

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "CaptureConfig":
        """Load config from `path`, or auto-discover it; fall back to defaults."""
        if path is not None:
            p = Path(path).expanduser()
            data = _read_yaml(p)
            return cls._from_dict(data, source=str(p))

        for candidate in _config_search_paths():
            if candidate.is_file():
                data = _read_yaml(candidate)
                return cls._from_dict(data, source=str(candidate))

        return cls._from_dict({}, source="defaults")

    @classmethod
    def _from_dict(cls, data: dict, *, source: str) -> "CaptureConfig":
        data = data or {}
        # Allow CAPTURE_DEVICE_ID / DEJAVIEW_DEVICE_ID to override the file.
        device_id = (
            os.environ.get("CAPTURE_DEVICE_ID")
            or os.environ.get("DEJAVIEW_DEVICE_ID")
            or data.get("device_id")
            or _default_device_id()
        )
        memoryd_url = (
            os.environ.get("CAPTURE_MEMORYD_URL")
            or data.get("memoryd_url")
            or "http://127.0.0.1:8090"
        )
        return cls(
            memoryd_url=memoryd_url.rstrip("/"),
            device_id=device_id,
            poll_interval=float(data.get("poll_interval", cls.poll_interval)),
            min_capture_interval=float(
                data.get("min_capture_interval", cls.min_capture_interval)
            ),
            periodic_interval=float(data.get("periodic_interval", cls.periodic_interval)),
            max_upload_width=int(data.get("max_upload_width", cls.max_upload_width)),
            webp_quality=int(data.get("webp_quality", cls.webp_quality)),
            dedup_distance=int(data.get("dedup_distance", cls.dedup_distance)),
            monitor_index=int(data.get("monitor_index", cls.monitor_index)),
            probe_url=bool(data.get("probe_url", cls.probe_url)),
            source=source,
        )


def _read_yaml(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise RuntimeError(f"cannot read config {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise RuntimeError(f"config {path}: expected a mapping at the top level")
    return data
