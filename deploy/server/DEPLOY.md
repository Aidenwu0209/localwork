# AMD 服务器部署指南(算力端)

> 服务器:`ssh radeon-cloud`(本机别名,实际 `root@36.150.116.200 -p 30147`)。
> 硬件:AMD Radeon PRO W7900D 48GB(gfx1100)+ 双路 EPYC 128 核 + 503GB RAM。
> ROCm 7.2.0。共享:有一个 Dolphin-v2-ROCm 评估任务常驻(~10.6GB VRAM),**不得影响**。

---

## 0. 前置:只读体检(每次操作前先做)

```bash
ssh radeon-cloud "rocm-smi --showmeminfo vram --showuse; echo '---'; ps -p 20527 -o pid,etime,comm --no-headers"
```
确认 Dolphin 进程在、VRAM 余量足够(~37GB 可用给 DejaView)。

## 1. 推理引擎(llama.cpp HIP,已编译)

已在 `/root/llama.cpp/build/bin/llama-server`(commit 76f46ad29,GGML_HIP=ON,gfx1100)。

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
| perceive | gemma-4-E4B-it-Q8_0.gguf + mmproj-BF16 | 7.5GB + 0.9GB |
| sentinel | MiniCPM-V-4_6-Q4_K_M.gguf + mmproj-f16 | 0.5GB + 1.1GB |
| fast | MiniCPM5-1B-Q8_0.gguf | 1.1GB |
| embed | Qwen3-Embedding-0.6B-Q8_0.gguf | 0.6GB |

重建(overlay 丢了):`/workspace/dejaview-models/download-models.sh`(wget hf-mirror,断点续传)。
sha256:`/workspace/dejaview-models/sha256.txt` + `deploy/server/sha256.txt`。
brain Q6_K 单独下:`wget -c hf-mirror.com/.../ThinkingCap-Qwen3.6-27B-Q6_K.gguf`(手册 §2.4 应急档)。

## 3. 启动栈(server-stack.sh)

脚本在服务器 `/root/dejaview-launch/`(从仓库 `deploy/server/llama-launch/` 同步)。venv 在 `/root/llamavenv`(装了 `litellm[proxy]` 1.93)。

```bash
ssh radeon-cloud
cd /root/dejaview-launch

# 4 小模型常驻(~12GB,和 Dolphin 共存无压力)
./server-stack.sh up embed fast sentinel perceive
./server-stack.sh status
# brain 按需(先停 perceive 腾位,brain 能兼任 perceive)
./server-stack.sh down perceive && ./server-stack.sh up brain
./server-stack.sh down brain && ./server-stack.sh up perceive   # 用完恢复
```

`server-stack.sh` 命令:`up <role...>` / `down [role...]` / `status`。
brain 的量化档:`BRAIN_QUANT=Q8_0 ./brain.sh`(默认 Q6_K,共享 GPU 必须用 Q6_K)。

## 4. VRAM 预算(共享 GPU 编排)

| 配置 | VRAM | 与 Dolphin(10.6GB) |
|---|---|---|
| 4 小模型常驻 | ~12GB | 共 22.6GB,安全 |
| + brain Q6_K(停 perceive) | ~21GB | 共 ~43GB,留 5GB,**临界但可行** |
| + brain Q8_0(停 perceive) | ~28GB | 共 ~50GB > 48GB,**OOM,禁止** |
| 全 5 模型 + Dolphin | — | 不可能共存 |

**规则**:起 brain 前先 `rocm-smi` 确认余量 ≥ 22GB;起 brain 时停 perceive;brain Q6_K 是共享 GPU 的硬上限。

## 5. Mac 怎么连(SSH 隧道)

服务器网关绑 `0.0.0.0:4000` 但端口没暴露公网。Mac 走隧道:
```bash
ssh -f -N -L 14000:127.0.0.1:4000 radeon-cloud
# Mac 用 GATEWAY_URL=http://127.0.0.1:14000/v1
```
隧道抖动会导致 httpx ReadTimeout —— memoryd 的 GatewaySentinel/Perceive 已加 retry+长 timeout(180/240s)。

> 决赛现场用 LAN:服务器网关已在 0.0.0.0,Mac 直连服务器内网 IP:4000 即可,延迟从 ~15s 降到 ~5s。

## 6. ocrd 在哪跑

ocrd 是 CPU 微服务(确定性,不占 GPU),**跑在 Mac**(`services/ocrd`,`OCR_BACKEND=rapidocr` 默认)。生产(EPYC)切 `OCR_BACKEND=paddleocr` 用 PP-OCRv6(精度 +9 点,见 docs/benchmarks.md)。

## 7. T3.1 ROCm 消融报告(下一步,40 分主证据)

服务器栈就绪后,在服务器上跑(独占 GPU 时最准,或接受 Dolphin 共存的噪声):
- 量化:brain Q8/Q6/Q4 的 prefill/decode tok/s
- MTP:brain 开/关 `--spec-type draft-mtp`
- 并发:perceive `-np` 1/2/4 吞吐
- 结果写进 `docs/benchmarks.md`(模板在手册 §8)
- 每场景 ≥3 次取中位,附 rocm-smi VRAM 截图
