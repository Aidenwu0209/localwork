from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from capture import main
from capture.agent import run_agent
from capture.config import CaptureConfig
from capture.permissions import PermissionCheck


def test_permission_denied_exits_before_observer_or_http_client() -> None:
    config = CaptureConfig(device_id="synthetic-device")
    with (
        patch("capture.agent.check_screen_recording_permission", return_value=PermissionCheck(False, "denied")),
        patch("capture.agent._install_lock_observer", side_effect=AssertionError("observer installed")),
        patch("capture.agent.httpx.AsyncClient", side_effect=AssertionError("http client created")),
    ):
        assert asyncio.run(run_agent(config)) == 2


def test_main_raises_system_exit_for_permission_exit_code() -> None:
    async def denied(_: CaptureConfig) -> int:
        return 2

    with patch("capture.agent.run_agent", denied):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 2
