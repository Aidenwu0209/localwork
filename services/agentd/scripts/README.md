# P3.4 synthetic demo helpers

These helpers make the six-act video auditable without touching real user
memory. They require the separate `dejaview_demo` database, a dedicated
`/tmp/dejaview-p34-data` screenshot root, synthetic device ids, and a stopped
capture client. The stage refuses to start if any guard fails.

Set the explicit demo guard for every helper/service shell:

```bash
export DEJAVIEW_DEMO_MODE=1
export TIMELINE_DB_URL=postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview_demo
export DATA_ROOT=/tmp/dejaview-p34-data
export HONCHO_URL=http://127.0.0.1:8100
export OCR_BACKEND=paddleocr
```

With the isolated data layer up, seed only the historical synthetic PR:

```bash
uv run --project services/agentd python services/agentd/scripts/seed_demo_p34.py
```

The PR timestamp is computed as the previous week's Wednesday, so the spoken
question remains true on recording day. No current-day activity or Sentinel
result is prewritten. Act 2 creates today's events live through memoryd.

```bash
uv run --project services/agentd python services/agentd/scripts/render_evidence.py \
  --event-id <PR_EVENT_ID> \
  --highlight-text 'PR #1842' \
  --output docs/assets/demo/act-04-evidence.png
```

With a gateway serving logical names `fast` and `brain`, the visible four-stage
daily report can also be exercised directly:

```bash
GATEWAY_URL=http://127.0.0.1:4000/v1 \
uv run --project services/agentd python \
  services/agentd/scripts/demo_daily_report.py
```

The Planner and Reviewer use the fast logical role with thinking disabled; the
Writer uses brain; Retriever reads only `demo-p34` rows. The deterministic
review checks every citation id, time, and app and rejects any uncited factual
line before the independent model Reviewer sees the report.

These scripts do not fake the privacy-sentinel act. Act 3 must still run the
real memoryd pipeline against `tests/assets/sentinel/banking_01.png` and show
the resulting `sentinel_audit` row while confirming no timeline event or image
was stored.

## Isolated six-act recording stage

Before formal recording, stop capture, stage, memoryd, agentd, and Honcho.
Confirm no `python -m capture` process remains. Then switch databases:

```bash
docker compose -f deploy/mac/compose.honcho.yml down
make data-down
make demo-data-reset
mkdir -p /tmp/dejaview-p34-data
find /tmp/dejaview-p34-data -mindepth 1 -delete
uv run --project services/agentd python services/agentd/scripts/seed_demo_p34.py
```

The isolated Postgres volume is new after every reset, so configure Honcho's
empty pgvector columns for DejaView's fixed 1,024-dimensional embed model
before starting its API. The P3.4-only override makes the deliberately small
synthetic message set flush immediately instead of waiting for Honcho's
production 1,024-token batch:

```bash
docker compose -f deploy/mac/compose.honcho.yml \
  run --rm --no-deps \
  --entrypoint /app/.venv/bin/alembic \
  honcho-api upgrade head

docker compose -f deploy/mac/compose.honcho.yml \
  run --rm --no-deps \
  --entrypoint /app/.venv/bin/python \
  honcho-api scripts/configure_embeddings.py --yes

docker compose \
  -f deploy/mac/compose.honcho.yml \
  -f deploy/mac/compose.honcho-demo.yml \
  up -d --wait
```

Sync the metric-enabled launchers/exporter, then prove the replacement Radeon
instance is exclusive before loading any model. `rocm-smi` must show the
expected W7900, sane free VRAM, and no unknown live GPU tenant. Stop and
investigate instead of risking OOM if either check is ambiguous. On the
exclusive instance start the complete five-role pyramid for Act 1: `brain`
uses Q6_K and MTP stays off (the launcher must not be given
`--spec-type draft-mtp`). Keep all five roles resident for the recording.

