# DejaView — 项目状态交接(2026-07-23)

> 这份文档是**人话版的现状快照**,给接手的人(或几天后的你自己)快速理解"现在到哪了、能跑什么、卡在哪、下一步做什么"。
> 状态机以 `TASKBOARD.json` 为准;完整交接以 `docs/EXECUTION_HANDBOOK.md` **§12** 为准;踩坑细节以 `docs/verification-log.md` 为准。本文是人话导航。

---

## 一句话现状

**全链路跑通并验收**:Mac 采集端逐窗口截屏 → memoryd 编排(哨兵→OCR→新颖度门→理解→入库)→ AMD 服务器 ROCm 推理(5 个分层模型)→ Postgres+pgvector 时间线 → agentd 带 `[event#id HH:MM app]` 引用回答。`TASKBOARD` 中 **G0+M+D 共 33 项全部 accept**,54 分钟真实工作运行验收通过。**无遗留 `doing`/半成品**(M1.3/M2.4/M3.1 已于 2026-07-23 再验)。

**Phase 3 进行中**(提交材料 + 质量债):

| ID | 任务 | 状态 |
|---|---|---|
| P3.3 | README 双语打磨 | **accept** (`63b10d3`) |
| P3.5 | Rules + licenses.md + 提交清单 | **accept** (`3b7a0c7`) |
| P3.6 | 哨兵调优(降 normal 误杀) | **accept** — 误杀类 15/81→0,敏感 6/6 拦截 |
| P3.7 | perceive 提示词(具体 activity + verbatim⊆OCR) | **accept** — 20/20 抽查通过 |
| **P3.1** | **ROCm 消融报告** | **blocked** — 小模型数据已入 `benchmarks.md §2`;brain×MTP×并发因 SSH `:30147` 断连未测完 |
| P3.2 | Grafana 大屏 | false — depends P3.1 |
| P3.4 | 演示视频 ≤5min | false — depends P3.1,建议报告有数后再拍 |

**离提交还差**:P3.1 收尾(40 分主证据,等服务器 SSH 恢复)、P3.2 Grafana、P3.4 演示视频。

---

## 能跑什么(实测过的)

### 一键起来的栈

| 层 | 怎么起 | 端口 | 说明 |
|---|---|---|---|
| **Mac 数据层** | `make data-up` | pg :5433 / redis :6380 | postgres+pgvector+redis,compose.data.yml |
| **Mac Honcho** | `docker compose -f deploy/mac/compose.honcho.yml up -d` | :8100 | api+deriver,库复用 data 层 |
| **Mac ocrd** | `cd services/ocrd && uv run python -m ocrd` | :8006 | PP-OCRv4(rapidocr),Mac dev |
| **Mac memoryd** | `MEMORYD_REAL_PIPELINE=1 GATEWAY_URL=http://127.0.0.1:14000/v1 uv run --project services/memoryd python -m memoryd` | :8090 | 真推理流水线(需服务器网关) |
| **Mac agentd** | `GATEWAY_URL=http://127.0.0.1:14000/v1 uv run --project services/agentd python -m agentd` | :8101 | OpenAI 兼容出口 |
| **Mac capture** | `cd clients/capture && CAPTURE_DEVICE_ID=<id> uv run python -m capture` | — | 逐窗口截图 |
| **AMD 服务器推理栈** | `ssh radeon-cloud` → `cd /root/dejaview-launch && ./server-stack.sh up embed fast sentinel perceive` | :4000(网关) | 4 小模型常驻 ~12GB |
| **brain(按需)** | 同上 `./server-stack.sh up brain` | :8001 | Q6_K 21GB,和 Dolphin 共存要先停 perceive |
| **Mac↔服务器隧道** | `ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud` | Mac :14000 → 服务器 :4000 | 服务器网关不暴露公网,走隧道 |

### 完整工作流(从采集到问答)

```bash
# 1. 数据层 + Honcho
make data-up
docker compose -f deploy/mac/compose.honcho.yml up -d

# 2. SSH 隧道(Mac 用服务器推理)
ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud

# 3. ocrd + memoryd(真推理)+ capture
cd services/ocrd && nohup uv run python -m ocrd > /tmp/ocrd.log 2>&1 &
MEMORYD_REAL_PIPELINE=1 GATEWAY_URL=http://127.0.0.1:14000/v1 \
  nohup uv run --project services/memoryd python -m memoryd > /tmp/memoryd.log 2>&1 &
cd clients/capture && CAPTURE_DEVICE_ID=dev uv run python -m capture

# 4. 工作 N 分钟,然后问 agentd
GATEWAY_URL=http://127.0.0.1:14000/v1 uv run --project services/agentd python -m agentd
curl -X POST http://127.0.0.1:8101/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"What was I working on this morning?"}]}'
# → 带 [event#id HH:MM app] 引用的回答
```

