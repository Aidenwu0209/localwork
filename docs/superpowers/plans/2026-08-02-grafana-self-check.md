# Grafana System Self-Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an honest active self-check to the accepted one-screen Grafana dashboard while preserving every P3.2 ROCm gate.

**Architecture:** A standard-library Python exporter performs cached active probes against the local sovereignty stack and the two compute paths. Prometheus scrapes its low-cardinality metrics, and the provisioned Grafana JSON derives readable READY/DEGRADED/FAILED cards while retaining the existing fail-closed ROCm and model panels.

**Tech Stack:** Python 3.12 standard library, Prometheus 3.2, PromQL, Grafana 11.4 provisioned JSON, Docker Compose, `unittest`.

## Global Constraints

- Preserve the DejaView digital-memory narrative and the five logical model names `brain`, `perceive`, `sentinel`, `fast`, and `embed`.
- Never read `.env` files, credentials, timeline contents, screenshots, or real PII.
- The AMD server remains stateless; all monitoring services bind locally.
- Local fallback inference must send `chat_template_kwargs.enable_thinking=false`.
- No-data may not be mapped to green.
- Existing P3.2 ROCm exporter, one-GPU, four-role health, and four-role positive-throughput expressions remain exact.
- Commit author must be `Aidenwu0209 <1418557225@qq.com>` with no trailers.

---

### Task 1: Active self-check exporter

**Files:**
- Create: `deploy/mac/monitoring/health_exporter.py`
- Create: `deploy/mac/monitoring/test_health_exporter.py`

**Interfaces:**
- Produces: `ProbeSpec`, `ProbeResult`, `SelfCheck.run_cycle()`, `SelfCheck.render_metrics()`, and an HTTP server exposing `/health` and `/metrics` on port `9400`.
- Metrics: `dejaview_component_up`, `dejaview_component_latency_seconds`, `dejaview_component_last_success_unixtime`, `dejaview_selfcheck_last_probe_unixtime`, `dejaview_selfcheck_state`, and `dejaview_compute_path_state`.

- [ ] **Step 1: Write failing state-machine and probe tests**

```python
def test_ready_requires_local_core_and_radeon():
    results = healthy_local_results() | {
        "radeon_tunnel": ProbeResult(True, 0.01),
        "local_fallback": ProbeResult(False, 0.01),
    }
    assert derive_states(results) == (2, 2)

def test_local_fallback_request_disables_thinking():
    request = build_local_fallback_request()
    assert request["chat_template_kwargs"] == {"enable_thinking": False}
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest deploy/mac/monitoring/test_health_exporter.py -v`

Expected: import failure because `health_exporter.py` does not exist.

- [ ] **Step 3: Implement the minimal exporter**

```python
@dataclass(frozen=True)
class ProbeResult:
    healthy: bool
    latency_seconds: float
    detail: str = ""

def derive_states(results: Mapping[str, ProbeResult]) -> tuple[int, int]:
    core = all(results[name].healthy for name in LOCAL_CORE)
    remote = results["radeon_tunnel"].healthy
    local = results["local_fallback"].healthy
    compute = 2 if remote else 1 if local else 0
    return (2 if core and remote else 1 if core and local else 0, compute)
```

Use `socket.create_connection` for TCP probes, `urllib.request` for JSON HTTP
probes, a 30-second cache for the Local Metal inference, and
`ThreadingHTTPServer` for the exporter.

- [ ] **Step 4: Run the exporter tests and verify GREEN**

Run: `python3 -m unittest deploy/mac/monitoring/test_health_exporter.py -v`

Expected: all probe, cache, escaping, and state-machine tests pass.

### Task 2: Compose and Prometheus wiring

**Files:**
- Modify: `deploy/mac/compose.monitoring.yml`
- Modify: `deploy/mac/monitoring/prometheus.yml`
- Modify: `deploy/mac/monitoring/test_monitoring_contract.py`

**Interfaces:**
- Consumes: exporter `/health` and `/metrics` from Task 1.
- Produces: compose service `health-exporter` and Prometheus job `dejaview-selfcheck`.

- [ ] **Step 1: Add failing integration-contract assertions**

```python
def test_selfcheck_exporter_is_wired_and_scraped(self):
    rendered = json.loads(subprocess.run(
        ["docker", "compose", "-f", COMPOSE_PATH, "config", "--format", "json"],
        check=True, capture_output=True, text=True,
    ).stdout)
    self.assertIn("health-exporter", rendered["services"])
    self.assertIn('targets: ["health-exporter:9400"]', self.prometheus)
```

- [ ] **Step 2: Run the contract test and verify RED**

Run: `python3 deploy/mac/monitoring/test_monitoring_contract.py`

Expected: failure because the service and scrape target are absent.

- [ ] **Step 3: Add the service and scrape job**

```yaml
health-exporter:
  image: python:3.12-alpine
  command: ["python", "/app/health_exporter.py", "--host", "host.docker.internal"]
  volumes:
    - ./monitoring/health_exporter.py:/app/health_exporter.py:ro
  healthcheck:
    test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:9400/health | grep -q '\"status\": \"ok\"'"]
```

Prometheus job:

