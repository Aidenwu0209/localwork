# DejaView 执行任务指令(Phase 3 收尾 · 2026-07-28)

## 你的角色
执行工程师。全链路已通;G0+M+D 33/33 + P3.3/5/6/7 已 `accept`。你只做剩余 **P3.1 / P3.4 / P3.2**。比赛截止 **2026-08-06 23:59 UTC+8**(约 9 天)。评分 60+40。

## 必读(按序)
1. `docs/EXECUTION_HANDBOOK.md` **§12**(含聊天决策摘要——产品/架构/模型/纪律,**勿改叙事**)
2. `STATUS.md`
3. `docs/verification-log.md` · `deploy/server/DEPLOY.md` · `docs/benchmarks.md` §2
4. `TASKBOARD.json`

## 环境
- 仓库:`/Users/wu/Projects/Aidenwu0209/localwork/` · `git@github.com:Aidenwu0209/localwork.git`
- 服务器:`ssh radeon-cloud`(= `root@36.150.116.200 -p 30147`)。W7900D / ROCm 7.2。
- 起模型前 `rocm-smi`;brain 用 Q6_K 并先停 perceive;勿 OOM Dolphin。
- 隧道:`ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud`
- **注意**:2026-07-23 起 SSH `:30147` 多次不通 → P3.1 blocked。通了先冒烟再跑 brain 消融。

## 任务优先级
1. **P3.1** 收尾:`benchmarks.md §2` 填 brain 量化×MTP×并发 + perceive `-np`;≥3 次中位;截图;`blocked→accept`
2. **P3.4** 演示视频 ≤5min(手册 §9 六幕 + 拔网线)
3. **P3.2** Grafana 一屏(可与视频穿插;时间紧可砍成极简)

领取:`false`/`blocked` 且 depends 全 accept → `doing` → 完成 verify → `accept` + note → commit + push。

## 纪律(强制)
1. 作者只能 `Aidenwu0209 <1418557225@qq.com>`;**禁止任何 AI trailer**。Cursor 可能自动加 `Co-authored-by: Cursor`——每次提交后 `git log -1 --format='%B'` 核对;有则 `commit-tree` 重写再 push。
2. 勿重做已 accept 任务;勿改产品叙事与五模型分层;勿追 Honcho 上游;勿 `git add third_party/honcho`。
3. 真实 PII/密钥不上 git;演示前清库。
4. `[VERIFY]` 写入 `docs/verification-log.md`。

## 完成定义
P3.1 accept(brain 矩阵有实测数字)+ P3.4 成片 + P3.2 至少有演示可用大屏(或诚实砍并在 §10 note)。然后勾手册 §10 提交清单。