---

## 干了什么(成果)

### 数据层 + Honcho(M1.3/M3.1/M2.1-2.4)
- pgvector/pg16 + redis 起服,timeline_events/sentinel_audit/kb_chunks 三表 + 4 业务索引
- Honcho fork 钉在 340175ad,补丁栈(setup-honcho.sh 重建,submodule 保持 pristine)
- **3 个国内环境坑全解了**:① PyPI 镜像(wrapper Dockerfile sed 重写 uv.lock URL 到清华);② host 代理 127.0.0.1:7897 透传进容器(build/runtime 清空);③ Honcho 默认 vector(1536) vs 我们 1024(configure_embeddings.py ALTER)

### 推理栈(M2.5 + 服务器 S1)
- **Mac Metal**:5 逻辑名经 LiteLLM 网关冒烟通过(dev brain 由 E4B 兼任)
- **AMD 服务器**:llama.cpp HIP 编译(gfx1100 + ROCm 7.2,-j32 五分钟),4 小模型常驻 ~12GB,brain Q6_K 按需 21GB
- **Dolphin 共存**:服务器上别人在跑的 Dolphin 评估任务(~10.6GB VRAM)全程没被影响
- **关键发现**:MiniCPM5/MiniCPM-V/Gemma-E4B 都是**思考型模型**,fast-track 任务(哨兵/新颖度门)必须 `chat_template_kwargs.enable_thinking=false`,否则 token 全烧在 reasoning_content

### 服务(memoryd/ocrd/agentd/capture)
- **memoryd**:FastAPI 摄取编排,可插拔 pipeline(sentinel→ocrd→novelty→perceive→embed→store),三模式检索(semantic/exact/hybrid + 时间过滤),WebP→PNG 转换
- **ocrd**:双后端(rapidocr Mac dev ~1s/图 + paddleocr PP-OCRv6 EPYC 生产),精度 A/B(rapidocr 0.877 vs paddleocr 0.967)
- **agentd**:OpenAI 兼容 /v1/chat/completions + tool-calling 循环(4 工具)+ `[event#id HH:MM app]` 引用格式
- **capture**:逐窗口截图(screencapture -l <wid>)+ dhash 去重 + 锁屏暂停 + 零落盘 + osascript URL 探测
- **Honcho 记忆链路**:灌 20 条合成消息 → deriver 生成准确 summary → dialectic 准确回答用户画像问题

### Phase 3 已完成
- **P3.3 README 双语**:形态 A(Mac 主权+AMD 算力)/形态 B(单机)双拓扑、评分 60+40 对照、形态 A 一键复现冒烟
- **P3.5 licenses**:`docs/licenses.md` 覆盖五模型 + 引擎/库,Gemma 4 单独标注,用户数据不上云声明;手册 §10 诚实旁注
- **P3.6 哨兵**:category→decision,M4.4 的 normal 误杀类(15/81)→ 0;fixture 6/6 敏感拦截、0/4 normal 误杀
- **P3.7 perceive**:20/20 activity 具体(无 "working in X" 空泛),verbatim 全部可在 ocr_text 溯源
- **P3.1 ROCm(部分)**:`benchmarks.md §2` 有小模型 n=3 中位——fast decode 366.7 tok/s、sentinel 221、perceive ~80、4-model VRAM 13.71/47.98 GiB + rocm-smi 截图

---

## 已知问题 / 技术债(接手必看)

| # | 问题 | 状态 | 说明 |
|---|---|---|---|
| 1 | sentinel confidence 恒 0.5、normal 误杀 | **P3.6 已缓解** | category→decision 后 normal 强制 allow;仍可复查置信度语义 |
| 2 | perceive activity 偏泛 | **P3.7 已缓解** | 提示词 + 解析硬过滤,20/20 具体 |
| 3 | 网关偶发 `model=None` 400(~2%) | 待查 | 疑某调用方(Honcho health check?)漏 model 字段 |
| 4 | 单帧经隧道 ~12-15s | 决赛用 LAN | 服务器网关绑 0.0.0.0 + token 可降到 ~5s |
| 5 | ocrd 在 Mac=rapidocr,生产该 EPYC=paddleocr | 部署项 | 后端切换一行配置 |

