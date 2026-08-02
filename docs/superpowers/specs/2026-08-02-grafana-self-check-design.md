# Grafana System Self-Check Design

## Objective

Turn the existing P3.2 ROCm recording dashboard into an honest operational
self-check without removing any accepted competition metric. A judge or user
must be able to tell, in one glance, whether DejaView is ready, degraded, or
failed, which layer is responsible, and whether the displayed telemetry is
fresh.

## Scope

The change is limited to the local monitoring stack:

- `deploy/mac/monitoring/health_exporter.py` actively checks DejaView's local
  services and compute paths and exports low-cardinality Prometheus metrics.
- Prometheus scrapes that exporter in addition to the accepted ROCm, llama.cpp,
  and memoryd targets.
- Grafana adds a concise self-check row and shortens the accepted gate titles.
- Existing throughput, GPU, VRAM, request pressure, and event-rate charts stay
  on the same one-screen dashboard.

The six-act demo page and five-model architecture are unchanged.

## Information hierarchy

The dashboard is reorganized into three levels:

1. **System self-check** — overall state, freshness, local core, and compute
   path. These are the first four large cards.
2. **Accepted ROCm gates** — GPU, VRAM, exporter validity, GPU count, four
   required roles, and positive throughput. These remain fail-closed.
3. **Diagnostic trends** — model tokens/s, memory outcomes, Radeon pressure,
   and request pressure.

Short Chinese labels are used on the status cards so they remain readable at
the 1280×720 recording viewport. Panel descriptions retain the precise English
metric names where useful.

## Active probes

The self-check exporter uses only Python's standard library and runs inside the
monitoring compose stack. It probes `host.docker.internal` and never reads
credentials, user memories, screenshots, or environment secret files.

| Component | Probe | Healthy contract |
| --- | --- | --- |
| database | TCP `:5433` | connection succeeds |
| redis | TCP `:6380` | connection succeeds |
| honcho | GET `:8100/health` | JSON `status=ok` |
| ocrd | GET `:8006/health` | JSON `status=ok`, backend is reported |
| memoryd | GET `:8090/health` | JSON `status=ok` and `pipeline=real` |
| agentd | GET `:8101/health` | JSON `status=ok`, `service=agentd` |
| radeon_tunnel | GET `:14000/v1/models` | all five logical names present |
| local_fallback | POST `:4000/v1/chat/completions` | `fast` returns `OK`; `enable_thinking=false` |

The live Local Metal inference probe is cached for 30 seconds so Prometheus's
five-second scrape does not create continuous GPU work.

## Metrics

The exporter exposes:

- `dejaview_component_up{component,layer}` — `0` or `1` for each probe.
- `dejaview_component_latency_seconds{component,layer}` — latest probe latency.
- `dejaview_component_last_success_unixtime{component,layer}` — last successful
  active probe, or `0` when none has succeeded since exporter startup.
- `dejaview_selfcheck_last_probe_unixtime` — timestamp of the latest completed
  probe cycle.
- `dejaview_selfcheck_state` — `2=READY`, `1=DEGRADED`, `0=FAILED`.
- `dejaview_compute_path_state` — `2=RADEON`, `1=LOCAL`, `0=NONE`.

`READY` requires all six local-core components and the Radeon tunnel.
`DEGRADED` requires all six local-core components and a verified Local Metal
fallback while Radeon is unavailable. Missing local-core services or both
compute paths means `FAILED`.

ROCm exporter validity and the four required llama.cpp role gates remain
separate accepted evidence. The dashboard must not turn a successful HTTP
response or `/v1/models` registration into a fake ROCm-green gate.

## Visual and failure-state rules

- Overall mappings: `READY` green, `DEGRADED` amber, `FAILED` red.
- No-data is gray or red; it is never green.
- Telemetry age is green at `≤15s`, amber at `15–30s`, and red above `30s`.
- Local core displays `6/6`, `DEGRADED (<6)`, or `NO DATA`.
- Compute path displays `RADEON`, `LOCAL FALLBACK`, or `NONE`.
- Existing accepted gates retain their exact PromQL semantics.
- The dashboard refresh interval remains five seconds and time range remains
  the last five minutes.

## Verification

Acceptance requires:

1. Unit tests prove every probe branch and the READY/DEGRADED/FAILED state
   machine, including the thinking-disabled Local Metal request.
2. The monitoring contract test proves compose wiring, Prometheus scrape
   configuration, status mappings, no-data colors, layout, and preservation of
   all four accepted P3.2 gates.
3. `docker compose config` succeeds and all three monitoring services become
   healthy.
4. With the current intentionally incomplete local stack, the dashboard shows
   the corresponding red/degraded states rather than green.
5. After the formal tunnel and services are restored, the dashboard can reach
   READY and a fresh 1280×720 screenshot is retained.

