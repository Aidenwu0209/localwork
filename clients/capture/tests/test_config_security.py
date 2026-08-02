from __future__ import annotations

import pytest

from capture.config import CaptureConfig


@pytest.mark.parametrize(
    "device_id",
    (
        "../../../../../../tmp/escape",
        "device/child",
        r"device\child",
        "device\nchild",
        "d" * 129,
    ),
)
def test_capture_config_rejects_unsafe_device_id(device_id: str) -> None:
    with pytest.raises(ValueError, match="device_id"):
        CaptureConfig(device_id=device_id)
