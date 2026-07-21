"""``python -m ocrd`` entry — boots uvicorn serving ocrd on 127.0.0.1:8006.

Equivalent to the ``ocrd`` console script declared in ``pyproject.toml``;
kept so the service can be launched without installation (e.g. in dev via
``uv run python -m ocrd``) per the M5.1 task instructions.
"""

from __future__ import annotations

from . import main

if __name__ == "__main__":
    main()
