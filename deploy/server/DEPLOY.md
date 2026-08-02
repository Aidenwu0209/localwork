# AMD 服务器部署指南(算力端)

> 连接入口统一为本机已授权的 `ssh radeon-cloud` 别名。Radeon Cloud
> 实例和公网端口会更换;不在公开发布文档固化临时 IP、端口或实例 ID。
> 硬件:AMD Radeon PRO W7900D 48GB(gfx1100)+ 双路 EPYC 9334 / 128 逻辑核
> + 1007.56 GiB RAM。
> 已验收环境为 ROCm 7.2.1。P3.1 正式 run 时容器分配 GPU 上无 KFD
> 共租进程;早期共享实例曾有 Dolphin-v2-ROCm 常驻(~10.6GB
> VRAM)。任何实例一旦出现 Dolphin/未知 KFD 进程都视为共租,
> **不得影响**。

---

## 0. 前置:只读体检(每次操作前先做)

```bash
ssh radeon-cloud \
  "rocm-smi --showmeminfo vram --showuse; echo '---'; \
   rocm-smi --showpids verbose; echo '---'; \
   cd /root/dejaview-launch && ./server-stack.sh status"
```
确认 alias 命中已授权的当前实例,GPU/KFD 进程身份清楚且 VRAM 足够。
禁止凭旧端口、旧 PID 判断 Dolphin;若有任何未知共租进程,先停止并确认操作范围。

## 1. 推理引擎(llama.cpp HIP,已编译)

已在 `/root/llama.cpp/build/bin/llama-server`(commit
`76f46ad29d61fd8c1401e8221842934bf62a6064`,GGML_HIP=ON,gfx1100)。
P3.1 正式 run 记录的二进制 SHA256 为
`90d82cee630d8340b0f1f629e4675a23b7189b49f2d9869ed6efb424cfdeb55f`;
核验证据见
`docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/llama-build-verified.txt`。

若需重编译(服务器重建后):
```bash
ssh radeon-cloud
cd /root/llama.cpp   # 若没了:GIT_SSL_NO_VERIFY=true git clone https://github.com/ggml-org/llama.cpp
cmake -B build -DGGML_HIP=ON -DAMDGPU_TARGETS=gfx1100 -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DLLAMA_CURL=OFF
nohup cmake --build build --config Release -j32 > /root/llama-build.log 2>&1 &   # -j32 不抢 Dolphin CPU
# 等 ~5 分钟到 100%
```

> 镜像 gitclone/ghfast/kkgithub 全不可用;GitHub 直连需 `GIT_SSL_NO_VERIFY=true`(证书链问题)。
> `cmake` 若缺失:`apt-get install -y cmake`。
> `lemonade-sdk` 预编译包在 PyPI 没有(同名的是别的 parsing 工具),源码编译是确定路径。

## 2. 模型权重(已在 /root/dejaview-models/,overlay 易失)

| 逻辑名 | 文件 | 大小 |
|---|---|---|
| brain | ThinkingCap-Qwen3.6-27B-Q8_0.gguf + mmproj-f16 | 28GB + 0.9GB |
| brain(共享 GPU 用) | ThinkingCap-Qwen3.6-27B-Q6_K.gguf | 21GB |
| brain(P3.1 benchmark) | ThinkingCap-Qwen3.6-27B-Q4_K_M.gguf | 16.8GB |
| perceive | gemma-4-E4B-it-Q8_0.gguf + mmproj-BF16 | 7.5GB + 0.9GB |
| sentinel | MiniCPM-V-4_6-Q4_K_M.gguf + mmproj-f16 | 0.5GB + 1.1GB |
| fast | MiniCPM5-1B-Q8_0.gguf | 1.1GB |
| embed | Qwen3-Embedding-0.6B-Q8_0.gguf | 0.6GB |

