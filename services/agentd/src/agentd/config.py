"""agentd configuration (handbook §6.5).

agentd is the brain's对外出口: it speaks OpenAI-compatible /v1/chat/completions
to Open WebUI and resolves user questions via tool-calling against the timeline
DB, the Honcho user model, and the kb_chunks store. Inference for the brain
itself goes through the gateway (logical name `brain`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _env_path(name: str, default: str) -> Path:
    return Path(_env(name, default)).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    gateway_url: str          # brain + embed via LiteLLM
    timeline_db_url: str      # timeline_events + kb_chunks live here
    honcho_url: str           # Honcho dialectic (query_user_model)
    data_root: Path           # screenshots under here (fetch_screenshot)

    model_name: str = "dejaview"   # the OpenAI-compatible model id we expose
    brain_model: str = "brain"     # logical name at the gateway for tool-calling

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            gateway_url=_env("GATEWAY_URL", "http://127.0.0.1:4000/v1").rstrip("/"),
            timeline_db_url=_env(
                "TIMELINE_DB_URL",
                "postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview",
            ),
            honcho_url=_env("HONCHO_URL", "http://127.0.0.1:8100").rstrip("/"),
            data_root=_env_path("DATA_ROOT", "~/dejaview-data"),
            model_name=_env("AGENTD_MODEL_NAME", "dejaview"),
            brain_model=_env("AGENTD_BRAIN_MODEL", "brain"),
        )
