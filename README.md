# DejaView

A fully local "digital memory" system built for the AMD AI DevMaster Hackathon (Track 2: Agentic AI).

It continuously perceives your screen and voice, filters what should never be remembered through a
privacy sentinel model, extracts verbatim text with deterministic OCR, builds a psychological user
model with Honcho, and answers questions with screenshot evidence — with 100% of AI inference running
on an AMD Radeon PRO W7900D (ROCm). Your data never leaves your own devices.

> Development repository (private). Product codename: **DejaView**.

## Where to start

- `docs/EXECUTION_HANDBOOK.md` — the single source of truth: architecture, specs, task breakdown, acceptance criteria.
- `TASKBOARD.json` — live task states (`false → doing → accept`); the execution queue for agents.
- `docs/AGENT_KICKOFF_PROMPT.md` — the standing instruction given to executor agents.
- `docs/model-manifest.md` — model weights inventory (weights live on the compute server, not in git).

## Layout

```
docs/            handbook, kickoff prompt, manifests, verification log, benchmarks
deploy/server/   GPU-side: model bootstrap, llama.cpp launch configs, gateway, bench scripts
deploy/mac/      data-side: postgres/redis compose, timeline schema
services/        memoryd (ingest pipeline) · ocrd (deterministic OCR) · agentd (brain agent)
clients/capture/ cross-platform screen/audio capture client (macOS / Windows)
third_party/     pinned Honcho fork (submodule)
tests/assets/    synthetic test fixtures (screenshots, messages, sentinel set)
```
