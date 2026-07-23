# DejaView — 项目状态交接(2026-07-22)

> 这份文档是**人话版的现状快照**,给接手的人(或几天后的你自己)快速理解"现在到哪了、能跑什么、卡在哪、下一步做什么"。
> 状态机以 `TASKBOARD.json` 为准(33/33 accept);技术细节以 `docs/verification-log.md` 为准;本文是导航 + 总结。

---

## 一句话现状

**全链路跑通了**:Mac 采集端逐窗口截屏 → memoryd 编排(哨兵→OCR→新颖度门→理解→入库)→ AMD 服务器 ROCm 推理(5 个分层模型)→ Postgres+pgvector 时间线 → agentd 带 `[event#id HH:MM app]` 引用回答。TASKBOARD 33 个任务全部 accept,54 分钟真实工作运行验收通过。

**离提交还差**:ROCm 消融报告(40 分主证据)、Grafana 大屏、README 双语打磨、演示视频、Rules 核对。这些是 Phase 3(T3.x),不在当前 TASKBOARD 里。

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

## 干了什么(本阶段 19 个任务的成果)

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
- **agentd**:OpenAI 兼容 /v1/chat/completions + tool-calling 循环(4 工具)+ `[event#id HH:MM app]` 引用格式,端到端"我遇到过什么 GPU 错误"→ 带 ROCM-4042 引用回答
- **capture**:逐窗口截图(screencapture -l <wid>)+ dhash 去重 + 锁屏暂停 + 零落盘 + osascript URL 探测
- **Honcho 记忆链路**:灌 20 条合成消息 → deriver 生成准确 summary → dialectic 准确回答用户画像问题

### 测试资产(M6.1/M6.2/M6.3)
- 20 张合成截图(报错码/URL/中英混排)+ 30 条合成消息 + 50 对相邻帧(Jaccard 自动分桶)+ 40 张哨兵敏感页(银行/密码/私聊/证件 + 正常),全合成零真实 PII

---

## 已知问题 / 技术债(接手必看)

### 必须修的(影响功能/评分)

| # | 问题 | 影响 | 在哪修 | 难度 |
|---|---|---|---|---|
| 1 | **sentinel confidence 恒 0.5,15/81 normal 误杀** | 隐私哨兵精度差,正常页被拦 | T2.1:收紧 sentinel 提示词 + JSON 解析;直测时 banking 能 block,经 pipeline 误杀多 | 中 |
| 2 | **网关 `model=None` 400 错误(2%)** | 偶发请求漏 model 字段 | 查 Honcho/health check 路径,补 model 名 | 低 |
| 3 | **perceive activity 偏泛("working in X")** | 理解层没真正生成语义活动 | T1.6:perceive 提示词迭代,检查 JSON 解析兜底是否过激 | 中 |
| 4 | **单帧经隧道 ~12-15s** | 实时性差 | 决赛现场用 LAN(服务器网关绑 0.0.0.0 + 开端口/token),开发期凑合 | — |

### 架构性提醒

- **brain 27B 不能和 Dolphin 全量常驻**:Q8(28GB)+Dolphin(10.6)+4小模型(12)= 50.6 > 48GB 会 OOM。当前用 **Q6_K(21GB)**,且起 brain 前停 perceive(brain 能兼任 perceive)。决赛演示时若 GPU 独占可上 Q8。
- **服务器模型在 overlay**(容器重建即失):靠 `/workspace/dejaview-models/` 的引导脚本 + sha256 一键重建。服务器若被回收,跑 `download-models.sh` 重建。
- **真实窗口标题进 DB**:capture 抓的 title 含 SSH 地址/频道名等(运行时数据,不上 git,合规)。但 M4.4 长跑会让真实信息进 timeline_events——演示前清库。

### 还没做的(Phase 3,非当前 TASKBOARD)

| 任务 | 说明 | 评分影响 |
|---|---|---|
| **T3.1 ROCm 消融报告** | 量化(Q8/Q6/Q4)×MTP×并发 表+图,`docs/benchmarks.md` 续写 | **40 分主证据** |
| **T3.2 Grafana 大屏** | 四实例指标 + 事件率一屏 | 演示效果 |
| **T3.3 MCP server** | Cursor 内查时间线(可砍) | 加分 |
| **T3.4 README 双语打磨** | 当前 README 有基础,缺双拓扑图/评分对照/一键复现 | 提交必备 |
| **T3.5 演示视频** | 六幕分镜(手册 §9),含拔网线镜头 | 提交必备 |
| **T3.6 Rules 核对** | AMD AI Developer Program 注册、提交格式 | 提交必备 |
| **T0.6 E4B 音频** | whisper.cpp fallback(若 E4B 音频在 ROCm 不通) | 可砍 |
| **T0.7 MTP 收益** | brain 开关 draft-mtp 的 tok/s 对比 | 优化叙事 |

---

## 关键文件地图(接手先看这些)

```
docs/EXECUTION_HANDBOOK.md   ← 唯一事实来源(架构/规格/WBS),交接章节在末尾
docs/verification-log.md     ← 所有踩坑 + [VERIFY] 结论(必读,避免重踩)
docs/benchmarks.md           ← OCR 精度 A/B(T3.1 要在这续写 ROCm 消融)
TASKBOARD.json               ← 状态机(33/33 accept)
STATUS.md                    ← 本文(人话版快照)

deploy/mac/llama-launch/     ← Mac Metal 启动脚本 + dev-stack.sh 控制器
deploy/mac/honcho.Dockerfile ← Honcho 构建包装(PyPI 镜像,submodule 保持 pristine)
deploy/mac/honcho.env        ← 运行时配置(gitignored,含 IPv4 workaround)
deploy/server/llama-launch/  ← 服务器 ROCm 启动脚本 + server-stack.sh
deploy/server/litellm.yaml   ← 网关配置(Mac dev: brain 双映射 perceive;服务器:真 brain)

services/memoryd/            ← 摄取编排(stages.py 有 stub + GatewayXxx 真实现)
services/ocrd/               ← OCR 微服务(engine.py 双后端)
services/agentd/             ← brain 出口(tools.py 4 工具 + server.py tool-calling)
clients/capture/             ← 采集客户端(windows.py 逐窗口 + agent.py 主循环)
```

---

## 给接手 agent 的几句话

1. **先读 `docs/verification-log.md`** —— 里面所有"resolved"项都是踩过的坑,别重踩。尤其是:MiniCPM 思考模式、WebP→PNG、Docker 代理透传、IPv6 host.docker.internal。
2. **起任何推理前先 `ssh radeon-cloud "rocm-smi --showmeminfo vram"`** 看 Dolphin 还占多少,别 OOM 它。
3. **改 Honcho 提示词/deriver 逻辑**:submodule 保持 pristine,改 `deploy/mac/honcho-patches/` 的 diff,`setup-honcho.sh` 重建。
4. **commit 纪律**:作者只能是 `Aidenwu0209 <1418557225@qq.com>`,无 AI trailer;每任务一 commit + push。
5. **下一步最高优先级是 T3.1 ROCm 消融报告** —— 这是 40 分的主证据,现在服务器栈就绪了,可以直接跑量化×MTP×并发的基准。
