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


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name, "true" if default else "false").lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{name} must be one of 1,true,yes,on,0,false,no,off; got {value!r}"
    )


@dataclass(frozen=True)
class Settings:
    # Inference — logical names only. Gateway is LiteLLM (:4000); ocrd is direct.
    gateway_url: str  # e.g. http://127.0.0.1:4000/v1
    ocr_url: str  # e.g. http://127.0.0.1:8006

    # Data layer (Mac is the data-sovereignty side).
    timeline_db_url: str  # postgresql://... dejaview db
    redis_url: str

    # Single portable root for supported user artifacts (currently screenshots).
    data_root: Path

    # Honcho throttling (handbook §6.2 step 6): batch activity lines into one
    # Honcho message every N events or every M seconds, whichever first.
    honcho_flush_event_count: int
    honcho_flush_seconds: int

    # Pipeline safety: only explicit development/test wiring may use stubs.
    sentinel_gateway_url: str = "http://127.0.0.1:4000/v1"
    allow_stub_pipeline: bool = False

    # Honcho projection is local-only and intentionally separate from the
    # inference gateway.  Credentials in this URL are rejected at startup so
    # they cannot leak through health/status/errors.
    honcho_url: str = "http://127.0.0.1:8100"
    honcho_workspace: str = "dejaview"
    honcho_peer: str = "owner"
    honcho_poll_seconds: int = 5
    honcho_lease_seconds: int = 30
    honcho_retry_seconds: int = 5
    honcho_max_retry_seconds: int = 300
    honcho_max_attempts: int = 5
    honcho_batch_size: int = 20
    honcho_timezone: str = "Asia/Shanghai"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            gateway_url=_env("GATEWAY_URL", "http://127.0.0.1:4000/v1").rstrip("/"),
            sentinel_gateway_url=_env(
                "SENTINEL_GATEWAY_URL", "http://127.0.0.1:4000/v1"
            ).rstrip("/"),
            ocr_url=_env("OCR_URL", "http://127.0.0.1:8006").rstrip("/"),
            timeline_db_url=_env(
                "TIMELINE_DB_URL",
                "postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview",
            ),
            redis_url=_env("REDIS_URL", "redis://127.0.0.1:6380/0"),
            data_root=_env_path("DATA_ROOT", "~/dejaview-data"),
            honcho_flush_event_count=int(_env("HONCHO_FLUSH_EVENT_COUNT", "20")),
            honcho_flush_seconds=int(_env("HONCHO_FLUSH_SECONDS", "300")),
            allow_stub_pipeline=_env_bool("MEMORYD_ALLOW_STUB_PIPELINE", False),
            honcho_url=_safe_honcho_url(_env("HONCHO_URL", "http://127.0.0.1:8100")),
            honcho_workspace=_safe_identifier(_env("HONCHO_WORKSPACE", "dejaview")),
            honcho_peer=_safe_identifier(_env("HONCHO_PEER", "owner")),
            honcho_poll_seconds=_positive_int("HONCHO_POLL_SECONDS", "5"),
            honcho_lease_seconds=_positive_int("HONCHO_LEASE_SECONDS", "30"),
            honcho_retry_seconds=_positive_int("HONCHO_RETRY_SECONDS", "5"),
            honcho_max_retry_seconds=_positive_int("HONCHO_MAX_RETRY_SECONDS", "300"),
            honcho_max_attempts=_positive_int("HONCHO_MAX_ATTEMPTS", "5"),
            honcho_batch_size=_positive_int("HONCHO_BATCH_SIZE", "20"),
            honcho_timezone=_safe_timezone(_env("HONCHO_TIMEZONE", "Asia/Shanghai")),
        )


def _positive_int(name: str, default: str) -> int:
    try:
        value = int(_env(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _safe_identifier(value: str) -> str:
    if not value or len(value) > 128 or any(not (c.isalnum() or c in "-_" ) for c in value):
        raise ValueError("Honcho workspace and peer identifiers use only letters, digits, - and _")
    return value


def _safe_honcho_url(value: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("HONCHO_URL must be an http(s) origin without credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("HONCHO_URL must not include a query or fragment")
    return value.rstrip("/")


def _safe_timezone(value: str) -> str:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("HONCHO_TIMEZONE must name an installed IANA timezone") from exc
    return value
