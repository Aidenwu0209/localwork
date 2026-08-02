# DejaView 执行任务指令(Phase 3 收尾 · 2026-07-28)

## 你的角色
执行工程师。全链路已通;G0+M+D 33/33 + **P3.1–P3.7 全部 accept**。
P3.1 正式 run 为 `p31-w7900d-20260728T075653Z`,证据在
`docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/`。正式六幕成片
在 `docs/assets/demo/dejaview-p34-six-act-20260802.mp4`。比赛截止
**2026-08-06 23:59 UTC+8**。评分
60+40。

## 必读(按序)
1. `docs/EXECUTION_HANDBOOK.md` **§12**(含聊天决策摘要——产品/架构/模型/纪律,**勿改叙事**)
2. `STATUS.md`
3. `docs/verification-log.md` · `deploy/server/DEPLOY.md` ·
   `docs/benchmarks.md` §2 ·
   `docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/`
4. `TASKBOARD.json`

## 环境
- 仓库:`/Users/wu/Projects/Aidenwu0209/localwork/` · `git@github.com:Aidenwu0209/localwork.git`
- 当前服务器:`ssh radeon-cloud`(= `root@36.150.116.206 -p 31357`;
  实例 `u-15420-7be0d6c9`)。W7900D / ROCm 7.2.1。旧 `:30147` /
  `:30189` 是历史端口。
- P3.4 正式成片后 gateway 与五模型角色仍在当前实例常驻。实例重启或
  重新起模型前先 `rocm-smi` + `server-stack.sh status`;
  brain 共租时用 Q6_K、先停 perceive、MTP 默认关(加载后仍保留 ≥6 GiB
  才可开),勿碰未知 KFD 进程或 OOM Dolphin。
- 隧道:`ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud`
- **不要重跑 P3.1 正式矩阵**;后续只复用已落仓证据与
  `docs/benchmarks.md` 结论。

## 任务优先级
1. 不重跑 P3.1–P3.7;保持已落仓证据不变。
2. 只按手册 §10 完成人工提交检查(注册、Rules、仓库公开、服务器演示数据)。

领取:`false`/`blocked` 且 depends 全 accept → `doing` → 完成 verify → `accept` + note → commit + push。

## 纪律(强制)
1. 作者只能 `Aidenwu0209 <1418557225@qq.com>`;**禁止任何 AI trailer**。Cursor 可能自动加 `Co-authored-by: Cursor`——每次提交后 `git log -1 --format='%B'` 核对;有则 `commit-tree` 重写再 push。
2. 勿重做已 accept 任务;勿改产品叙事与五模型分层;勿追 Honcho 上游;勿 `git add third_party/honcho`。
3. 真实 PII/密钥不上 git;演示前清库。
4. `[VERIFY]` 写入 `docs/verification-log.md`。

## 完成定义
保持 P3.1/P3.2/P3.4 已落仓证据不变;不要扩展产品范围。只在人工确认后
勾手册 §10 对应提交项。