### 架构性提醒

- **brain 27B 不能和 Dolphin 全量常驻**:Q8(28GB)+Dolphin(10.6)+4小模型(12)= 50.6 > 48GB 会 OOM。共享 GPU 用 **Q6_K(21GB)**,且起 brain 前停 perceive(brain 能兼任)。决赛若 GPU 独占再上 Q8。
- **服务器模型在 overlay**(容器重建即失):靠 `/workspace/dejaview-models/download-models.sh` + sha256 一键重建。
- **真实窗口标题进 DB**:M4.4 长跑会让真实信息进 timeline_events——演示前清库。
- **honcho submodule 会显示 dirty**:那是 `setup-honcho.sh` 把 `deploy/mac/honcho-patches/` apply 到工作区的正常态,pin 仍是干净的 340175ad。**不要 `git add third_party/honcho`**。

---

## 还没做的(Phase 3 剩余)

| ID | 任务 | 评分影响 | 备注 |
|---|---|---|---|
| **P3.1** | ROCm 消融收尾(brain 量化×MTP×并发) | **40 分主证据** | 等服务器 SSH 恢复;`benchmarks.md §2` 已列 blocked 行待填 |
| P3.2 | Grafana 大屏 | 演示 | depends P3.1 |
| P3.4 | 演示视频 ≤5min(手册 §9 六幕 + 拔网线) | 提交必备 | 建议 P3.1 有数后拍 |
| — | P3.8-P3.10 / MCP / 音频 | 可砍 | 见手册 §1.5 砍需求顺序 |

---

## 关键文件地图(接手先看这些)

```
docs/EXECUTION_HANDBOOK.md   ← 唯一事实来源;完整交接看 §12
docs/verification-log.md     ← 所有踩坑 + [VERIFY] 结论(必读,避免重踩)
docs/benchmarks.md           ← OCR 精度 A/B(§1)+ ROCm 消融(§2,P3.1 部分/待收尾)
docs/licenses.md             ← 许可证清单(P3.5)
docs/AGENT_KICKOFF_PROMPT.md ← 直接丢给执行 agent 的开工指令(Phase 3)
TASKBOARD.json               ← 状态机(33/33 accept + P3.* 进行中)
STATUS.md                    ← 本文(人话版快照)

deploy/mac/llama-launch/     ← Mac Metal 启动脚本 + dev-stack.sh 控制器
deploy/mac/honcho.Dockerfile ← Honcho 构建包装(PyPI 镜像,submodule 保持 pristine)
deploy/mac/honcho.env        ← 运行时配置(gitignored,含 IPv4 workaround)
deploy/server/DEPLOY.md      ← AMD 服务器起停 + VRAM/Dolphin 共存预算
deploy/server/litellm.yaml   ← 网关配置(Mac dev: brain 双映射 perceive;服务器:真 brain)

services/memoryd/            ← 摄取编排(stages.py + eval_sentinel/eval_perceive 脚本)
services/ocrd/               ← OCR 微服务(engine.py 双后端)
services/agentd/             ← brain 出口(tools.py 4 工具 + server.py tool-calling)
clients/capture/             ← 采集客户端(windows.py 逐窗口 + agent.py 主循环)
```

---

## 给接手 agent 的几句话

1. **先读 `docs/EXECUTION_HANDBOOK.md` §12 + 本文 + `docs/verification-log.md`** —— resolved 项都是踩过的坑,别重踩(MiniCPM 思考模式、WebP→PNG、Docker 代理透传、IPv6 host.docker.internal)。
2. **起任何推理前先 `ssh radeon-cloud "rocm-smi --showmeminfo vram"`** 看 Dolphin 还占多少,别 OOM 它。
3. **改 Honcho 提示词/deriver 逻辑**:submodule 保持 pristine,改 `deploy/mac/honcho-patches/` 的 diff,`setup-honcho.sh` 重建。
4. **commit 纪律**:作者只能是 `Aidenwu0209 <1418557225@qq.com>`,**无任何 AI trailer**(Cursor 可能自动加 Co-authored-by,提交后务必核对);每任务一 commit + push。
5. **下一步最高优先级是 P3.1 ROCm 消融收尾** —— 40 分主证据,等服务器 SSH 恢复后跑 brain 量化×MTP×并发,填 `benchmarks.md §2` 的 blocked 行,再把 TASKBOARD P3.1 `blocked→accept`。
