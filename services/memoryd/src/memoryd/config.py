"""Configuration loaded from environment (handbook §4.2 / .env.example).

App code speaks ONLY logical model names (brain/perceive/sentinel/fast/embed);
physical routing lives entirely in deploy/server/litellm.yaml. memoryd reaches
inference through one gateway URL and OCR through one direct URL (ocrd is
deterministic, not an LLM, so it bypasses LiteLLM per handbook §2.3).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    return value.strip()


def _env_path(name: str, default: str) -> Path:
    # Expand ~ and resolve; do NOT require existence at import time (the dir is
    # created lazily by the storage layer on first write).
    return Path(_env(name, default)).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    # Inference — logical names only. Gateway is LiteLLM (:4000); ocrd is direct.
    gateway_url: str  # e.g. http://127.0.0.1:4000/v1
    ocr_url: str  # e.g. http://127.0.0.1:8006

    # Data layer (Mac is the data-sovereignty side).
    timeline_db_url: str  # postgresql://... dejaview db
    redis_url: str

    # Single portable root for all user artifacts (screenshots/audio/docs).
    data_root: Path

    # Honcho throttling (handbook §6.2 step 6): batch activity lines into one
    # Honcho message every N events or every M seconds, whichever first.
    honcho_flush_event_count: int
    honcho_flush_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            gateway_url=_env("GATEWAY_URL", "http://127.0.0.1:4000/v1").rstrip("/"),
            ocr_url=_env("OCR_URL", "http://127.0.0.1:8006").rstrip("/"),
            timeline_db_url=_env(
                "TIMELINE_DB_URL",
                "postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview",
            ),
            redis_url=_env("REDIS_URL", "redis://127.0.0.1:6380/0"),
            data_root=_env_path("DATA_ROOT", "~/dejaview-data"),
            honcho_flush_event_count=int(_env("HONCHO_FLUSH_EVENT_COUNT", "20")),
            honcho_flush_seconds=int(_env("HONCHO_FLUSH_SECONDS", "300")),
        )
