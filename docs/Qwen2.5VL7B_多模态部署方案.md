# Qwen2.5-VL-7B 多模态服务 · 部署方案（AI 服务器 192.168.1.5）

> 状态：**方案待你确认，确认前不执行任何清理/下载/启动**。
> 目标：在现有服务器规范下，**清理无用模型 + 独立虚拟环境部署 Qwen2.5-VL-7B（vLLM）+ 开机自启**，全程**不碰现有环境**（`/opt/vllm-venv-new` 及两个已运行服务不动）。

---

## 一、现有规范（已核实，作为对齐基准）

| 项 | 现有规范 |
|---|---|
| vLLM 虚拟环境 | `/opt/vllm-venv-new`（vllm 0.12.0 / torch 2.9.0+cu128 / transformers 4.57.0） |
| 已运行服务 | `qwen-embedding.service`（:8080）、`rerank.service`（:8001）—— 均 systemd、enabled 自启、Restart=always |
| systemd 写法 | `ExecStart=/opt/vllm-venv-new/bin/vllm serve /opt/models/<模型> --host 0.0.0.0 --port <P> --gpu-memory-utilization X --served-model-name <名>`，`User=ubuntu`，`WorkingDirectory` |
| 模型目录 | `/opt/models/` |
| 脚本目录 | `/opt/scripts/` |
| 数据规范 | `/data/{minio,mysql,postgres,taos,backup,weknora,docker}` |
| 端口 | 8080(embed)、8001(rerank)、9000/9001(minio)、8100/8181(WeKnora)、5432(PG)… |
| 显存 | RTX 5090 32G，当前已用 ~13G，**剩余 ~18.5G** |

---

## 二、模型清理清单（释放 ~23G）

**删除（无用/不完整/空目录）：**
| 目录 | 大小 | 原因 |
|---|---|---|
| `Qwen2.5-VL-32B-Instruct-AWQ` | 20G | 完整但 32B 超显存，OOM，**不适用** |
| `Qwen2.5-VL-32B-AWQ` | 2.2G | **不完整**（.incomplete） |
| `Qwen2.5-VL-27B-AWQ` | 4K | 空目录 |
| `Qwen3.8-VL-27B-AWQ` | 4K | 空目录 |
| `Qwen3.8-27B-AWQ` | 4K | 空目录 |

**保留（在用 / 有值）：** `Qwen3-Embedding-4B`、`bge-reranker-v2-m3`、`Qwen3-14B-AWQ`、`Qwen3.8-27B`、`Qwen2.5-VL-7B-AWQ`（**待补全**）

> 删除命令（执行前逐项确认，误删可回滚：先 `mv` 到 `/data/backup` 而非直接 `rm`）。

---

## 三、独立虚拟环境（**不碰现有环境**）

用户反馈现有多套 vLLM 环境曾"弄乱套"，故**新建专用环境**：

```
/opt/vllm-venv-vl          # 全新独立 venv（与 vllm-venv / vllm-venv-new 完全隔离）
```

步骤：
1. `python3 -m venv /opt/vllm-venv-vl`
2. pip 用国内镜像（清华源）装：`vllm`（版本对齐现有 `0.12.0`，保证 Qwen2.5-VL 兼容）
3. 装 `huggingface_hub`（补全模型用）
4. **全程不动** `/opt/vllm-venv`、`/opt/vllm-venv-new`

---

## 四、模型补全下载（hf-mirror，国内可达已验证）

- 现状：`/opt/models/Qwen2.5-VL-7B-AWQ` 只有 253M（下载中断），**缺 ~8G 权重**。
- 网络：huggingface.co 不通(000)，**hf-mirror.com 通(200)**。
- 方案：先删残缺目录 → 用 `HF_ENDPOINT=https://hf-mirror.com` 断点续传补全：
```
HF_ENDPOINT=https://hf-mirror.com /opt/vllm-venv-vl/bin/huggingface-cli download \
  Qwen/Qwen2.5-VL-7B-Instruct-AWQ --local-dir /opt/models/Qwen2.5-VL-7B-AWQ --resume-download
```
- 完成后校验：目录应 ~8-9G，无 `.incomplete` 残留。

