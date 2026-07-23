# DejaView · 全本地数字记忆体

> 持续感知你的屏幕与语音,把数字生活变成**可问答、带证据的记忆**;用 Honcho 心理建模理解「你是谁」;隐私哨兵把关「什么不该被记住」。**AI 推理 100% 在 Radeon PRO W7900D(ROCm)上执行**,数据永远存在用户自己的设备上。

产品代号:**DejaView**(déjà vu + view:你的机器替你「似曾相识」)。  
英文主文档:[README.md](README.md)

面向 [AMD AI DevMaster Hackathon](https://luma.com/amd-4dhi) · **赛道 2 · Agentic AI**。

---

## 为什么做这个(获奖叙事)

微软 Recall 因隐私几乎翻车、Rewind 卖身——这个产品形态被云端判了死刑。我们用一块 **48 GB Radeon** 把它安全复活,且比它们多两层:

1. **用户心理建模**(Honcho reasoning-first 画像 + dialectic 问答——不只记得,还理解)
2. **模型级隐私哨兵**(本地记忆内部也有权限分级;敏感帧在 OCR / 落盘前就被拦截)

**同品类先例:** Microsoft Recall(云端信任危机)、Rewind.ai(已转向)、OpenRecall(开源 AGPL——截屏+OCR+搜索,无理解层)。

**我们的差异:** ① Honcho 用户画像 · ② 入库前隐私哨兵 · ③ Agent 任务闭环(tool calling、日报多 Agent 流) · ④ 五模型分层常驻 48 GB + ROCm 优化报告 · ⑤ 存储/计算分离的数据主权架构。

**四根柱子(永不砍):** 隐私哨兵 · 带截图证据的问答 · 日报多 Agent 流 · ROCm 优化报告。

---

## 双拓扑

同一套代码与 compose,靠 `GATEWAY_URL` / profile 切换(见 `docs/EXECUTION_HANDBOOK.md` §2.2)。  
下方 **形态 A** 是陌生人今天就能冒烟的路径;**形态 B** 是评委复现 / 演示日用的单机 AMD 拓扑。

### 形态 A — Mac 数据主权 + AMD 无状态算力

*日常主拓扑。有状态记忆在用户 Mac;GPU 端纯算力。*

```
┌─ 传感器(Mac/Win) ─┐   ┌─ 数据主权端(Mac,有状态) ──────────────┐   ┌─ 算力端(AMD,无状态) ────────────┐
│ capture 客户端     │   │ memoryd(编排)        agentd(主脑出口) │   │ LiteLLM 网关 :4000              │
│ 逐窗口截图         │──▶│ ocrd(PP-OCR,CPU)     Honcho(用户画像) │──▶│ brain :8001 · perceive :8002    │
│ dhash · 零落盘     │   │ Postgres+pgvector     timeline+kb+audit│   │ sentinel :8003 · fast :8005     │
└────────────────────┘   │ DATA_ROOT(~/dejaview-data)             │   │ embed :8004 ·(可选 EPYC ocrd)   │
                         └────────────────────────────────────────┘   └────────────────────────────────┘
                                      GATEWAY_URL 是唯一 Mac↔服务器接缝
                                      (开发期:SSH 隧道 Mac :14000 → 服务器 :4000)
```

- **有状态只在 Mac:** Postgres、Redis、截图/录音/文档、审计日志。单一可移植 `DATA_ROOT`。
- **服务器纯无状态:** 模型服务 + 网关(+ 可选 EPYC OCR)。不落用户数据、不落 prompt 日志。
- **网络:** LAN 或 Tailscale/WireGuard;冒烟用 SSH 隧道即可(见下)。

### 形态 B — AMD 单机(评委 / 演示)

*全部服务落在一台 AMD 机器上(手册 §2.2「单机」)。同一套镜像;把 `GATEWAY_URL` 指到本机。评委无需 Mac 数据面时用此形态。*

```
┌──────────────────────────── AMD 单机(有状态 + 算力) ────────────────────────────┐
│  capture ─▶ memoryd / ocrd / Honcho / Postgres / DATA_ROOT                      │
│                    │                                                             │
│                    └──▶ LiteLLM :4000 ─▶ brain / perceive / sentinel / fast / embed (ROCm) │
└──────────────────────────────────────────────────────────────────────────────────┘
```

服务器起停、VRAM 预算与模型下载详见 [`deploy/server/DEPLOY.md`](deploy/server/DEPLOY.md)。  
日常端口表与已知问题见 [`STATUS.md`](STATUS.md)。

---

## 评分对照(赛道 2)

| 评分维度 | 权重 | DejaView 怎么拿分 |
|---|---|---|
| 功能完整性与应用价值 | **60** | 逐窗口采集 → 哨兵 → OCR → 新颖度门 → perceive → 时间线 → Honcho 画像 → 带证据问答(`[event#id HH:MM app]`)。四柱 + 多窗口感知。 |
| AMD Radeon GPU 与 ROCm 优化 | **40** | W7900D 48 GB 五逻辑模型常驻;三层推理金字塔;llama.cpp HIP / gfx1100;存储/计算分离。**证据:** [`docs/benchmarks.md`](docs/benchmarks.md)(OCR A/B 已入;**ROCm 消融章节由 P3.1 补全**)。 |

---

## 形态 A 冒烟(干净机器)

前置:Docker Desktop · [`uv`](https://github.com/astral-sh/uv) · SSH 别名 `radeon-cloud` 指向已就绪的 AMD 推理栈(见 [`DEPLOY.md`](deploy/server/DEPLOY.md))。先复制环境模板:

```bash
cp .env.example .env
cp deploy/mac/honcho.env.example deploy/mac/honcho.env   # 按需改;本地冒烟无需真实密钥
```

最小命令(完整起服表与排障:[`STATUS.md`](STATUS.md) · 手册 §12.5):

```bash
# 1. 数据层 + Honcho(Mac)
make data-up
docker compose -f deploy/mac/compose.honcho.yml up -d
# 一次性:把 Honcho pgvector 维数对齐到 1024
docker compose -f deploy/mac/compose.honcho.yml run --rm --no-deps \
  --entrypoint /app/.venv/bin/python honcho-api scripts/configure_embeddings.py --yes

# 2. AMD 推理(4 小模型常驻;brain 按需——先查 VRAM)
ssh radeon-cloud "cd /root/dejaview-launch && ./server-stack.sh up embed fast sentinel perceive"

# 3. 隧道(服务器网关不暴露公网)
ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud

# 4. ocrd · memoryd · capture
cd services/ocrd && nohup uv run python -m ocrd > /tmp/ocrd.log 2>&1 &
MEMORYD_REAL_PIPELINE=1 GATEWAY_URL=http://127.0.0.1:14000/v1 \
  nohup uv run --project services/memoryd python -m memoryd > /tmp/memoryd.log 2>&1 &
cd clients/capture && CAPTURE_DEVICE_ID=dev uv run python -m capture

# 5. agentd 问答(需要时先在服务器起 brain:./server-stack.sh up brain)
GATEWAY_URL=http://127.0.0.1:14000/v1 uv run --project services/agentd python -m agentd
curl -s http://127.0.0.1:8101/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"我最近遇到过哪些 GPU 报错?请引用事件。"}]}'
```

| 层 | 起法 | 端口 |
|---|---|---|
| 数据层 | `make data-up` | pg `:5433` · redis `:6380` |
| Honcho | `compose.honcho.yml up -d` | `:8100` |
| 隧道 | `ssh -L 14000:…:4000` | Mac `:14000` → 服务器 `:4000` |
| ocrd | `uv run python -m ocrd` | `:8006` |
| memoryd | `MEMORYD_REAL_PIPELINE=1 … python -m memoryd` | `:8090` |
| agentd | `python -m agentd` | `:8101` |
| capture | `python -m capture` | — |

---

## 逻辑模型名表

应用代码只允许出现下列逻辑名(经 `GATEWAY_URL` 调用)。物理路由只在 `deploy/server/litellm.yaml`。

| 逻辑名 | 角色 | 物理模型 | 端口 |
|---|---|---|---|
| `brain` | 深层:推理 / 规划 / 视觉 / 写作 | ThinkingCap-Qwen3.6-27B(+ mmproj) | 8001 |
| `perceive` | 中层:读屏理解、转写、Honcho deriver 基线 | Gemma 4 E4B(+ mmproj) | 8002 |
| `sentinel` | 快车道·视觉:隐私分类 | MiniCPM-V 4.6 Q4_K_M(+ mmproj) | 8003 |
| `fast` | 快车道·文本:新颖度 / 合并 / 打标 | MiniCPM5-1B | 8005 |
| `embed` | 全部向量化(查询侧加指令前缀) | Qwen3-Embedding-0.6B(1024 维) | 8004 |
| `ocrd`*(非 LLM)* | 确定性逐字 OCR | PP-OCRv6 / rapidocr(CPU) | 8006 |

**切云三纪律(仅开发期):** ① **`sentinel` 永远本地**——它看的是未过滤画面。② 切换 `embed` 必须全量重建索引。③ 比赛演示与提交视频必须**全本地**。

---

## 隐私与数据主权

- 用户记忆(Postgres、Redis、`DATA_ROOT` 截图/录音/文档、审计日志)只在**你自己的设备**——从不落 AMD 算力节点。
- 采集端:**零落盘**(内存处理 → POST → 丢弃)。哨兵 `block` 帧只写审计——不 OCR、不落图。
- 仓库只有**合成测试资产**(无真实 PII、无 API key)。若跑过真实采集,公开演示前请清库。
- SearXNG 默认 **disabled**(与「数据不出设备」叙事冲突)。

---

## 许可证

第三方许可证将汇总于 [`docs/licenses.md`](docs/licenses.md)(P3.5)。预览:

- **Apache-2.0:** ThinkingCap · MiniCPM · Honcho · llama.cpp · PaddleOCR · Qwen3-Embedding  
- **Gemma License:** Gemma 4 E4B — **单独标注**  
- **MIT:** LiteLLM · MarkItDown · Open WebUI  

禁止引入 AGPL 代码(OpenRecall 只作思路参考)。

---

## 状态与延伸阅读

**TASKBOARD:** G0+M+D **33/33 accept**。全链路已验收;54 分钟真实运行通过。Phase 3 材料(ROCm 消融、Grafana、演示视频、licenses)进行中——见 [`STATUS.md`](STATUS.md)。

| 文档 | 用途 |
|---|---|
| [`STATUS.md`](STATUS.md) | 人话快照:起服表、已知问题、下一步——**先读** |
| [`docs/EXECUTION_HANDBOOK.md`](docs/EXECUTION_HANDBOOK.md) | 唯一事实来源(架构 / 规格 / 交接 §12) |
| [`docs/verification-log.md`](docs/verification-log.md) | 已解 `[VERIFY]` 与踩坑 |
| [`docs/benchmarks.md`](docs/benchmarks.md) | OCR A/B + ROCm 消融(P3.1) |
| [`deploy/server/DEPLOY.md`](deploy/server/DEPLOY.md) | AMD 服务器运维 / VRAM / 隧道 |
| [`TASKBOARD.json`](TASKBOARD.json) | 权威任务状态机 |

## 目录

```
docs/             手册、verification-log、benchmarks、模型清单
deploy/server/    GPU 侧启动脚本、网关、DEPLOY.md、download-models.sh
deploy/mac/       数据侧 compose(postgres/redis/honcho)、Metal llama-launch
services/         memoryd · ocrd · agentd
clients/capture/  逐窗口截屏(macOS MVP)
third_party/      Honcho submodule @ 340175ad
tests/assets/     合成测试资产——零真实 PII
```
