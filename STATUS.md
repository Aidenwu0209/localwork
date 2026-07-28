# DejaView — 项目状态交接(2026-07-28)

> 人话版快照。完整决策与进度以 `docs/EXECUTION_HANDBOOK.md` **§12** 为准(已含 7/20–7/28 会话决策摘要)。状态机:`TASKBOARD.json`。踩坑:`docs/verification-log.md`。开工指令:`docs/AGENT_KICKOFF_PROMPT.md`。

---

## 一句话现状

**DejaView** = 全本地数字记忆体(赛道 AMD Hackathon Track 2 · Agentic AI)。全链路已跑通验收。截止 **2026-08-06**,约剩 **9 天**。

| 状态 | 内容 |
|---|---|
| **已完成** | G0+M+D **33/33 accept**;**P3.1 ROCm 消融**;P3.3 README;P3.5 licenses;P3.6 哨兵;P3.7 perceive |
| **P3.1 正式证据** | run `p31-w7900d-20260728T075653Z`;18 个 brain 量化×MTP×并发 cell + 3 个 perceive `-np` cell,均为 1 次 warm-up + 3 次实测;原始证据在 `docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/`,截图在 `docs/assets/p31/p31-w7900d-20260728T075653Z/` |
| **未开始** | **P3.4** 演示视频; **P3.2** Grafana |
| **可砍** | MCP / 音频 / MarkItDown / 日报多 Agent UI |

**下一优先:P3.4 演示视频 → P3.2 Grafana**。

---

## 交给其他 Agent 时丢这三样

1. `docs/EXECUTION_HANDBOOK.md` **§12**(含聊天决策:产品叙事、存算分离、五模型、OCR 不用 VL、git 纪律、Cursor trailer 坑)
2. `docs/AGENT_KICKOFF_PROMPT.md`
3. `TASKBOARD.json`(P3.1 已验收;只领 P3.4 / P3.2)

---

## 能跑什么

| 层 | 命令 | 端口 |
|---|---|---|
| 数据层 | `make data-up` | pg :5433 / redis :6380 |
| Honcho | `docker compose -f deploy/mac/compose.honcho.yml up -d` | :8100 |
| ocrd | `cd services/ocrd && uv run python -m ocrd` | :8006 |
| memoryd | `MEMORYD_REAL_PIPELINE=1 GATEWAY_URL=http://127.0.0.1:14000/v1 uv run --project services/memoryd python -m memoryd` | :8090 |
| agentd | `GATEWAY_URL=http://127.0.0.1:14000/v1 uv run --project services/agentd python -m agentd` | :8101 |
| capture | `cd clients/capture && CAPTURE_DEVICE_ID=<id> uv run python -m capture` | — |
| 隧道 | `ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud` | Mac :14000 |
| 服务器 | `ssh radeon-cloud` → `/root/dejaview-launch/server-stack.sh up embed fast sentinel perceive` | :4000 |

当前服务器:`root@36.150.116.200 -p 30189`(实例
`u-4695-e6d1476b`;别名仍为 `radeon-cloud`)。旧 `:30147` 是
2026-07-23～28 历史故障实例,勿再作为现行端口。P3.1 正式 run 结束后
gateway 与五个模型角色均为 `down`,VRAM 回到 28,016,640 B;P3.4/P3.2
起服前先 `rocm-smi` + `server-stack.sh status`,再只起所需角色。若未来
再次出现 Dolphin/未知 KFD 共租,brain 用 Q6_K、先停 perceive、MTP
默认关;只有加载后仍保留 ≥6 GiB 余量才可开 MTP。

---

## 会话里定死的关键决策(摘要)

- **产品**:数字记忆体,不做普通 RAG 聊天;叙事 = Recall/Rewind 翻车 → Radeon 安全复活 + Honcho + 哨兵。
- **架构**:Mac=数据主权;AMD=无状态算力;经 LiteLLM 逻辑名(`brain/perceive/sentinel/fast/embed`)。
- **模型**:brain=ThinkingCap-27B;perceive=Gemma4-E4B;sentinel=MiniCPM-V;fast=MiniCPM5;embed=Qwen3-Emb-0.6B;ocrd=**PP-OCRv6**(不用 VL)。
- **Honcho**:钉 `340175ad` + `deploy/mac/honcho-patches/`;勿追 main;勿 add dirty submodule。
- **Git**:作者仅 Aidenwu0209;无 AI trailer(Cursor 会自动加,用 commit-tree 清);每任务 push。

详情见手册 **§12.2**。

---

## Phase 3 明细

| ID | 状态 | 说明 |
|---|---|---|
| P3.3 | accept | 双语 README + 双拓扑 + 冒烟 |
| P3.5 | accept | `docs/licenses.md`;§10 诚实旁注 |
| P3.6 | accept | normal 误杀类 15/81→0 |
| P3.7 | accept | 20/20 具体 activity;verbatim⊆ocr |
| P3.1 | **accept** | run `p31-w7900d-20260728T075653Z`;Q8/Q6/Q4×MTP×并发 + perceive `-np` 全矩阵;原始证据、hash、日志、rocm-smi 齐全 |
| P3.4 | false | §9 六幕 + 拔网线 ≤5min |
| P3.2 | false | Grafana 一屏 |

---

## 接手纪律(最短版)

1. 勿重做 33+5 已 accept 项,尤其不要重跑 P3.1 正式矩阵。
2. 起 GPU 前 `rocm-smi`,勿碰未知 KFD 进程;共租时勿 OOM Dolphin。
3. commit 后检查无 `Co-authored-by`。
4. 演示前清 timeline 库。
5. 最高优先 P3.4,随后 P3.2。