---

## 五、vLLM 启动 + systemd 服务（对齐现有规范）

**端口：8002**（已在 ufw 放行、未占用，无需开新端口）

新建 `/etc/systemd/system/vl-7b.service`（完全复刻现有 qwen-embedding.service 写法）：
```
[Unit]
Description=Qwen2.5-VL-7B-AWQ vLLM
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu
ExecStart=/opt/vllm-venv-vl/bin/vllm serve /opt/models/Qwen2.5-VL-7B-AWQ \
  --host 0.0.0.0 --port 8002 \
  --gpu-memory-utilization 0.35 \
  --max-model-len 8192 \
  --served-model-name Qwen2.5-VL-7B-AWQ
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```
- `systemctl enable --now vl-7b`（开机自启，对齐现有服务）
- 与现有 8080/8001 **完全独立**：不同环境、不同端口、不同模型，互不影响

---

## 六、目录/结构对齐现有规范

| 项 | 路径 | 规范来源 |
|---|---|---|
| 模型 | `/opt/models/Qwen2.5-VL-7B-AWQ` | 现有 |
| 虚拟环境 | `/opt/vllm-venv-vl` | 新建（并列 vllm-venv-new） |
| systemd | `/etc/systemd/system/vl-7b.service` | 现有 |
| 日志 | 归入 `/opt/scripts/` 或 systemd journal | 现有 |

---

## 七、显存/资源评估

| 项 | 数值 |
|---|---|
| 当前显存剩余 | ~18.5G |
| Qwen2.5-VL-7B-AWQ 加载(4bit) | 权重 ~5-6G + KV cache + 视觉编码器 ≈ **7-8G** |
| 启动后剩余 | **~10-11G**（安全，不影响现有两个 vLLM） |
| gpu-memory-utilization | 0.35（保守，防止吃满挤占现有服务） |

> 结论：7B-VL 启动后显存从容，不挤占现有 embedding/rerank。

---

## 八、接入 WeKnora（可选，多模态识别）

- WeKnora 的 **VLM 配置**指向 `http://host.docker.internal:8002/v1`，模型 `Qwen2.5-VL-7B-AWQ`。
- 用途：知识库内**识别病虫害图片、解析含图表 PDF、理解农事图表**（配合你 S2 已转 MinIO 的图片）。
- 此步**独立于问答主链路**，可后接。

---

## 九、验证清单（执行后逐项核对）

1. `docker ps` / 现有 8080、8001 服务**不受影响**（仍在跑）
2. `curl http://127.0.0.1:8002/v1/models` → 返回 Qwen2.5-VL-7B-AWQ
3. 上传一张水稻病害图 → `POST /v1/chat/completions` 能返回图片描述（多模态生效）
4. 显存：`nvidia-smi` 确认仍剩 ~10G
5. 无 `.incomplete` 残留

---

## 十、风险与回滚

| 风险 | 应对 |
|---|---|
| hf-mirror 下载中断 | `--resume-download` 断点续传；可换 modelscope 源 |
| 新建 venv 装 vllm 耗时 | 走清华源，装完校验版本 |
| 显存紧张 | 已设 0.35 保守值；若 OOM 调低或换 3B |
| 误删模型 | 先 `mv` 到 `/data/backup`，确认无误再删 |

**回滚**：停 `vl-7b` 服务 + 删 `/opt/vllm-venv-vl` + 删模型目录 → 恢复原状，不影响任何现有服务。

---

## 十一、待你确认

1. **清理清单**是否按上面执行（32B 两个 + 3 个空目录删，其余保留）？
2. **端口 8002** 是否可用（已放行、未占用）？
3. **虚拟环境命名** `/opt/vllm-venv-vl`、**gpu-memory-utilization 0.35** 是否 OK？
4. 是否**同时接入 WeKnora VLM**（第 8 节，可后接）？

> 你确认后我再执行；执行顺序：清理备份 → 建 venv → 补全模型 → 起服务 → 验证。
