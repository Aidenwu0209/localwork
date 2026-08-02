"""Embedding helper for the query side (handbook §6.5)."""

from __future__ import annotations

from agentd.router import ComputeRouter


def embed_query(router: ComputeRouter, query: str) -> list[float]:
    """Embed a user query through the shared verified compute router."""
    return router.embed(query).embedding
