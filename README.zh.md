# DejaView · 全本地数字记忆体

> 持续感知你的屏幕,把数字生活变成**可问答、带证据的记忆**;用 Honcho 心理建模理解「你是谁」;隐私哨兵把关「什么不该被记住」。**原始画面先经过设备本地 Sentinel 隐私门,主要 Agentic 算力再由 Radeon PRO W7900D(ROCm)执行**,数据始终留在用户自己的设备上,并保留已验证的 Local Metal 降级能力。当前服务不支持音频和文档写入。

产品代号:**DejaView**(déjà vu + view:你的机器替你「似曾相识」)。
英文主文档:[README.md](README.md)

面向 [AMD AI DevMaster Hackathon](https://luma.com/amd-4dhi) · **赛道 2 · Agentic AI**。

---

## 为什么做这个(获奖叙事)

微软 Recall 因隐私几乎翻车、Rewind 卖身——这个产品形态被云端判了死刑。我们用一块 **48 GB Radeon** 把它安全复活,且比它们多两层:

1. **用户心理建模**(Honcho reasoning-first 画像 + dialectic 问答——不只记得,还理解)
2. **模型级隐私哨兵**(本地记忆内部也有权限分级;敏感帧在 OCR / 落盘前就被拦截)

**同品类先例:** Microsoft Recall(云端信任危机)、Rewind.ai(已转向)、OpenRecall(开源 AGPL——截屏+OCR+搜索,无理解层)。

**我们的差异:** ① Honcho 用户画像 · ② 入库前隐私哨兵 · ③ Agent 任务闭环(tool calling、日报多 Agent 流) · ④ 五个逻辑模型角色与经实测的显存编排 + ROCm 优化报告 · ⑤ 存储/计算分离的数据主权架构。

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
│ 逐窗口截图         │──▶│ 本地 Sentinel → ocrd(PP-OCR,CPU)          │──▶│ brain :8001 · perceive :8002    │
│ dhash · 零落盘     │   │ Postgres+pgvector     timeline+kb+audit│   │ fast :8005                     │
└────────────────────┘   │ DATA_ROOT(~/dejaview-data)             │   │ embed :8004 ·(可选 EPYC ocrd)   │
                         └────────────────────────────────────────┘   └────────────────────────────────┘
                         SENTINEL_GATEWAY_URL 保持本地;仅放行帧才会使用 GATEWAY_URL
```

- **有状态只在数据主权端:** Postgres、Redis、截图与审计日志。单一可移植 `DATA_ROOT`;当前不支持音频/文档写入。
- **采集客户端:** macOS 仍是比赛已验证客户端;P3.19 已加入 Win32/mss Windows 后端,内存处理像素并在安全桌面暂停。完整 Windows 产品栈仍在门禁中,见 [`deploy/windows/README.md`](deploy/windows/README.md)。
- **服务器纯无状态:** 模型服务 + 网关(+ 可选 EPYC OCR)。不落用户数据、不落 prompt 日志。
- **隐私顺序:** memoryd 先把原始帧发送到 `SENTINEL_GATEWAY_URL` 配置的本地 Sentinel；仅放行帧才会经 `GATEWAY_URL` 请求 Radeon 算力。
- **网络:** LAN 或 Tailscale/WireGuard;冒烟用 SSH 隧道即可(见下)。

### 形态 B — AMD 单机(评委 / 演示)

*全部服务落在一台 AMD 机器上(手册 §2.2「单机」)。同一套镜像;把 `GATEWAY_URL` 指到本机。评委无需 Mac 数据面时用此形态。*

```
┌──────────────────────────── AMD 单机(有状态 + 算力) ────────────────────────────┐
│  capture ─▶ memoryd / 本地 Sentinel / ocrd / Honcho / Postgres / DATA_ROOT       │
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
| AMD Radeon GPU 与 ROCm 优化 | **40** | W7900D 48 GB 五个逻辑角色与显存感知编排;三层推理金字塔;llama.cpp HIP / gfx1100;存储/计算分离。**证据:** [`docs/benchmarks.md`](docs/benchmarks.md)(OCR A/B 已入;**ROCm 消融章节由 P3.1 补全**)。 |

上表是 Track 2 的 **100 分基础评分**。本地竞赛路径没有申报可选的云模型优化加分。

---

## 演示视频

[**观看 3 分 15 秒英文主旁白提交版**](docs/assets/demo/dejaview-p34-six-act-20260802-en-3m.mp4)——真实提交材料片头后接完整隔离实录：合成数据、带证据回忆、Honcho、隐私拦截、Radeon 日报 Agent、可见断开已验证的 SSH 计算链路，以及 Local Metal fallback 完成第二次日报。Wi-Fi 保持不动。[原始验收证据片](docs/assets/demo/dejaview-p34-six-act-20260802.mp4)保持不变；提交版替换为英文旁白并提供完整英文字幕。

完整性、字幕哈希与时间码：[`docs/assets/demo/p34-video-manifest.json`](docs/assets/demo/p34-video-manifest.json)；可编辑字幕：[`dejaview-p34-six-act-20260802-en-3m.srt`](docs/assets/demo/dejaview-p34-six-act-20260802-en-3m.srt)。提交版时长 3:15.2，满足官方 3–5 分钟窗口。

### 提交材料直达

- 项目规格：[Markdown](docs/submission/PROJECT_SPECIFICATION.md) · [可编辑 DOCX](docs/submission/DejaView-Project-Specification.docx)
- 补充演示：[可编辑 PPTX](docs/submission/DejaView-Track2-Presentation.pptx)
- 视频：[英文主旁白 MP4](docs/assets/demo/dejaview-p34-six-act-20260802-en-3m.mp4) · [可编辑 SRT](docs/assets/demo/dejaview-p34-six-act-20260802-en-3m.srt) · [清单](docs/assets/demo/p34-video-manifest.json)
- Radeon/ROCm 优化：[benchmark 报告](docs/benchmarks.md) · [P3.1 哈希证据](docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/)

---

## 形态 A 冒烟(干净机器)

前置:Docker Desktop · [`uv`](https://github.com/astral-sh/uv) · `llama-server`
与已下载的本地 Sentinel 权重 · SSH 别名 `radeon-cloud`
指向已授权的 AMD 推理栈(见 [`DEPLOY.md`](deploy/server/DEPLOY.md))。
下列命令均从克隆根目录执行;`REPO_ROOT` 保证后台进程不受后续 `cd` 影响:

Windows 使用 PowerShell 包装器运行等价生命周期:`deploy\\windows\\dejaview.cmd doctor`、
`tunnel-up`、`product-up`、`product-status`、`capture`;本地 Sentinel 门禁不变,
原始画面永不经过 Radeon 隧道。

```bash
git submodule update --init --recursive
make setup
cp .env.example .env
cp deploy/mac/honcho.env.example deploy/mac/honcho.env   # 按需改;本地冒烟无需真实密钥
set -a; source .env; set +a   # memoryd 直接读取已导出的环境变量
make doctor                    # 只读;启服前运行时端点可以是 WARN
make test                      # 离线第一方契约;CI 跑同一命令
```

最小命令(完整起服表与排障:[`STATUS.md`](STATUS.md) · 手册 §12.5):

```bash
# 1. 设备本地 Sentinel;未过滤像素只能走这个网关
./deploy/mac/llama-launch/dev-stack.sh up sentinel

# 2. 只处理已放行内容的 AMD 推理;brain 按需启动,先查 VRAM
ssh radeon-cloud "cd /root/dejaview-launch && ./server-stack.sh up embed fast perceive"

# 3. 隧道(服务器网关不暴露公网)
ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud

# 4. 按就绪门禁启数据层、Honcho、OCR、memoryd 和 agentd。
#    已导出 URL 保证 Sentinel 本地、Radeon 主路、Metal 降级。
make product-up
# 每个全新 Honcho 数据库执行一次:将 pgvector 对齐为当前 embed 的 1024 维
docker compose -f deploy/mac/compose.honcho.yml run --rm --no-deps \
  --entrypoint /app/.venv/bin/python honcho-api scripts/configure_embeddings.py --yes
make product-status

# 5. capture 保持前台,便于看到屏幕录制授权
make capture

# 日常产品页:http://127.0.0.1:8101/(深度问答时按需起 brain)
curl -s http://127.0.0.1:8101/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"我最近遇到过哪些 GPU 报错?请引用事件。"}]}'
```

| 层 | 起法 | 端口 |
|---|---|---|
| 产品生命周期 | `make product-up/status/down` | 受管本地服务 |
| 数据层 | 由 `product-up` 启动 | pg `:5433` · redis `:6380` |
| Honcho | 由 `product-up` 启动 | `:8100` |
| 本地 Sentinel | `dev-stack.sh up sentinel` | 网关 `:4000` · 模型 `:8003` |
| 隧道 | `ssh -L 14000:…:4000` | Mac `:14000` → 服务器 `:4000` |
| ocrd | 由 `product-up` 启动 | `:8006` |
| memoryd | 由 `product-up` 启动 | `:8090` |
| agentd | 由 `product-up` 启动 | `:8101` |
| capture | `make capture` | — |

---

## 逻辑模型名表

应用代码使用下列逻辑名。`sentinel` 通过独立且本地的
`SENTINEL_GATEWAY_URL` 调用，其他经网关的阶段使用 `GATEWAY_URL`。物理路由只在
`deploy/server/litellm.yaml`。

| 逻辑名 | 角色 | 物理模型 | 端口 |
|---|---|---|---|
| `brain` | 深层:推理 / 规划 / 视觉 / 写作 | ThinkingCap-Qwen3.6-27B(+ mmproj) | 8001 |
| `perceive` | 中层:读屏理解、Honcho deriver 基线 | Gemma 4 E4B(+ mmproj) | 8002 |
| `sentinel` | 快车道·视觉:隐私分类 | MiniCPM-V 4.6 Q4_K_M(+ mmproj) | 8003 |
| `fast` | 快车道·文本:新颖度 / 合并 / 打标 | MiniCPM5-1B | 8005 |
| `embed` | 全部向量化(查询侧加指令前缀) | Qwen3-Embedding-0.6B(1024 维) | 8004 |
| `ocrd`*(非 LLM)* | 确定性逐字 OCR | PP-OCRv6 / rapidocr(CPU) | 8006 |

**切云三纪律(仅开发期):** ① **`sentinel` 永远本地**——它看的是未过滤画面，须用 `SENTINEL_GATEWAY_URL` 单独配置。② 切换 `embed` 必须全量重建索引。③ 比赛演示与提交视频必须**全本地**。

上表定义的是五个逻辑角色,不代表每种拓扑都把五套权重同时常驻。
日常分离拓扑把 Sentinel 放在数据主权端,`brain` 按需启动;单机/演示拓扑可按实测显存策略把各角色放到 Radeon。

---

## 隐私与数据主权

- 用户记忆(Postgres、Redis、`DATA_ROOT` 截图、审计日志)只在**你自己的设备**——从不落 AMD 算力节点。当前音频和文档入口均返回 `501 unsupported_media`。
- 采集端:**零落盘**(内存处理 → POST → 丢弃)。哨兵 `block` 帧只写审计——不 OCR、不落图。
- 采集端每 30 秒发送一次仅元数据 heartbeat，锁屏时也继续；帧响应的 `processing_state` 为 `stored`、`merged` 或 `blocked`。缺少屏幕录制权限时，capture 会在开始采集前以状态码 `2` 退出。
- 已入库事件通过原子 outbox 自动进入 Honcho。投影仅包含 activity/topics/app/time/event provenance;OCR、窗口标题、URL、截图和 blocked 帧永不进入,且可暂停、重试、幂等去重。
- 仓库只有**合成测试资产**(无真实 PII、无 API key)。若跑过真实采集,公开演示前请清库。
- SearXNG 默认 **disabled**(与「数据不出设备」叙事冲突)。

---

## 许可证

第三方许可证、Gemma 单独标注、用户数据不上云声明、以及手册 §10 就绪说明见 [`docs/licenses.md`](docs/licenses.md)。

- **Apache-2.0:** ThinkingCap · MiniCPM · Honcho · PaddleOCR · Qwen3-Embedding · Gemma 4(详见单独标注)
- **Gemma(单独标注):** Gemma 4 E4B perceive — 见 `docs/licenses.md`
- **MIT:** llama.cpp · LiteLLM · MarkItDown · Open WebUI

禁止引入 AGPL 代码(OpenRecall 只作思路参考)。

---

## 状态与延伸阅读

**TASKBOARD:** 当前 **48/49 accept**;P3.12–P3.18 成熟产品加固、发布复现
与证据化全链路验收均已完成;用户新增的最终比赛风险清零 P3.19 为 `doing`。已验收证据包括
全链路、ROCm 消融、Grafana、隐私/理解门和 ≤5 分钟故障切换视频——见 [`STATUS.md`](STATUS.md)。

| 文档 | 用途 |
|---|---|
| [`STATUS.md`](STATUS.md) | 人话快照:起服表、已知问题、下一步——**先读** |
| [`docs/EXECUTION_HANDBOOK.md`](docs/EXECUTION_HANDBOOK.md) | 唯一事实来源(架构 / 规格 / 交接 §12) |
| [`docs/verification-log.md`](docs/verification-log.md) | 已解 `[VERIFY]` 与踩坑 |
| [`docs/benchmarks.md`](docs/benchmarks.md) | OCR A/B + ROCm 消融(P3.1) |
| [`deploy/server/DEPLOY.md`](deploy/server/DEPLOY.md) | AMD 服务器运维 / VRAM / 隧道 |
| [`docs/submission/PROJECT_SPECIFICATION.md`](docs/submission/PROJECT_SPECIFICATION.md) | Track 2 英文项目规格(同目录含可编辑 DOCX) |
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
