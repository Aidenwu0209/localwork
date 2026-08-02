"""Offline integrity tests for the P3.4 synthetic recording helpers."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from agentd.router import RouteMetadata, RouteResult


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


daily = _load_module("p34_daily", "demo_daily_report.py")
seed = _load_module("p34_seed", "seed_demo_p34.py")
honcho_seed = _load_module("p34_honcho_seed", "seed_honcho_p34.py")
stage = _load_module("p34_stage", "demo_stage.py")


def _events() -> list[dict]:
    return [
        {
            "id": 42,
            "hhmm": "09:18",
            "app": "VS Code",
            "activity": "Reviewing a synthetic kernel",
        }
    ]


def _route(*, backend: str, reason: str) -> RouteMetadata:
    return RouteMetadata(
        backend=backend,
        physical_model="brain" if backend == "radeon" else "perceive",
        logical_model="brain",
        degraded=backend == "local_metal",
        reason=reason,
        latency_ms=7,
    )


class _DailyRouter:
    def __init__(self, responses: list[RouteResult]) -> None:
        self.responses = iter(responses)
        self.calls: list[dict] = []

    def chat(self, logical_model: str, messages: list[dict], **kwargs: object) -> RouteResult:
        self.calls.append({"logical_model": logical_model, "messages": messages, **kwargs})
        return next(self.responses)


def test_previous_week_wednesday_is_dynamic() -> None:
    tz = timezone(timedelta(hours=8))
    now = datetime(2026, 8, 6, 12, 0, tzinfo=tz)
    result = seed._previous_week_wednesday(now)
    assert result.isoformat() == "2026-07-29T15:18:00+08:00"


def test_daily_gate_accepts_exact_citation_metadata() -> None:
    report = "# Daily\n\n- Reviewed the kernel. [event#42 09:18 VS Code]"
    validation = daily._validate_report(report, _events())
    assert validation["pass"] is True
    assert validation["uncited_factual_lines"] == []


def test_daily_gate_rejects_wrong_app_and_uncited_fact() -> None:
    report = (
        "# Daily\n\n"
        "- Reviewed the kernel. [event#42 09:18 Chrome]\n"
        "- This line has no evidence."
    )
    validation = daily._validate_report(report, _events())
    assert validation["pass"] is False
    assert validation["metadata_mismatches"]
    assert validation["uncited_factual_lines"] == ["- This line has no evidence."]


def test_json_agent_retries_a_non_json_planner_response() -> None:
    router = _DailyRouter(
        [
            RouteResult(
                content="I cannot plan without seeing the events.",
                message={"content": "I cannot plan without seeing the events."},
                route=_route(backend="radeon", reason="primary_ok"),
            ),
            RouteResult(
                content='{"sections":["Focus","Evidence"],"focus":"grounded work"}',
                message={"content": '{"sections":["Focus","Evidence"],"focus":"grounded work"}'},
                route=_route(backend="radeon", reason="primary_ok"),
            ),
        ]
    )

    plan, attempts = daily._router_json_with_retry(
        router,
        model="fast",
        system="return JSON",
        user="plan",
        max_tokens=100,
        validate=daily._validate_plan_response,
    )

    assert attempts == 2
    assert plan["sections"] == ["Focus", "Evidence"]


def test_daily_json_parser_does_not_echo_model_output_in_errors() -> None:
    with pytest.raises(ValueError) as failure:
        daily._parse_json_object("upstream secret-token and private report text")

    assert "secret-token" not in str(failure.value)
    assert "private report" not in str(failure.value)


def test_json_agent_retries_contradictory_empty_rejection() -> None:
    router = _DailyRouter(
        [
            RouteResult(
                content='{"decision":"reject","issues":[]}',
                message={"content": '{"decision":"reject","issues":[]}'},
                route=_route(backend="radeon", reason="primary_ok"),
            ),
            RouteResult(
                content='{"decision":"pass","issues":[]}',
                message={"content": '{"decision":"pass","issues":[]}'},
                route=_route(backend="radeon", reason="primary_ok"),
            ),
        ]
    )

    review, attempts = daily._router_json_with_retry(
        router,
        model="fast",
        system="return JSON",
        user="review",
        max_tokens=100,
        validate=daily._validate_review_response,
    )

    assert attempts == 2
    assert review == {"decision": "pass", "issues": []}


def test_daily_router_retries_invalid_json_and_records_actual_fallback_route() -> None:
    router = _DailyRouter(
        [
            RouteResult(
                content="not JSON",
                message={"content": "not JSON"},
                route=_route(backend="radeon", reason="primary_ok"),
            ),
            RouteResult(
                content='{"sections":["Focus","Evidence"],"focus":"grounded"}',
                message={"content": '{"sections":["Focus","Evidence"],"focus":"grounded"}'},
                route=_route(backend="local_metal", reason="remote_invalid_output"),
            ),
        ]
    )
    routes: list[dict] = []

    plan, attempts = daily._router_json_with_retry(
        router,
        model="fast",
        system="return JSON",
        user="plan",
        max_tokens=100,
        validate=daily._validate_plan_response,
        route_metadata=routes,
    )

    assert plan["focus"] == "grounded"
    assert attempts == 2
    assert routes == [
        {"backend": "radeon", "physical_model": "brain", "logical_model": "brain", "degraded": False, "reason": "primary_ok", "latency_ms": 7},
        {"backend": "local_metal", "physical_model": "perceive", "logical_model": "brain", "degraded": True, "reason": "remote_invalid_output", "latency_ms": 7},
    ]
    assert [call["logical_model"] for call in router.calls] == ["fast", "fast"]


def test_daily_stream_publishes_completed_report_route_not_preselected_backend(monkeypatch) -> None:
    routes = {
        "planner": [{"backend": "radeon", "reason": "primary_ok"}],
        "writer": [{"backend": "local_metal", "reason": "remote_timeout"}],
        "reviewer": [{"backend": "local_metal", "reason": "remote_timeout"}],
    }
    launched: list[list[str]] = []

    class _Process:
        stdout = None

        def wait(self, timeout: float) -> int:
            return 0

        def poll(self) -> int:
            return 0

        def terminate(self) -> None:
            return None

    def fake_popen(command: list[str], **_kwargs: object) -> _Process:
        launched.append(command)
        output = Path(command[command.index("--output") + 1])
        audit_output = Path(command[command.index("--audit-output") + 1])
        output.write_text("# synthetic\n", encoding="utf-8")
        audit_output.write_text(json.dumps({"routes": routes}), encoding="utf-8")
        return _Process()

    monkeypatch.setattr(stage.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(stage, "_iter_process_output", lambda *_args, **_kwargs: iter(()))

    events = list(
        stage._daily_stream(
            radeon_gateway_url="http://synthetic-radeon/v1",
            local_gateway_url="http://synthetic-local/v1",
        )
    )

    result = next(json.loads(event[6:]) for event in events if '"type": "result"' in event)
    assert len(launched) == 1
    assert "--radeon-gateway-url" in launched[0]
    assert "--local-gateway-url" in launched[0]
    assert result["route_metadata"] == routes


def test_reviewer_event_projection_excludes_raw_ocr() -> None:
    event = {
        "id": 42,
        "hhmm": "09:18",
        "app": "VS Code",
        "activity": "Reviewing a synthetic kernel",
        "ocr_excerpt": "noisy \u25a1 OCR",
    }
    assert daily._review_event_projection([event]) == [
        {
            "id": 42,
            "hhmm": "09:18",
            "app": "VS Code",
            "activity": "Reviewing a synthetic kernel",
        }
    ]


def test_embedding_gate_requires_1024_nonzero_finite_values() -> None:
    assert stage._embedding_is_real("[" + ",".join(["0"] * 1023 + ["0.5"]) + "]")
    assert not stage._embedding_is_real("[" + ",".join(["0"] * 1024) + "]")
    assert not stage._embedding_is_real("[1,2,3]")
    assert not stage._embedding_is_real(None)


def test_bbox_gate_requires_finite_in_image_rectangle() -> None:
    assert stage._valid_bbox(
        {"bbox": [10, 20, 110, 80]},
        width=200,
        height=100,
    )
    assert not stage._valid_bbox({"bbox": []}, width=200, height=100)
    assert not stage._valid_bbox(
        {"bbox": [10, 20, 210, 80]},
        width=200,
        height=100,
    )
    assert not stage._valid_bbox(
        {"bbox": [10, 20, float("nan"), 80]},
        width=200,
        height=100,
    )


def test_formal_ssh_tunnel_requires_exact_host_and_proof_forwards() -> None:
    command = (
        "/usr/bin/ssh -f -N -o ExitOnForwardFailure=yes "
        "-L 14000:127.0.0.1:4000 "
        "-L 18001:127.0.0.1:8001 "
        "-L 18002:127.0.0.1:8002 "
        "-L 18003:127.0.0.1:8003 "
        "-L 18004:127.0.0.1:8004 "
        "-L 18005:127.0.0.1:8005 "
        "-L 19393:127.0.0.1:9393 "
        "radeon-cloud"
    )
    assert stage._ssh_tunnel_command_matches(command)
    assert not stage._ssh_tunnel_command_matches(
        command.replace("radeon-cloud", "some-other-host")
    )
    assert not stage._ssh_tunnel_command_matches(
        command.replace("-L 19393:127.0.0.1:9393 ", "")
    )
    assert not stage._ssh_tunnel_command_matches(
        command.replace("-L 18003:127.0.0.1:8003 ", "")
    )
    assert not stage._ssh_tunnel_command_matches(command.replace("-N ", ""))


def test_software_disconnect_terminates_attested_tunnel_and_clears_cache(
    monkeypatch,
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        monkeypatch.setattr(stage, "_assert_remote_tunnel", lambda: process.pid)
        stage._connectivity_cache = (0.0, {"mode": "radeon"})

        result = stage._disconnect_remote_tunnel()

        assert result == {
            "disconnected": True,
            "method": "verified_ssh_tunnel_termination",
            "tunnel_pid": process.pid,
        }
        assert process.wait(timeout=3) == -15
        assert stage._connectivity_cache is None
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)


def test_stage_assets_disable_browser_caching() -> None:
    client = TestClient(stage.app)
    for path in ("/", "/demo_stage.css", "/demo_stage.js"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
    html = client.get("/").text
    assert 'href="/demo_stage.css?v=' in html
    assert 'src="/demo_stage.js?v=' in html


def test_local_role_identity_requires_llama_alias_and_port() -> None:
    command = (
        "/opt/homebrew/bin/llama-server -m /models/fast.gguf "
        "--alias fast --host 127.0.0.1 --port 8005"
    )
    assert stage._llama_role_command_matches(command, role="fast", port=8005)
    assert not stage._llama_role_command_matches(command, role="brain", port=8005)
    assert not stage._llama_role_command_matches(command, role="fast", port=8001)
    assert not stage._llama_role_command_matches(
        "python fake_server.py --alias fast --port 8005",
        role="fast",
        port=8005,
    )


def test_local_runtime_closes_pidfile_command_and_listener_identity(
    monkeypatch,
) -> None:
    identities = {
        stage.LOCAL_ROLE_IDENTITIES["fast"][0]: (
            501,
            "llama-server -m fast.gguf --alias fast --port 8005",
        ),
        stage.LOCAL_ROLE_IDENTITIES["perceive"][0]: (
            502,
            "llama-server -m perceive.gguf --alias perceive --port 8002",
        ),
        stage.LOCAL_GATEWAY_PIDFILE: (
            503,
            "uvx --from litellm litellm --config server.yaml --port 4000",
        ),
    }
    listeners = {8005: [501], 8002: [502], 4000: [503]}
    monkeypatch.setattr(
        stage,
        "_read_live_pid_command",
        lambda pidfile: identities[pidfile],
    )
    monkeypatch.setattr(stage, "_listener_pids", lambda port: listeners[port])

    proof = stage._assert_local_runtime()
    assert proof["ok"] is True
    assert proof["role_pids"] == {"fast": 501, "perceive": 502}

    listeners[4000] = [999]
    monkeypatch.setattr(
        stage,
        "_pid_is_or_descends_from",
        lambda pid, ancestor: pid == ancestor,
    )
    with pytest.raises(RuntimeError, match="listener :4000"):
        stage._assert_local_runtime()


def test_rocm_metric_gate_requires_success_and_positive_total_vram() -> None:
    payload = (
        "dejaview_rocm_exporter_scrape_success 1\n"
        'dejaview_rocm_gpu_utilization_percent{gpu="card0"} 73\n'
        'dejaview_rocm_vram_used_bytes{gpu="card0"} 12000000000\n'
        'dejaview_rocm_vram_total_bytes{gpu="card0"} 48000000000\n'
    )
    assert stage._metric_present(
        payload,
        "dejaview_rocm_exporter_scrape_success",
        value="1",
    )
    assert stage._metric_numeric_values(
        payload,
        "dejaview_rocm_vram_total_bytes",
    ) == [48000000000.0]
    zero_payload = payload.replace("48000000000", "0")
    assert not any(
        value > 0
        for value in stage._metric_numeric_values(
            zero_payload,
            "dejaview_rocm_vram_total_bytes",
        )
    )


def test_remote_attestation_always_proves_five_roles(monkeypatch) -> None:
    rocm = (
        "dejaview_rocm_exporter_scrape_success 1\n"
        'dejaview_rocm_gpu_utilization_percent{gpu="card0"} 7\n'
        'dejaview_rocm_vram_used_bytes{gpu="card0"} 12000000000\n'
        'dejaview_rocm_vram_total_bytes{gpu="card0"} 48000000000\n'
    )
    llama = "llamacpp:requests_processing 0\nllamacpp:predicted_tokens_seconds 42\n"
    calls: list[str] = []

    role_by_port = {
        18001: "brain",
        18002: "perceive",
        18003: "sentinel",
        18004: "embed",
        18005: "fast",
    }

    class Response:
        def __init__(self, text: str = "", payload: dict | None = None) -> None:
            self.text = text
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            assert self._payload is not None
            return self._payload

    class Client:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str) -> Response:
            calls.append(url)
            if ":19393/" in url:
                return Response(rocm)
            if url.endswith("/v1/models"):
                if ":14000/" in url:
                    ids = list(role_by_port.values())
                else:
                    port = int(url.split(":")[2].split("/")[0])
                    ids = [role_by_port[port]]
                return Response(payload={"data": [{"id": value} for value in ids]})
            return Response(llama)

    monkeypatch.setattr(stage, "_assert_remote_tunnel", lambda: 404)
    monkeypatch.setattr(stage.httpx, "Client", Client)

    proof = stage._assert_remote_runtime(include_brain=False)
    assert proof["model_metrics"] == [
        "brain",
        "embed",
        "fast",
        "perceive",
        "sentinel",
    ]
    assert proof["gateway_models"] == proof["model_metrics"]
    assert proof["brain_smoke_requested"] is False
    for port in role_by_port:
        assert any(f":{port}/v1/models" in url for url in calls)
        assert any(f":{port}/metrics" in url for url in calls)

    calls.clear()
    proof = stage._assert_remote_runtime(include_brain=True)
    assert proof["model_metrics"] == [
        "brain",
        "embed",
        "fast",
        "perceive",
        "sentinel",
    ]
    assert proof["brain_smoke_requested"] is True


def test_model_identity_gate_rejects_malformed_or_empty_pages() -> None:
    assert stage._model_ids({"data": [{"id": "brain"}]}) == {"brain"}
    with pytest.raises(TypeError, match="data list"):
        stage._model_ids({"models": ["brain"]})
    with pytest.raises(RuntimeError, match="no model ids"):
        stage._model_ids({"data": [{"not_id": "brain"}]})


def test_remote_attestation_rejects_gateway_missing_one_role(monkeypatch) -> None:
    rocm = (
        "dejaview_rocm_exporter_scrape_success 1\n"
        'dejaview_rocm_gpu_utilization_percent{gpu="card0"} 7\n'
        'dejaview_rocm_vram_used_bytes{gpu="card0"} 12000000000\n'
        'dejaview_rocm_vram_total_bytes{gpu="card0"} 48000000000\n'
    )

    class Client:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(stage, "_assert_remote_tunnel", lambda: 404)
    monkeypatch.setattr(stage.httpx, "Client", Client)
    monkeypatch.setattr(stage, "_fetch_metrics", lambda *_args: rocm)
    monkeypatch.setattr(
        stage,
        "_fetch_model_ids",
        lambda _client, url: (
            {"brain", "perceive", "sentinel", "fast"} if ":14000/" in url else {"brain"}
        ),
    )

    with pytest.raises(RuntimeError, match="lacks logical roles: embed"):
        stage._assert_remote_runtime(include_brain=False)


def test_formal_ocr_health_requires_paddleocr() -> None:
    stage._validate_ocrd_health(
        {"status": "ok", "backend": "paddleocr", "engine_loaded": True}
    )
    with pytest.raises(RuntimeError, match="PP-OCRv6"):
        stage._validate_ocrd_health(
            {"status": "ok", "backend": "rapidocr", "engine_loaded": True}
        )
    with pytest.raises(RuntimeError, match="PP-OCRv6"):
        stage._validate_ocrd_health(
            {"status": "error", "backend": "paddleocr", "engine_loaded": True}
        )
    with pytest.raises(RuntimeError, match="warmed"):
        stage._validate_ocrd_health(
            {"status": "ok", "backend": "paddleocr", "engine_loaded": False}
        )


def test_honcho_snapshot_requires_one_session_empty_card_and_disabled_config() -> None:
    stage._validate_honcho_isolation(
        sessions_page={"items": [{"id": stage.HONCHO_SESSION}], "total": 1},
        peer_card_page={"peer_card": None},
        workspace={
            "configuration": {
                "peer_card": {"use": False, "create": False},
            }
        },
    )
    with pytest.raises(RuntimeError, match="not isolated"):
        stage._validate_honcho_isolation(
            sessions_page={
                "items": [
                    {"id": stage.HONCHO_SESSION},
                    {"id": "unexpected"},
                ],
                "total": 2,
            },
            peer_card_page={"peer_card": None},
            workspace={
                "configuration": {
                    "peer_card": {"use": False, "create": False},
                }
            },
        )
    with pytest.raises(RuntimeError, match="nonempty global peer card"):
        stage._validate_honcho_isolation(
            sessions_page={"items": [{"id": stage.HONCHO_SESSION}], "total": 1},
            peer_card_page={"peer_card": ["cross-session fact"]},
            workspace={
                "configuration": {
                    "peer_card": {"use": False, "create": False},
                }
            },
        )


def test_honcho_seed_refuses_a_second_session_append() -> None:
    honcho_seed._assert_unseeded({"items": [], "total": 0})
    with pytest.raises(RuntimeError, match="refuses to append"):
        honcho_seed._assert_unseeded(
            {"items": [{"id": honcho_seed.SESSION}], "total": 1}
        )


def test_connectivity_collapses_inflight_requests_and_caches(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    async def fake_probe(url: str, *, include_brain: bool) -> dict[str, bool]:
        calls.append((url, include_brain))
        await asyncio.sleep(0.01)
        return {
            "fast": True,
            "brain": include_brain and url == stage.REMOTE_GATEWAY,
        }

    async def fake_attest(*, include_brain: bool) -> dict:
        return {
            "remote": {"ok": True, "include_brain": include_brain},
            "local": {"ok": True},
        }

    async def scenario() -> None:
        stage._connectivity_cache = None
        monkeypatch.setattr(stage, "_gateway_models_ready", fake_probe)
        monkeypatch.setattr(stage, "_runtime_attestations", fake_attest)
        first, second = await asyncio.gather(
            stage._connectivity(),
            stage._connectivity(),
        )
        cached = await stage._connectivity()
        assert first == second == cached
        assert first["mode"] == "radeon"
        assert first["daily_mode"] == "unchecked"
        daily = await stage._connectivity(force=True, include_daily=True)
        assert daily["daily_mode"] == "radeon"

    asyncio.run(scenario())
    assert calls.count((stage.REMOTE_GATEWAY, False)) == 1
    assert calls.count((stage.LOCAL_GATEWAY, False)) == 1
    assert calls.count((stage.REMOTE_GATEWAY, True)) == 1
    assert calls.count((stage.LOCAL_GATEWAY, True)) == 1
    stage._connectivity_cache = None


def test_connectivity_never_routes_to_an_unattested_backend(monkeypatch) -> None:
    async def fake_probe(url: str, *, include_brain: bool) -> dict[str, bool]:
        return {"fast": True, "brain": include_brain}

    async def fake_attest(*, include_brain: bool) -> dict:
        return {
            "remote": {"ok": False, "error": "not an SSH tunnel"},
            "local": {"ok": True, "include_brain": include_brain},
        }

    async def scenario() -> None:
        stage._connectivity_cache = None
        monkeypatch.setattr(stage, "_gateway_models_ready", fake_probe)
        monkeypatch.setattr(stage, "_runtime_attestations", fake_attest)
        state = await stage._connectivity(force=True, include_daily=True)
        assert state["remote_radeon"] is False
        assert state["remote_daily_ready"] is False
        assert state["local_metal"] is True
        assert state["local_daily_ready"] is True
        assert state["daily_mode"] == "local_fallback"

    asyncio.run(scenario())
    stage._connectivity_cache = None


def test_process_output_budget_can_terminate_a_stalled_backend() -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; print('started', flush=True); time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        with pytest.raises(TimeoutError, match="backend budget"):
            list(stage._iter_process_output(process, timeout_seconds=0.1))
    finally:
        stage._terminate_process(process)
    assert process.poll() is not None
