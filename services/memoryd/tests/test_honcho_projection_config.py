"""P3.15 Honcho routing configuration must remain a credential-free origin."""

from __future__ import annotations

import pytest

from memoryd.config import _safe_honcho_url


@pytest.mark.parametrize(
    "value",
    (
        "https://honcho.synthetic/v3",
        "http://honcho.synthetic/private/queue",
        "https://user:secret@honcho.synthetic",
    ),
)
def test_honcho_url_rejects_paths_and_credentials(value: str) -> None:
    with pytest.raises(ValueError, match="origin"):
        _safe_honcho_url(value)


def test_honcho_url_accepts_bare_origin_and_root_slash() -> None:
    assert _safe_honcho_url("https://honcho.synthetic") == "https://honcho.synthetic"
    assert _safe_honcho_url("https://honcho.synthetic/") == "https://honcho.synthetic"
