"""DejaView capture client — macOS MVP (handbook §5.1 / §5.2).

Continuously senses the user's screen: grabs a frame on frontmost-window
change (with a 30s periodic fallback) and POSTs it in memory to memoryd's
`/v1/ingest/frame` endpoint. Pixels never touch disk on the client side; a
frame is captured, encoded to WebP in RAM, uploaded, then dropped.

Run with:  `uv run python -m capture`  (after editing capture.yaml).
"""

from __future__ import annotations

import asyncio
import logging

from capture.config import CaptureConfig


__all__ = ["main", "CaptureConfig"]


def main() -> None:
    """Entry point for `python -m capture` and the `capture` console script."""
    from capture.agent import run_agent

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    config = CaptureConfig.load()
    try:
        asyncio.run(run_agent(config))
    except KeyboardInterrupt:
        # asyncio.run swallows the CancelledError; surface a clean exit.
        pass


if __name__ == "__main__":
    main()
