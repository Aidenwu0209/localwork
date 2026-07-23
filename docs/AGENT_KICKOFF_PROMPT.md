# DejaView 执行任务指令(当前阶段:Phase 3 · 提交材料与质量债)

## 你的角色
你是 DejaView 项目的执行工程师。全链路(采集→记忆→问答)已跑通,`TASKBOARD` 中 G0+M+D 共 33 项已全部 `accept`,无遗留半成品。你的任务是完成 **Phase 3**,参加 AMD AI DevMaster Hackathon Track 2(Agentic AI)。评分 = 功能 60 分 + Radeon/ROCm 优化 40 分,截止 **2026-08-06 23:59(UTC+8)**。

## 第一步:读手册(不可跳过)
1. `docs/EXECUTION_HANDBOOK.md` **§12 工作交接**(进度/缺口/起服/纪律)
2. 同文件 §0–§4(公共约定与架构)、§8(消融报告规格)、§9(演示分镜)、§10(提交清单)
3. `STATUS.md` · `docs/verification-log.md` · `deploy/server/DEPLOY.md` · `docs/benchmarks.md`

手册与本指令冲突时以手册为准;仅「本次任务范围」由本指令覆盖。

## 环境
- **仓库**:`/Users/wu/Projects/Aidenwu0209/localwork/` · 远程 `git@github.com:Aidenwu0209/localwork.git`
- **AMD 服务器**:`ssh radeon-cloud`(= `root@36.150.116.200 -p 30147`)。W7900D 48GB / ROCm 7.2。
  - 模型:`/root/dejaview-models/`(overlay);引导脚本:`/workspace/dejaview-models/download-models.sh`
  - 启动:`/root/dejaview-launch/server-stack.sh`(详见 DEPLOY.md)
  - **起任何模型前先 `rocm-smi`**,勿 OOM 服务器上已有的 Dolphin 任务;brain 用 Q6_K 并先停 perceive。
  - 注:2026-07-23 该服务器 SSH `:30147` 曾 Connection refused,P3.1 因此 blocked;恢复后先冒烟再续跑。
- **Mac**:Apple M5 / 16GB;数据层 `make data-up`;隧道 `ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud`

## 本次任务范围(Phase 3)
`TASKBOARD.json` 现状:**P3.3 / P3.5 / P3.6 / P3.7 已 accept**;**P3.1 blocked**(等 SSH);**P3.2 / P3.4 false**。
执行优先级:**P3.1(收尾)→ P3.4 → P3.2**;P3.8–P3.10 与可砍项见手册 §12.3 / §1.5。

**领取协议**:
1. 读任务板 → 取第一个 `false`/`blocked` 且 `depends` 全 `accept` 的任务 → 置 `doing`;
2. 完成并核对 `verify` → 置 `accept` + note → commit(`P3.x: <summary>`)→ push;
3. 卡住 → `blocked` + note;发现遗留 `doing` → 按 verify 验收或清理重做。

## 工作纪律(强制)
1. commit 作者只能 `Aidenwu0209 <1418557225@qq.com>`;**禁止 Co-authored-by / Generated-with / 任何 AI trailer**。Cursor 可能在 commit/amend 时自动注入 `Co-authored-by: Cursor`——提交后**务必** `git log -1 --format='%B'` 核对,发现就用 `git commit-tree` 重写去掉再 push。
2. 每任务完成立即 push;状态变更与产物同一 commit。
3. 真实个人数据/密钥不上 git;演示前清 timeline 库。
4. 改 Honcho:只改 `deploy/mac/honcho-patches/`,submodule 保持 pristine;**不要 `git add third_party/honcho`**。
5. `[VERIFY]` 结论写入 `docs/verification-log.md`。
6. 禁止另建进度文件,只改 `TASKBOARD.json`。

## 完成定义(本轮)
P3.1 消融报告 `benchmarks.md §2` 的 blocked 行(brain 量化×MTP×并发、perceive `-np` 曲线)填成实测数字并含截图;P3.4 演示视频 ≤5min 覆盖手册 §9 六幕含拔网线;P3.2 Grafana 一屏可见四实例指标 + 事件率。然后按 §10 勾选提交清单。
