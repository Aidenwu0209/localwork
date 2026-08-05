# DejaView — 项目状态交接(2026-08-06)

> 人话版快照。完整决策与进度以 `docs/EXECUTION_HANDBOOK.md` **§12** 为准(已含 7/20–7/28 会话决策摘要)。状态机:`TASKBOARD.json`。踩坑:`docs/verification-log.md`。开工指令:`docs/AGENT_KICKOFF_PROMPT.md`。

---

## 一句话现状

**DejaView** = 全本地数字记忆体(赛道 AMD Hackathon Track 2 · Agentic AI)。比赛六幕、ROCm 证据与成熟产品加固 P3.12–P3.18 均已验收;**P3.19 已 `accept`**(最终 push CI 全绿)。工程侧交付含英文 3:15.2 视频、submission-check、DOCX/PPTX、Actions 固定 SHA、Windows capture、Mac 受管 READY/Radeon recall/evidence、断链 Local Metal fallback、桌面/移动 axe、干净检出。截止 **2026-08-06 23:59 UTC+8**。

| 状态 | 内容 |
|---|---|
| **已完成** | G0+M+D **33/33 accept**;**P3.1 ROCm 消融**;P3.3 README;P3.5 licenses;P3.6 哨兵;P3.7 perceive |
| **P3.1 正式证据** | run `p31-w7900d-20260728T075653Z`;18 个 brain 量化×MTP×并发 cell + 3 个 perceive `-np` cell,均为 1 次 warm-up + 3 次实测;原始证据在 `docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/`,截图在 `docs/assets/p31/p31-w7900d-20260728T075653Z/` |
| **本次新增完成** | **P3.2 Grafana** 一屏验收;**P3.4 六幕视频** 2:37 成片;**P3.11 Grafana 系统自检**含 READY/DEGRADED/FAILED、数据新鲜度、本机核心与算力路径 |
| **工程任务** | TASKBOARD 当前 **49/49 accept**;P3.1–P3.19 不重做;余下仅为人工提交门槛 |
| **产品化边界** | 单用户、屏幕记忆优先、隐私 fail-closed、真实 Radeon→Local Metal 降级、Honcho 自动成长、可点击证据产品页、干净机器复现 |

**下一优先:队员本人完成人工提交门槛** — AMD Developer Program、Rules、官方 fork/英文 PR、仓库可见性、服务器仅演示数据与最终平台上传。工程侧 P3.19 已关闭。

---

## 交给其他 Agent 时丢这三样

1. `docs/P3.19_HANDOFF_PROMPT.md`（现为 accept 后人工门槛指引）
2. `docs/EXECUTION_HANDBOOK.md` **§12**(含聊天决策:产品叙事、存算分离、五模型、OCR 不用 VL、git 纪律、Cursor trailer 坑)
3. `TASKBOARD.json` + `docs/verification-log.md` 最后章节

---

## 能跑什么

| 层 | 命令 | 端口 |
|---|---|---|
| 发布体检 | `make setup && make doctor` | 只读依赖/补丁/端点检查 |
| 第一方测试 | `make test` | 与 CI 相同的离线契约集 |
| 产品生命周期 | `make product-up` / `make product-status` / `make product-down` | 本地受管服务 |
| 数据层 | `make data-up` | pg :5433 / redis :6380 |
| Honcho | `docker compose -f deploy/mac/compose.honcho.yml up -d` | :8100 |
| ocrd | `cd services/ocrd && uv run python -m ocrd` | :8006 |
| memoryd | `SENTINEL_GATEWAY_URL=http://127.0.0.1:4000/v1 GATEWAY_URL=http://127.0.0.1:14000/v1 uv run --project services/memoryd python -m memoryd` | :8090 |
| agentd | `RADEON_GATEWAY_URL=http://127.0.0.1:14000/v1 LOCAL_GATEWAY_URL=http://127.0.0.1:4000/v1 uv run --project services/agentd python -m agentd` | :8101 |
| capture | `CAPTURE_DEVICE_ID=<id> make capture`(前台,授权可见) | — |
| 隧道 | `ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud` | Mac :14000 |
| 本地隐私门 | `./deploy/mac/llama-launch/dev-stack.sh up sentinel` | :4000 / :8003 |
| AMD 算力端 | `ssh radeon-cloud` → `/root/dejaview-launch/server-stack.sh up embed fast perceive` | :4000 |

