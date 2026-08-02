# DejaView ROCm live dashboard

This stack is local-only: Grafana and Prometheus bind to loopback, llama.cpp and
`rocm-smi` stay on the AMD server, and memoryd stays on the Mac.

## 1. Enable real exporters

The five server launchers include llama.cpp `--metrics`. Sync those launchers
and the exporter before starting the view:

```bash
cd /Users/wu/Projects/Aidenwu0209/localwork
ssh radeon-cloud 'mkdir -p /root/dejaview-launch/monitoring'
rsync -a deploy/server/llama-launch/ radeon-cloud:/root/dejaview-launch/
rsync -a deploy/server/monitoring/ radeon-cloud:/root/dejaview-launch/monitoring/
```

On the AMD server, inventory the GPU and the existing DejaView PIDs first.
Restart only the four named DejaView roles so already-running processes pick up
`--metrics`; do not use a broad `pkill` and do not touch another KFD process:

```bash
rocm-smi --showmeminfo vram --showuse
rocm-smi --showpids verbose
cd /root/dejaview-launch
./server-stack.sh status
for role in embed fast sentinel perceive; do
  pidfile="/tmp/dejaview-$role.pid"
  [[ -r "$pidfile" ]] || continue
  pid="$(cat "$pidfile")"
  kill -0 "$pid" 2>/dev/null || continue
  command_line="$(tr '\0' ' ' <"/proc/$pid/cmdline")"
  if [[ "$command_line" != *"llama-server"* ||
        "$command_line" != *"--alias $role"* ]]; then
    echo "$pidfile points to an unrelated PID $pid; abort" >&2
    exit 1
  fi
  ps -fp "$pid"
done
./server-stack.sh down embed fast sentinel perceive
./server-stack.sh up embed fast sentinel perceive
if ss -H -ltnp 'sport = :9393' | grep -q .; then
  echo "Exporter port :9393 already occupied; verify it before continuing"
  ss -H -ltnp 'sport = :9393'
else
  python3 monitoring/rocm_smi_exporter.py \
    >/tmp/dejaview-rocm-exporter.log 2>&1 &
  exporter_pid=$!
  sleep 1
  if ! kill -0 "$exporter_pid" 2>/dev/null; then
    tail -50 /tmp/dejaview-rocm-exporter.log >&2
    exit 1
  fi
  echo "$exporter_pid" >/tmp/dejaview-rocm-exporter.pid
fi
```

When shared VRAM allows the brain role, stop perceive first and use the exact
Q6_K file:

```bash
./server-stack.sh down perceive
BRAIN_QUANT=Q6_K ./server-stack.sh up brain
```

## 2. Forward loopback metrics to the Mac

```bash
ssh -f -N \
  -o ExitOnForwardFailure=yes \
  -L 18001:127.0.0.1:8001 \
  -L 18002:127.0.0.1:8002 \
  -L 18003:127.0.0.1:8003 \
  -L 18004:127.0.0.1:8004 \
  -L 18005:127.0.0.1:8005 \
  -L 19393:127.0.0.1:9393 \
  radeon-cloud
```

memoryd exposes `/metrics` on its existing loopback port 8090. After syncing
this revision, restart only the verified `python -m memoryd` process using the
same environment shown in `STATUS.md`; record the new PID so a later restart is
targeted:

```bash
pgrep -af 'python -m memoryd'
# After visually verifying the DejaView PID:
kill <verified-memoryd-pid>
MEMORYD_REAL_PIPELINE=1 GATEWAY_URL=http://127.0.0.1:14000/v1 \
  nohup uv run --project services/memoryd python -m memoryd \
  >/tmp/dejaview-memoryd.log 2>&1 &
echo $! >/tmp/dejaview-memoryd.pid
curl -fsS http://127.0.0.1:8090/metrics | head
```

## 3. Open the provisioned one-screen view

```bash
make monitoring-up
open http://127.0.0.1:3000/d/dejaview-rocm-live/dejaview-radeon-rocm-live
```

The dashboard refreshes every five seconds and shows:

- the ROCm exporter's own `dejaview_rocm_exporter_scrape_success` value, not
  merely a successful HTTP response;
- exactly one Radeon GPU series (`GPU series count · must be 1`);
- a `4/4` health gate and a separate `4/4` positive tokens/s gate for the
  required `perceive`, `sentinel`, `embed`, and `fast` roles;
- llama.cpp decode and prefill tokens/s for each logical model role;
- live Radeon GPU utilization and VRAM use;
- new timeline events/min and ingest outcomes;
- active/deferred request pressure. The `brain` role remains optional and is
  intentionally excluded from the four-role gates.

If a tunnel or exporter is down, the corresponding gate is red (or shows no
data for non-gated charts) rather than fabricating a healthy value. Stop the
local view with `make monitoring-down`.

## P3.2 acceptance gate

`make monitoring-up` now waits for Prometheus `/-/ready` and Grafana's database
health, rather than only waiting for their containers to start. That confirms
the local view is running; it does **not** prove the forwarded AMD metrics are
valid. Before accepting a live capture, query Prometheus and retain the results
with the dashboard screenshot:

```bash
curl -fsSG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=min(dejaview_rocm_exporter_scrape_success{job="rocm"} or on (job, instance) (0 * up{job="rocm"}))'
curl -fsSG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=count(dejaview_rocm_gpu_utilization_percent{job="rocm"}) or vector(0)'
curl -fsSG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=sum(up{job="llama",role=~"perceive|sentinel|embed|fast"} or vector(0))'
curl -fsSG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=count(max by (role) ((llamacpp:predicted_tokens_seconds{job="llama",role=~"perceive|sentinel|embed|fast"} > 0) or (llamacpp:prompt_tokens_seconds{job="llama",role=~"perceive|sentinel|embed|fast"} > 0))) or vector(0)'
```

The four values must be `1`, `1`, `4`, and `4`, respectively. Also retain live
GPU/VRAM values and a non-zero memory-pipeline rate; a `200` from `/metrics`
alone is not evidence of ROCm exporter health.
