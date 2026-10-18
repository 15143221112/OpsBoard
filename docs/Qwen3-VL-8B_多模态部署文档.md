# Qwen3-VL-8B 多模态模型部署文档（替换 Qwen2.5-VL-7B）

> 适用服务器：192.168.1.5（Ubuntu，RTX 5090 32G / 62G RAM）
> 适用环境：东北水稻知识库 AI 服务器 vLLM 模型栈
> 部署状态：✅ 已完成并验证通过（2026-10-09）

---

## 目录

1. [部署概述](#1-部署概述)
2. [服务器现有规范](#2-服务器现有规范)
3. [前置验证（vllm 兼容性）](#3-前置验证vllm-兼容性)
4. [模型下载](#4-模型下载)
5. [systemd 服务配置](#5-systemd-服务配置)
6. [显存规划与调优](#6-显存规划与调优)
7. [功能验证清单](#7-功能验证清单)
8. [WeKnora 对接](#8-weknora-对接)
9. [回滚方案](#9-回滚方案)
10. [参考文件](#10-参考文件)

---

## 1. 部署概述

将多模态视觉语言模型从 **Qwen2.5-VL-7B-AWQ** 升级为 **Qwen3-VL-8B-Instruct-FP8**，用于知识库的图片理解与 OCR 文档识别。

| 项 | 旧（7B） | 新（8B） |
|---|---|---|
| 模型 | Qwen2.5-VL-7B-AWQ | Qwen3-VL-8B-Instruct-FP8 |
| 下载源 | modelscope | modelscope |
| 模型目录 | `/opt/models/Qwen2.5-VL-7B-AWQ` | `/opt/models/Qwen3-VL-8B-FP8` |
| 端口 | 8002 | 8002（沿用） |
| 运行环境 | `/opt/vllm-venv-new` | `/opt/vllm-venv-new`（复用，零升级） |

**升级收益**（同口径基准对比）：OCRBench 864→896、MathVista 68.2→~77、MMMU 58.6→~69、DocVQA ~96（持平）、RealWorldQA 68.5→~71。OCR/文档/文本能力明显提升。

---

## 2. 服务器现有规范

部署严格沿用现有规范，不引入新约定：

| 规范项 | 现有配置 |
|---|---|
| 模型存储 | `/opt/models/<模型名>`（目录名对齐模型） |
| 运行环境 | `/opt/vllm-venv-new`（vllm 0.12.0），**不新建、不升级** |
| 服务管理 | systemd，`/etc/systemd/system/<模型>.service` |
| 端口 | 8002=VL、8080=embedding、8001=rerank |
| 防火墙 | 仅放行 WeKnora 前端 8100/8181；8002 属内网不放行 |
| 下载工具 | `/opt/vllm-venv-dl`（modelscope 1.40.1） |

> ⚠️ 约束：**不触碰** embedding(8080, Qwen3-Embedding-4B)、rerank(8001, bge-reranker-v2-m3)。不降 embedding 维度（2560 保留）、不重建向量库。

---

## 3. 前置验证（vllm 兼容性）

**结论先行**：vllm 0.12.0 **原生支持 Qwen3-VL-8B**，无需升级、无需新建环境。

验证方式（代码层，零风险零下载）：

```bash
/opt/vllm-venv-new/bin/python -c "
from vllm.model_executor.models.registry import ModelRegistry
archs = ModelRegistry.get_supported_archs()
print('Qwen3VL' in [a for a in archs])
"
```

实测 vllm 0.12.0 支持的 245 个架构中**包含** `Qwen3VLForConditionalGeneration`（Qwen3-VL 架构），同时支持 `KimiVLForConditionalGeneration`。

> 若验证不支持：按用户决策**不升级 vllm、沿用 7B**（本次验证通过，未触发）。

---

## 4. 模型下载

### 4.1 模型源（重要）

modelscope **没有** `Qwen3-VL-8B-Instruct-AWQ`（404），但有**官方 FP8 版** `Qwen/Qwen3-VL-8B-Instruct-FP8`（性能接近 BF16，显存更省）。故采用 FP8。

```bash
cd /tmp
nohup /opt/vllm-venv-dl/bin/python -c "
from modelscope import snapshot_download
snapshot_download('Qwen/Qwen3-VL-8B-Instruct-FP8', local_dir='/opt/models/Qwen3-VL-8B-FP8')
" > /tmp/vl8_download.log 2>&1 &
```

### 4.2 下载结果

| 项 | 值 |
|---|---|
| 总大小 | 9.9G |
| 权重文件 | `model-00001-of-00002.safetensors`（5.36G）+ `model-00002-of-00002.safetensors`（5.22G） |
| 文件数 | 14（含 config.json、tokenizer、vision 预处理器等） |
| 完整性 | 0 个 `.incomplete` |
| 架构 | `Qwen3VLForConditionalGeneration`（config.json 确认） |
| 下载时长 | 约 30 分钟（~3MB/s） |

> 若误下 AWQ（404 空目录），清理：`rm -rf /opt/models/Qwen3-VL-8B-AWQ`

---

## 5. systemd 服务配置

### 5.1 服务文件 `/etc/systemd/system/vl-8b.service`

```ini
[Unit]
Description=Qwen3-VL-8B-FP8 vLLM
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu
Environment=PYTHONUNBUFFERED=1
Environment=VLLM_MM_INPUT_CACHE_GIB=1
ExecStart=/opt/vllm-venv-new/bin/vllm serve /opt/models/Qwen3-VL-8B-FP8 \
  --host 0.0.0.0 --port 8002 \
  --gpu-memory-utilization 0.57 \
  --max-model-len 4096 --enforce-eager \
  --served-model-name Qwen3-VL-8B-FP8
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 5.2 关键参数说明

| 参数 | 值 | 说明 |
|---|---|---|
| `--gpu-memory-utilization` | 0.57 | VL 显存预算上限（0.57×32G≈18.2G）；8B 需比 7B 更多预算 |
| `--max-model-len` | 4096 | 单次最大 token；KV cache 与 4096 匹配 |
| `--enforce-eager` | 启用 | **必须**。vllm 0.12 对 Qwen3-VL 的 torch.compile 有编译 bug（UnboundLocalError tracer_output），禁用编译后稳定 |
| `VLLM_MM_INPUT_CACHE_GIB=1` | 环境变量 | 多模态输入缓存从默认 4G 降到 1G，释放显存给 KV |
| `--served-model-name` | Qwen3-VL-8B-FP8 | API 中的模型名，需与 /v1/models 返回一致 |

### 5.3 启停命令

```bash
# 启用并启动
sudo systemctl daemon-reload
sudo systemctl enable --now vl-8b

# 查看状态
systemctl status vl-8b
journalctl -u vl-8b -f

# 停止
sudo systemctl stop vl-8b
```

---

## 6. 显存规划与调优

### 6.1 最终显存占用（验证后实测）

| 服务 | PID | 显存占用 |
|---|---|---|
| Qwen3-VL-8B（8002） | 3273166 | ~13.5G |
| Qwen3-Embedding（8080） | 1663964 | 10.7G |
| bge-reranker（8001） | 1665379 | 2.4G |
| **合计** | | 26.96G / 32G，**free 5.15G** ✅ |

### 6.2 部署中遇到的 3 个问题及解决

| # | 现象 | 根因 | 解决 |
|---|---|---|---|
| 1 | `No available memory for the cache blocks`（KV 负） | 8B-FP8 模型占 10.17G，比 7B 大；0.35/0.45 预算不够 | gpu-memory-utilization 提到 0.55 |
| 2 | `UnboundLocalError: tracer_output`（EngineCore 崩溃） | vllm 0.12 对 Qwen3-VL 的 torch.compile 编译 bug | 加 `--enforce-eager` 禁用编译 |
| 3 | `max seq len 4096 needs 0.56 GiB KV, available 0.47 GiB` | KV cache 差 0.09G（0.55 时） | gpu-memory-utilization 0.55→**0.57**，KV 可用升至 **1.09G** |

> **KV cache 调优记录**：0.55 时 KV 可用 0.47G，恢复 4096 需 0.56G（差 0.09G）；提到 0.57 后 KV 可用 1.09G（超出需求，支持并发），free 仍 5.15G 健康。

---

## 7. 功能验证清单

### 7.1 API 就绪

```bash
curl -s http://127.0.0.1:8002/v1/models
```
返回 `Qwen3-VL-8B-FP8`，max_model_len=4096。

### 7.2 文本推理

```bash
curl -s http://127.0.0.1:8002/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen3-VL-8B-FP8","messages":[{"role":"user","content":"用一句话介绍你自己"}],"max_tokens":80}'
```
✅ 正常返回通义千问自我介绍。

### 7.3 多模态 OCR（核心）

通过 OpenAI 兼容 API 传 base64 图片，模型正确读出图片内文字（测试图含 `Northeast Rice KB OCR Test 2026`、`12345.67 rice yield kg/mu` 等），数字/小数点识别准确。✅

### 7.4 服务/端口/显存

```bash
systemctl is-active vl-8b qwen-embedding rerank   # 全部 active
ss -tlnp | grep -E '8002|8080|8001'               # 三个端口都监听
nvidia-smi --query-gpu=memory.free --format=csv,noheader   # ~5.15G
```

---

## 8. WeKnora 对接

8B 已在 8002 就绪，若要正式接入 WeKnora VLM（Web UI 模型配置）：

| 配置项 | 值 |
|---|---|
| Base URL | `http://192.168.1.5:8002/v1`（SSRF 白名单已含 192.168.1.5，可直接用） |
| 模型名 | `Qwen3-VL-8B-FP8` |
| 用途 | 图片理解 / 文档 OCR |

---

## 9. 回滚方案

7B 模型与 `vl-7b.service` **完整保留**（未删除，仅 disable），可随时回滚：

```bash
# 切回 7B
sudo systemctl stop vl-8b
sudo systemctl enable --now vl-7b

# WeKnora VLM 模型名改回
Qwen2.5-VL-7B-Instruct-AWQ
```

---

## 10. 参考文件

| 文件 | 用途 |
|---|---|
| `D:\zhihong\东北水稻知识库_WeKnora_AI服务器部署文档.md` | WeKnora 全套部署（13 容器、8100/8181、MinIO 私有桶、REST API §13） |
| `D:\zhihong\东北水稻知识库_开发文档.md` | 开发文档（含 BladeX REST API 对接手册） |
| `D:\zhihong\Qwen2.5VL7B_多模态部署方案.md` | 7B 部署背景 |
| `/opt/models/Qwen3-VL-8B-FP8` | 8B 模型（服务器） |
| `/etc/systemd/system/vl-8b.service` | 8B 服务（服务器） |

---

*文档整理：2026-10-09 · 基于实测验证结果*