重建(overlay 丢了):`/workspace/dejaview-models/download-models.sh`(wget hf-mirror,断点续传)。
sha256:`/workspace/dejaview-models/sha256.txt` + `deploy/server/sha256.txt`。
当前 bootstrap 已钉 revision 并覆盖 brain Q8/Q6/Q4;结束时执行
`sha256sum -c`,不要手工替换量化文件或静默回退。

## 3. 启动栈(server-stack.sh)

脚本在服务器 `/root/dejaview-launch/`(从仓库 `deploy/server/llama-launch/` 同步)。venv 在 `/root/llamavenv`(装了 `litellm[proxy]` 1.93)。

```bash
ssh radeon-cloud
cd /root/dejaview-launch

# 单机/演示拓扑可启 4 个小角色(~12GB);日常 split 拓扑的 sentinel 留在数据主权端
./server-stack.sh up embed fast sentinel perceive
./server-stack.sh status
# brain 按需(先停 perceive 腾位,brain 能兼任 perceive)
./server-stack.sh down perceive && ./server-stack.sh up brain
./server-stack.sh down brain && ./server-stack.sh up perceive   # 用完恢复
```

`server-stack.sh` 命令:`up <role...>` / `down [role...]` / `status`。
brain 的量化档:`BRAIN_QUANT=Q8_0 ./brain.sh`(默认 Q6_K,共享 GPU 必须用 Q6_K)。

五个名称是稳定的逻辑角色,不是“任何时候五套权重全部同时常驻”的承诺。
分离日常拓扑通常只在 AMD 端启 `embed fast perceive`,Sentinel 在数据主权端,
brain 按需起停;单机/演示拓扑才根据实测显存余量编排全部角色。

### 3.1 同步并启用 P3.2 实时指标

五个 launcher 必须使用当前仓库版本，才能带上 llama.cpp `--metrics`；ROCm
exporter 也必须同步到服务器。先在 Mac 仓库根目录执行：

```bash
ssh radeon-cloud 'mkdir -p /root/dejaview-launch/monitoring'
rsync -a deploy/server/llama-launch/ radeon-cloud:/root/dejaview-launch/
rsync -a deploy/server/monitoring/ radeon-cloud:/root/dejaview-launch/monitoring/
```

随后在服务器先做 GPU/KFD 与 DejaView 托管进程的只读清点，再只重启明确
命名的小模型角色，禁止广域 `pkill`、禁止碰 Dolphin 或未知 KFD 进程。
`server-stack.sh` 会在 `${DEJAVIEW_RUNTIME_DIR:-/tmp/dejaview}` 中保存带进程
启动指纹的 PID 记录；不要用旧版单行 PID 文件自行终止进程：

```bash
rocm-smi --showmeminfo vram --showuse
rocm-smi --showpids verbose
cd /root/dejaview-launch
./server-stack.sh status
./server-stack.sh down embed fast sentinel perceive
./server-stack.sh up embed fast sentinel perceive
if ss -H -ltnp 'sport = :9393' | grep -q .; then
  echo "指标端口 :9393 已占用，先核对现有进程"
  ss -H -ltnp 'sport = :9393'
else
  python3 monitoring/rocm_smi_exporter.py \
    >/tmp/dejaview-rocm-exporter.log 2>&1 &
  exporter_pid=$!
  sleep 1
  if ! kill -0 "$exporter_pid" 2>/dev/null; then
    tail -50 /tmp/dejaview-rocm-exporter.log >&2
    exit 1
  fi
  echo "$exporter_pid" >/tmp/dejaview-rocm-exporter.pid
fi
```

Mac 侧 memoryd 也要在同步当前代码后，先用
`pgrep -af 'python -m memoryd'`核对 PID，再只终止该已核对进程，并按
`STATUS.md`原环境重启。新的 `/metrics` 响应使用 Prometheus 0.0.4
Content-Type；Grafana/Prometheus 起服与隧道命令见
`deploy/mac/monitoring/README.md`。