```yaml
- job_name: dejaview-selfcheck
  static_configs:
    - targets: ["health-exporter:9400"]
```

- [ ] **Step 4: Verify compose and contract GREEN**

Run: `docker compose -f deploy/mac/compose.monitoring.yml config --quiet`

Run: `python3 deploy/mac/monitoring/test_monitoring_contract.py`

Expected: both commands exit zero.

### Task 3: Self-check dashboard and honest no-data states

**Files:**
- Modify: `deploy/mac/monitoring/grafana/dashboards/dejaview-rocm-live.json`
- Modify: `deploy/mac/monitoring/test_monitoring_contract.py`

**Interfaces:**
- Consumes: Task 1 metrics plus the accepted ROCm and llama.cpp metrics.
- Produces: first-row panels `系统状态`, `数据新鲜度`, `本机核心`, and `算力路径`.

- [ ] **Step 1: Add failing dashboard behavior assertions**

```python
def test_dashboard_has_system_selfcheck_row(self):
    self.assertEqual(self.panels["系统状态"]["gridPos"], {"h": 4, "w": 6, "x": 0, "y": 0})
    self.assertIn("dejaview_selfcheck_state", self.panels["系统状态"]["targets"][0]["expr"])
    self.assertEqual(self.panels["数据新鲜度"]["fieldConfig"]["defaults"]["noValue"], "STALE")

def test_no_data_is_never_green(self):
    for title in ("GPU", "VRAM", "ROCm采集", "GPU数量", "常驻模型", "活跃吞吐"):
        self.assertNotEqual(self.panels[title]["fieldConfig"]["defaults"].get("noValue"), "OK")
```

- [ ] **Step 2: Run and verify RED**

Run: `python3 deploy/mac/monitoring/test_monitoring_contract.py`

Expected: missing-panel failures for the new self-check row.

- [ ] **Step 3: Modify the provisioned dashboard JSON**

Add the four self-check stat panels at `y=0`; move the six short ROCm gates to
`y=4`; move existing charts down by four grid units. Use literal value mappings
for state and compute-path cards and threshold colors for freshness and counts.
Set GPU/VRAM no-data text to `NO DATA` with gray/red base color. Preserve the
four accepted expressions verbatim.

- [ ] **Step 4: Run and verify GREEN**

Run: `python3 deploy/mac/monitoring/test_monitoring_contract.py`

Expected: all monitoring contract tests pass.

### Task 4: Live verification, documentation, and acceptance

**Files:**
- Modify: `deploy/mac/monitoring/README.md`
- Modify: `docs/verification-log.md`
- Modify: `TASKBOARD.json`
- Create: `docs/assets/p32/grafana-selfcheck-20260802.png`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: a reproducible operator workflow, live screenshot, verification log,
  and accepted `P3.11` state.

- [ ] **Step 1: Recreate the monitoring stack**

Run: `docker compose -f deploy/mac/compose.monitoring.yml up -d --wait --force-recreate`

Expected: `health-exporter`, Prometheus, and Grafana report healthy.

- [ ] **Step 2: Verify fail-closed behavior before restoration**

Run: `curl -fsS http://127.0.0.1:9090/api/v1/query --data-urlencode 'query=dejaview_selfcheck_state'`

Expected: current missing services/tunnel produce `0` or `1`, never `2`.

- [ ] **Step 3: Restore the formal metric tunnel and required services**

Use the exact attested forwards from `deploy/mac/monitoring/README.md`. Before
starting a duplicate process, resolve each listener and PID; keep
`enable_thinking=false` for fast-track smoke calls.

- [ ] **Step 4: Verify live READY evidence**

Run the exporter, GPU-count, four-role health, and four-role positive-throughput
Prometheus queries from the README plus `dejaview_selfcheck_state` and
telemetry-age queries.

Expected: state `2`, exporter `1`, GPU count `1`, required roles `4`, positive
throughput roles `4`, and telemetry age `≤15` seconds.

- [ ] **Step 5: Capture and inspect the 1280×720 dashboard**

Save `docs/assets/p32/grafana-selfcheck-20260802.png`; confirm titles are not
truncated, no-data is not green, and the four accepted charts remain visible.

- [ ] **Step 6: Run the full monitoring verification**

Run: `python3 -m unittest discover -s deploy/mac/monitoring -p 'test_*.py' -v`

Run: `docker compose -f deploy/mac/compose.monitoring.yml config --quiet`

Run: `git diff --check`

Expected: zero failures and zero whitespace errors.

- [ ] **Step 7: Record verification and accept the task**

Append a `[VERIFY] P3.11` section to `docs/verification-log.md`, set P3.11 to
`accept`, and include the screenshot and metric results in its note.

- [ ] **Step 8: Commit, inspect trailers, and push**

```bash
git add TASKBOARD.json deploy/mac/compose.monitoring.yml deploy/mac/monitoring \
  docs/superpowers docs/verification-log.md docs/assets/p32/grafana-selfcheck-20260802.png
git commit -m "P3.11: add Grafana system self-check"
git log -1 --format='%an <%ae>%n%B'
git push origin main
```

Expected author: `Aidenwu0209 <1418557225@qq.com>`; message contains no trailer.