Radeon Cloud 实例和公网端口可能更换;发布文档只使用本机已授权的
`ssh radeon-cloud` 别名,不把某次临时公网坐标当成产品配置。实例重启或重起模型前,
先 `rocm-smi` + `server-stack.sh status`,再只起所需角色。五个逻辑角色
不等于所有权重在每种拓扑同时常驻:daily split 中 Sentinel 留在数据主权端,
brain 按需启动。若出现 Dolphin/未知 KFD 共租,brain 用 Q6_K、先停 perceive、MTP
默认关;只有加载后仍保留 ≥6 GiB 余量才可开 MTP。

---

## 会话里定死的关键决策(摘要)

- **产品**:数字记忆体,不做普通 RAG 聊天;叙事 = Recall/Rewind 翻车 → Radeon 安全复活 + Honcho + 哨兵。
- **架构**:Mac/Windows=数据主权,原始画面先过本地 Sentinel;AMD=放行后的无状态算力;经 LiteLLM 逻辑名(`brain/perceive/sentinel/fast/embed`)。
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
| P3.4 | **accept** | 2:37 六幕成片;可见软件断开已验证远端链路,Local Metal 完成第二次引用日报 |
| P3.2 | **accept** | Grafana 一屏 + 实时 fail-closed 门禁 + 截图已入仓 |
| P3.11 | **accept** | Grafana 系统自检;缺服务/隧道不误绿;READY 实拍 + 13 tests |
| P3.12 | **accept** | 成熟产品设计与 P3.13–P3.18 验收矩阵;基线 80 tests |
| P3.13 | **accept** | Sentinel fail-closed + strict schema;默认 real/Stub 503;审计 reason;真实 processing_state;audio/doc 501;capture outcome/heartbeat/权限退出;127 tests + live synthetic proof |
| P3.14 | **accept** | 问答与日报共用真实 Radeon-first router;引用防伪;Local Metal `perceive` fallback;63 tests + Radeon/断链本地/双失败 live probes |
| P3.15 | **accept** | 原子 Honcho outbox、严格最小化 payload、幂等 worker、退避/租约、暂停/状态;59 tests + live success/replay/failure/pause proof |
| P3.16 | **accept** | 默认日常产品页;能力签名证据+CSRF+本地控制边界;四档无溢出;axe 0 violations;独立安全/UX 复审 PASS |
| P3.17 | **accept** | 干净检出全套通过;幂等 Honcho setup、只读 doctor、第一方 CI、受管生命周期、LICENSE/NOTICE、双语文档、英文规格与可编辑 PPT 均完成 QA |
| P3.18 | **accept** | CI 全绿 + 当前源码隔离 Radeon Recall/引用/受控证据图 + 发布/隐私/历史 live-flow 证据总验收 |
| P3.19 | **accept** | 2026-08-06: SHA `c83920f` / Actions run `31033305322` Linux+macOS 全绿;ffmpeg/ffprobe 已装入 CI;人工门槛仍未勾选 |

---

## 接手纪律(最短版)

1. 勿重做 TASKBOARD 49 个已 accept 项,尤其不要重跑 P3.1 正式矩阵或 P3.19 工程门禁。
2. 起 GPU 前 `rocm-smi`,勿碰未知 KFD 进程;共租时勿 OOM Dolphin。
3. commit 后检查无 `Co-authored-by`。
4. 演示前清 timeline 库。
5. 只按 P3.12 设计实施已验收范围;不扩到账户、同步、安装器或未定义模型。
6. 人工门槛(注册/官方 PR/上传)不得写成已完成,除非有队员本人证据。