## 4. VRAM 预算(共享 GPU 编排)

| 配置 | VRAM | 与 Dolphin(10.6GB) |
|---|---|---|
| 4 小模型常驻 | ~12GB | 共 22.6GB,安全 |
| + brain Q6_K(停 perceive) | ~21GB | 共 ~43GB,留 5GB,**临界但可行** |
| + brain Q8_0(停 perceive) | ~28GB | 共 ~50GB > 48GB,**OOM,禁止** |
| 全 5 模型 + Dolphin | — | 不可能共存 |

**规则**:起 brain 前先 `rocm-smi` 确认余量 ≥ 22GB;起 brain 时停
perceive;brain Q6_K 是共享 GPU 的硬上限。MTP 在本次 build 增加
4.95 GiB resident VRAM;共租默认关,只有加载后仍保留 ≥6 GiB 余量才开。

P3.1 正式 run 在当前无容器内 KFD 共租的 replacement instance 上测得:

| 场景 | resident / sampled peak VRAM GiB |
|---|---:|
| brain Q8_0,MTP off / on | 29.36 / 29.43 · 34.31 / 34.43 |
| brain Q6_K,MTP off / on | 23.49 / 23.83 · 28.44 / 29.23 |
| brain Q4_K_M,MTP off / on | 18.56 / 18.90 · 23.51 / 24.10 |
| perceive Q8_0,`-np` 1 / 2 / 4 | 6.38 / 6.51 · 6.41 / 6.55 · 6.48 / 6.62 |

这些数是独占条件的优化证据,不能覆盖上表的 Dolphin 共租安全上限。

## 5. Mac 怎么连(SSH 隧道)

服务器网关硬绑定 `127.0.0.1:4000`，不接受公网或局域网直接访问。Mac 只走
SSH 隧道：
```bash
ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud
# Mac 用 GATEWAY_URL=http://127.0.0.1:14000/v1
```
隧道抖动会导致 httpx ReadTimeout —— memoryd 的 GatewaySentinel/Perceive 已加
retry 与长 timeout（180/240s）。现场也保持该回环绑定与隧道边界，不改成
`0.0.0.0`。

## 6. ocrd 在哪跑

ocrd 是 CPU 微服务(确定性,不占 GPU),**跑在 Mac**(`services/ocrd`,`OCR_BACKEND=rapidocr` 默认)。生产(EPYC)切 `OCR_BACKEND=paddleocr` 用 PP-OCRv6(精度 +9 点,见 docs/benchmarks.md)。

## 7. P3.1 ROCm 消融报告(accept,40 分主证据)

正式 run:`p31-w7900d-20260728T075653Z`。证据目录:
`docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/`。

- brain 全因子:Q8_0/Q6_K/Q4_K_M × MTP off/on × client concurrency
  1/4/8,共 18 cells;
- perceive:真实 multimodal 路径下 `-np`/client concurrency
  `(1,1)/(2,2)/(4,4)`,共 3 cells;
- 每 cell 1 次 warm-up + 3 次实测,summary 从原始 JSON 重算中位数;
- MTP-on 有非零 draft,温度 0 的 on/off 输出 parity **PASS**;
- 生产 brain 保持 Q6_K;MTP 仅在独占或加载后确认 ≥6 GiB 余量时开启;
  Q8_0 并发 4/8 保持 MTP-off;
- manifest 绑定 llama.cpp commit、二进制/权重/提示词/图像 hash,
  `SHA256SUMS` 覆盖落仓证据。

结果表见 `docs/benchmarks.md` §2;核验结论见
`docs/verification-log.md`;派生总表见证据目录 `p31-summary.md`。
不要重跑正式矩阵。

**退出状态:**harness 按安全策略留下 gateway、sentinel、fast、embed、
perceive、brain 全部 `down`;before/after scoped VRAM 均为 28,016,640 B。
开始 P3.4/P3.2 前先执行 §0 体检,再只起所需角色。
