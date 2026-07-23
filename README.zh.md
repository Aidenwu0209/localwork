# DejaView · 全本地数字记忆体

为 AMD AI DevMaster Hackathon(赛道 2:Agentic AI)打造的全本地"数字记忆"系统。

持续感知你的屏幕,用隐私哨兵模型过滤"什么不该被记住",用确定性 OCR 提取逐字文本,用
Honcho 构建用户心理画像,并带着截图证据回答你的问题——**AI 推理 100% 在 AMD Radeon
PRO W7900D(ROCm)上执行,数据永远存在你自己的设备上**。

> 产品代号:**DejaView**(déjà vu + view:你的机器替你"似曾相识")。
> 英文主文档见 `README.md`。

---

## 为什么做这个

微软 Recall 因隐私几乎翻车、Rewind 卖身——这个产品形态被云端判了死刑。我们用一块
48 GB Radeon 把它安全复活,且比它们多两层:**用户心理建模**(Honcho——不只记得,还理解)
与**模型级隐私哨兵**(本地记忆内部也有权限分级:敏感画面在到达 OCR 或存储之前就被拦截)。

四根不能砍的柱子:**隐私哨兵 · 带截图证据的问答 · 日报多 Agent 流 · ROCm 优化报告**。

## 架构(双平面,存储/计算分离)

```
┌─ 传感器(Mac/Win)─┐  ┌─ 数据主权端(Mac,有状态)───────────────┐  ┌─ 算力端(AMD 服务器,无状态)──────┐
│ capture 客户端    │  │ memoryd(编排)        agentd(主脑出口)│  │ LiteLLM 网关 :4000              │
│ 逐窗口截图        │─▶│ ocrd(PP-OCR,CPU)     Honcho(用户画像)│─▶│ brain :8001(ThinkingCap-27B)     │
│ dhash 去重        │  │ Postgres+pgvector     timeline+kb+audit│  │ perceive :8002(Gemma E4B)        │
│ 零落盘            │  │ DATA_ROOT(截图)                          │  │ sentinel :8003(MiniCPM-V 4.6)    │
└───────────────────┘  └─────────────────────────────────────────┘  │ fast :8005(MiniCPM5-1B)          │
                          GATEWAY_URL 是唯一接缝 ◀──────────────────│ embed :8004(Qwen3-Embedding-0.6B)│
                                                                     └──────────────────────────────────┘
```

- **有状态的全在 Mac**:Postgres、Redis、截图/录音/文档、审计日志。单一可移植数据根
  `DATA_ROOT`(`~/dejaview-data`)。Mac↔服务器之间只有一条 LiteLLM `GATEWAY_URL` 接缝。
- **服务器纯无状态**:模型服务 + OCR + 网关 + 监控。关闭 prompt 日志(`--log-disable`);
  权重在 overlay,靠 manifest+sha256 一键重建。
- **三层推理金字塔**:每个请求路由到"够用的最便宜层"——高频浅任务走快车道(≈1B),中频
  理解走 perceive(8B 级),低频深推理走 brain(27B)。这个金字塔本身就是"推理速度优化"
  评分项的叙事素材。

## 快速开始(开发拓扑:Mac + AMD 服务器经 SSH 隧道)

前置:Docker Desktop 运行中;`uv` 已装;SSH 别名 `radeon-cloud` 指向 AMD 服务器。

```bash
# 1. 数据层 + Honcho(Mac)
make data-up                                                    # pgvector :5433 + redis :6380
docker compose -f deploy/mac/compose.honcho.yml up -d           # Honcho api/deriver :8100
# 向量维度 ALTER 到 1024(一次性,见 STATUS.md)

# 2. AMD 服务器推理栈(完整指南见 deploy/server/DEPLOY.md)
ssh radeon-cloud "cd /root/dejaview-launch && ./server-stack.sh up embed fast sentinel perceive"

# 3. Mac 桥接到服务器网关(服务器端口不公开)
ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud                   # Mac :14000 → 服务器 :4000

# 4. Mac 服务
cd services/ocrd && nohup uv run python -m ocrd > /tmp/ocrd.log 2>&1 &           # :8006
MEMORYD_REAL_PIPELINE=1 GATEWAY_URL=http://127.0.0.1:14000/v1 \
  nohup uv run --project services/memoryd python -m memoryd > /tmp/memoryd.log 2>&1 &   # :8090
cd clients/capture && CAPTURE_DEVICE_ID=dev uv run python -m capture             # 逐窗口采集

# 5. 问你的记忆(先在服务器起 brain:./server-stack.sh up brain)
GATEWAY_URL=http://127.0.0.1:14000/v1 uv run --project services/agentd python -m agentd   # :8101
curl -X POST http://127.0.0.1:8101/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"我今天上午在做什么?"}]}'
# → "你在调试 ROCM-4042 报错 [event#120 00:45 Terminal]"
```

## 评分对照(赛道 2)

| 评分维度 | 权重 | DejaView 怎么拿 |
|---|---|---|
| 功能完整性与应用价值 | 60 | 逐窗口采集→哨兵→OCR→理解→时间线→Honcho 画像→带证据问答。四柱 + 多窗口感知。 |
| Radeon GPU / ROCm 优化 | 40 | 五模型常驻 48GB(Q8/Q6 分层);三层推理金字塔;llama.cpp HIP/gfx1100;消融报告(T3.1,`docs/benchmarks.md`);存储/计算分离。 |

## 当前状态

**TASKBOARD:33/33 全绿。** 全链路端到端验证通过(M3.4);54 分钟真实工作运行验收四项全
达标(真实事件、零外部网络、哨兵审计、零落盘)。剩余是 Phase 3(ROCm 消融报告、Grafana、
演示视频、README 打磨)——见 `STATUS.md` → "还没做的"。

## 先读哪些

- **`STATUS.md`** — 人话版快照:能跑什么、已知问题、下一步。**先读这个。**
- `docs/EXECUTION_HANDBOOK.md` — 唯一事实来源:架构/规格/WBS/交接(§12)。
- `docs/verification-log.md` — 所有踩坑 + `[VERIFY]` 结论(必读,避免重踩)。
- `docs/benchmarks.md` — OCR 精度 A/B;T3.1 ROCm 消融报告写在这。
- `deploy/server/DEPLOY.md` — AMD 服务器部署(编译/VRAM 预算/Dolphin 共存/隧道)。
- `TASKBOARD.json` — 权威任务状态机。

## 许可证(供 `docs/licenses.md`,T3.6)

Apache-2.0:ThinkingCap / MiniCPM / Honcho / llama.cpp / PaddleOCR。**Gemma License 单独标注。**
Qwen3-Embedding:Apache-2.0。LiteLLM:MIT。MarkItDown:MIT。Open WebUI:MIT。