```bash
# Mac, from the repository root
ssh radeon-cloud 'mkdir -p /root/dejaview-launch/monitoring'
rsync -a deploy/server/llama-launch/ radeon-cloud:/root/dejaview-launch/
rsync -a deploy/server/monitoring/ radeon-cloud:/root/dejaview-launch/monitoring/

# Radeon host
ssh radeon-cloud
rocm-smi
rocm-smi --showpids
cd /root/dejaview-launch
BRAIN_QUANT=Q6_K ./server-stack.sh up embed fast sentinel perceive brain
./server-stack.sh status
rocm-smi --showmeminfo vram
if ss -H -ltnp 'sport = :9393' | grep -q .; then
  curl -fsS http://127.0.0.1:9393/metrics |
    grep -q '^dejaview_rocm_exporter_scrape_success 1' ||
    { echo "existing :9393 is not a healthy ROCm exporter" >&2; exit 1; }
else
  python3 monitoring/rocm_smi_exporter.py \
    >/tmp/dejaview-rocm-exporter.log 2>&1 &
  exporter_pid=$!
  sleep 1
  kill -0 "$exporter_pid" 2>/dev/null ||
    { tail -50 /tmp/dejaview-rocm-exporter.log >&2; exit 1; }
  echo "$exporter_pid" >/tmp/dejaview-rocm-exporter.pid
fi
exit
```

Use one exact formal tunnel for the gateway and its proof endpoints. If any
listed local proof port is already occupied, inspect it and stop only a
previously verified DejaView SSH tunnel before continuing:

```bash
for port in 14000 18001 18002 18003 18004 18005 19393; do
  lsof -nP -iTCP:"$port" -sTCP:LISTEN
done
```

```bash
ssh -f -N \
  -o ExitOnForwardFailure=yes \
  -o ControlMaster=no \
  -o ControlPath=none \
  -L 14000:127.0.0.1:4000 \
  -L 18001:127.0.0.1:8001 \
  -L 18002:127.0.0.1:8002 \
  -L 18003:127.0.0.1:8003 \
  -L 18004:127.0.0.1:8004 \
  -L 18005:127.0.0.1:8005 \
  -L 19393:127.0.0.1:9393 \
  radeon-cloud

# Pre-start the independently verified Local Metal fallback.
./deploy/mac/llama-launch/dev-stack.sh up fast perceive sentinel
```

The stage refuses to start unless `lsof` + `ps` prove that the `:14000`
listener is that exact `radeon-cloud` SSH command with every proof forward, the
ROCm exporter reports a successful scrape with positive total VRAM, the
gateway `/v1/models` lists `brain`, `perceive`, `sentinel`, `fast`, and `embed`,
and every role's dedicated `/v1/models` identity plus llama.cpp `/metrics`
endpoint is live. This is the fail-closed Act 1 proof that the five-model
pyramid is real, not merely a responsive gateway. The local route is accepted
only when the dev-stack fast/perceive/gateway pidfiles, their command lines,
and their listeners all agree; locally, `brain` is deliberately mapped to the
same on-device perceive model.

Start each local service from the repository root in its own terminal. The
production memory pipeline is real by default; only the explicit
`MEMORYD_ALLOW_STUB_PIPELINE=true` test opt-in enables stubs, and that mode
rejects frames. Keep the raw-frame Sentinel on local `:4000`; only frames it
allows may continue through the Radeon compute tunnel.

```bash
# Terminal A — deterministic PP-OCRv6 service (formal take; never rapidocr)
OCR_BACKEND=paddleocr uv run --project services/ocrd python -m ocrd

# Terminal B — real ingest pipeline routed through the Radeon tunnel
DEJAVIEW_DEMO_MODE=1 \
TIMELINE_DB_URL=postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview_demo \
DATA_ROOT=/tmp/dejaview-p34-data \
SENTINEL_GATEWAY_URL=http://127.0.0.1:4000/v1 \
GATEWAY_URL=http://127.0.0.1:14000/v1 \
OCR_URL=http://127.0.0.1:8006 \
uv run --project services/memoryd python -m memoryd

# Terminal C — grounded recall, also routed through the Radeon tunnel
DEJAVIEW_DEMO_MODE=1 \
TIMELINE_DB_URL=postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview_demo \
DATA_ROOT=/tmp/dejaview-p34-data \
HONCHO_URL=http://127.0.0.1:8100 \
GATEWAY_URL=http://127.0.0.1:14000/v1 \
uv run --project services/agentd python -m agentd

# Terminal D — isolated Honcho API + immediate-flush demo deriver
docker compose \
  -f deploy/mac/compose.honcho.yml \
  -f deploy/mac/compose.honcho-demo.yml \
  up -d --wait
```

Confirm the identities before continuing:

```bash
curl -fsS http://127.0.0.1:8090/health | python3 -m json.tool
curl -fsS http://127.0.0.1:8101/health | python3 -m json.tool
```

Both services must report gateway origin `http://127.0.0.1:14000`, database
`dejaview_demo`, and data root `/tmp/dejaview-p34-data`; memoryd must additionally
report `pipeline: real`.

