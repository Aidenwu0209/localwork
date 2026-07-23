# Third-party licenses & submission checklist notes

> DejaView / 全本地数字记忆体 · P3.5 · 2026-07-23  
> Weights are **not** in git (see [`model-manifest.md`](model-manifest.md)); this file records license facts for models, engines, and libraries we actually use.

---

## User data never leaves the device

- Screenshots, OCR text, timeline events, Honcho peer memory, audit logs, and `DATA_ROOT` artifacts live only on the **user-owned data plane** (Mac / single-box host).
- The AMD compute node runs **stateless inference** over a local/tunnel gateway (`GATEWAY_URL`). Prompt/image payloads for inference are not retained as a user memory store on the GPU host.
- Capture client: in-memory → POST → discard (**zero local disk**). Sentinel `block` frames write audit only — no OCR, no screenshot file.
- No cloud LLM API keys in the contest path. SearXNG stays **disabled** by default.
- Repo fixtures are **synthetic only**; clear the timeline DB before public demos if real capture was used.

---

## Models (logical roles)

| Role | Weights | License (summary) | Upstream |
|---|---|---|---|
| **brain** | ThinkingCap-Qwen3.6-27B GGUF | **Apache-2.0** | https://huggingface.co/bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF |
| **perceive** | Gemma 4 E4B-it GGUF + mmproj | **See Gemma callout below** | https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF · base family https://ai.google.dev/gemma |
| **sentinel** | MiniCPM-V 4.6 GGUF + mmproj | **Apache-2.0** | https://huggingface.co/openbmb/MiniCPM-V-4.6-gguf |
| **fast** | MiniCPM5-1B GGUF | **Apache-2.0** | https://huggingface.co/openbmb/MiniCPM5-1B-GGUF |
| **embed** | Qwen3-Embedding-0.6B GGUF | **Apache-2.0** | https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF |

> Handbook §10 historically listed `bge-m3`; the shipped stack uses **Qwen3-Embedding-0.6B** instead (same Apache-2.0 class).

---

## Gemma — separate callout (required)

**Component:** `perceive` = Gemma 4 E4B-it (GGUF via ggml-org; Mac/dev may use a lower quant of the same family).

| Item | Detail |
|---|---|
| **Why flagged alone** | Contest / handbook §10 requires Gemma to be called out separately from the Apache-2.0 cluster (ThinkingCap / MiniCPM / Honcho / llama.cpp / embeddings). |
| **Gemma 4 license text** | Google publishes **Gemma 4** under **Apache License 2.0**: https://ai.google.dev/gemma/docs/gemma_4_license |
| **Gemma family terms (older gens)** | Pre–Gemma-4 models used the **Gemma Terms of Use**: https://ai.google.dev/gemma/terms — do not confuse with Gemma 4. |
| **Prohibited use** | https://ai.google.dev/gemma/prohibited_use_policy (incorporated by older Gemma ToU; still treat as operational guidance). |
| **Redistribution notice** | When redistributing weights (not just linking), include the notice required by the applicable Google terms / Apache NOTICE practice. DejaView does **not** vendor weights in git. |
| **GGUF redistributor** | https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF |

**Bottom line for judges:** perceive is a **Google Gemma 4** weight; license is **Apache-2.0 for Gemma 4**, but it is **documented separately** here on purpose.

---

## Inference & orchestration libraries

| Component | License (summary) | Upstream |
|---|---|---|
| llama.cpp (HIP/Metal) | MIT | https://github.com/ggml-org/llama.cpp |
| LiteLLM (gateway) | MIT | https://github.com/BerriAI/litellm |
| Honcho (pinned `340175ad` + local patches) | Apache-2.0 | https://github.com/plastic-labs/honcho |
| PaddleOCR / PP-OCRv6 | Apache-2.0 | https://github.com/PaddlePaddle/PaddleOCR |
| rapidocr-onnxruntime (Mac OCR default) | Apache-2.0 | https://github.com/RapidAI/RapidOCR |
| onnxruntime | MIT | https://github.com/microsoft/onnxruntime |
| Postgres + pgvector / Redis | PostgreSQL / BSD-style | via `deploy/mac/compose.data.yml` |
| Open WebUI / MarkItDown (optional / future) | MIT | https://github.com/open-webui/open-webui · https://github.com/microsoft/markitdown |

**Do not copy AGPL code** (OpenRecall is reference-only for product shape).

---

## Python service dependencies (direct)

Declared in each service’s `pyproject.toml` (typical OSI-friendly stack: MIT / BSD / Apache). Not an exhaustive transitive SPDX dump — pin files are `uv.lock`.

| Package area | Examples | Typical license |
|---|---|---|
| HTTP / API | FastAPI, Starlette, Uvicorn, httpx, python-multipart | MIT |
| Images | Pillow, mss, imagehash | HPND / MIT / BSD-style |
| DB | psycopg, psycopg-pool | LGPL-3.0 (psycopg3) — used as a client library |
| OCR | paddleocr, rapidocr-onnxruntime, onnxruntime | Apache-2.0 / MIT |
| macOS capture | pyobjc-*, PyYAML | MIT / BSD |

Project application code under `services/`, `clients/`, `deploy/` is original DejaView work unless a file states otherwise.

---

## AMD AI Developer Program / Rules — team self-check

Agents **cannot** register on behalf of teammates. Before final contest upload, **each teammate** must personally confirm:

- [ ] Registered for **AMD AI Developer Program** (mainland China: **AMD Developer Program China**) — prize eligibility requires this (handbook §1.1).
- [ ] Read the contest **Rules & Conditions** on the official Luma / AMD page: https://luma.com/amd-4dhi
- [ ] Know the submitter contact / Discord if needed: `ai_dev_contests@amd.com` · https://discord.gg/zt9caur5B3
- [ ] Submission format/platform matches current Rules (do not invent a portal — follow the published instructions).

Record who confirmed (name + date) in the team chat or a private note — not required in git.

---

## Handbook §10 readiness (honest status · 2026-07-23)

Mirror of `docs/EXECUTION_HANDBOOK.md` §10. **Do not treat unchecked boxes as done.**

| §10 item | Status | Notes |
|---|---|---|
| 全员注册 AMD AI Developer Program | **待** | Team self-check above — not automatable |
| Rules & Conditions 通读 + 按规定提交 | **待** | Same; portal/format TBD by Rules at submit time |
| 仓库公开、README 双语、两拓扑、快速开始可复现 | **部分具备** | README 双语 + 形态 A/B 拓扑 + 冒烟步骤已由 **P3.3** (`63b10d3`) 完成;仓库仍为 **private**,公开化留提交前 |
| `docs/benchmarks.md` + Grafana 截图 | **待** | OCR A/B 已在 `benchmarks.md`;**ROCm 消融 = P3.1**(进行中);**Grafana = P3.2**(depends P3.1) |
| 演示视频 ≤5 分钟 | **待** | **P3.4**(depends P3.1;手册 §9 六幕含拔网线) |
| `docs/licenses.md`(含 Gemma 单独标注) | **已具备** | 本文(P3.5) |
| 提示词/示例去个人信息;无真实隐私/API key 入库 | **基本具备** | Honcho few-shot 已合成化(M2.3);`tests/assets` 全合成;演示前若跑过真实采集须清库 |
| 比赛服务器仅演示数据、赛后可销毁重建 | **部分具备** | 算力端无状态 + `download-models.sh` 可重建权重;提交前仍须确认演示数据/清库流程 |

**Still open for final submit (do not fake-check):** P3.1 ROCm ablation · P3.2 Grafana · P3.4 demo video · repo publicize · human AMD/Rules registration.