Warm PP-OCRv6 before starting the stage so model initialization is outside the
five-minute take, then verify the live engine rather than trusting an
environment string:

```bash
curl -fsS \
  -F 'file=@tests/assets/sentinel/normal_code_01.png;type=image/png' \
  http://127.0.0.1:8006/ocr |
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["backend"] == "paddleocr"'
curl -fsS http://127.0.0.1:8006/health |
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert d == {"status":"ok","backend":"paddleocr","engine_loaded":True}'
```

Seed the dedicated `dejaview-p34` / `demo-owner` Honcho profile only after the
API and deriver are healthy:

```bash
DEJAVIEW_DEMO_MODE=1 \
TIMELINE_DB_URL=postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview_demo \
HONCHO_URL=http://127.0.0.1:8100 \
uv run --project services/agentd \
  python services/agentd/scripts/seed_honcho_p34.py
```

The seed intentionally refuses a second run instead of duplicating synthetic
messages. If seeding was interrupted, restart from `make demo-data-reset`.

Then run the same-origin local stage:

```bash
# Terminal E
DEJAVIEW_DEMO_MODE=1 \
TIMELINE_DB_URL=postgresql://dejaview:dejaview@127.0.0.1:5433/dejaview_demo \
DATA_ROOT=/tmp/dejaview-p34-data \
HONCHO_URL=http://127.0.0.1:8100 \
uv run --project services/agentd python services/agentd/scripts/demo_stage.py

# Terminal F, after Terminal E reports that :8120 is ready
open http://127.0.0.1:8120
```

The stage startup probes memoryd and refuses to open unless `/health` reports
the real pipeline, database `dejaview_demo`, and the same dedicated demo
`DATA_ROOT`. It also requires a warmed `paddleocr` engine (PP-OCRv6), the exact
attested Radeon SSH/metrics path, and the attested Local Metal dev stack. This
prevents a stub pipeline, RapidOCR rehearsal backend, arbitrary OpenAI-compatible
listener, or differently configured memoryd process from producing a
plausible-looking act.

Act 2 submits three safe synthetic windows through the real
sentinel→OCR→novelty→perceive→embed pipeline. It requires three normal/allow
audits, nonempty OCR text and bboxes, non-stub activities, screenshots inside
the demo root, and nonzero 1024-dimensional embeddings. Act 3 proves exactly
one audit row, zero timeline rows, and zero screenshot files. Before Act 4,
confirm the already-resident Q6_K brain remains healthy; do not stop perceive
or change the five-role Act 1 state:

```bash
./server-stack.sh status
curl -fsS http://127.0.0.1:8001/v1/models | python3 -m json.tool
curl -fsS http://127.0.0.1:8001/metrics | grep '^llamacpp:'
```

Act 4 accepts only answers whose every citation id/time/app resolves to the
isolated timeline and whose cited `1842` text has an OCR bbox. Act 5 requires
at least one real Honcho-derived conclusion scoped to session
`p3-4-synthetic` in the dedicated `dejaview-p34` / `demo-owner` namespace; the
stage also requires that this is the namespace's only session and that its
global peer card is empty. Its user-model chat is scoped to that same session.
Act 6 performs real `fast`+`brain` inference smokes, then streams the real
Planner→Retriever→Writer→Reviewer trace. After the Radeon run, use the visible
`DISCONNECT RADEON LINK` control. Its server-side guard terminates only the
exact SSH process already attested with the gateway, five role proof forwards,
and ROCm exporter forward. Leave Wi-Fi untouched, wait for
`LINK DOWN · LOCAL READY`, and run Act 6 again.

Every fast-track chat request in the stage sends
`chat_template_kwargs.enable_thinking=false`; do not remove this while
rehearsing or recording. The disconnect proof is the real termination of that
verified tunnel, not a CSS-only badge change, disabled network interface, or
firewall simulation. The second Act 6 run is the Radeon-safe recovery beat:
the remote brain route disappears and the independently attested Local Metal
mapping finishes the same grounded report without changing the digital-memory
story.

After recording, stop stage, agentd, memoryd, and Honcho before restoring the
regular database. Then stop the isolated stack and restore the preserved data:

```bash
docker compose \
  -f deploy/mac/compose.honcho.yml \
  -f deploy/mac/compose.honcho-demo.yml \
  down
make demo-data-down
make data-up
```

If another take is required before teardown, recreate the exact formal SSH
tunnel from the command earlier in this document, reload the stage, and confirm
`RADEON ROCm · ONLINE` before recording again.
