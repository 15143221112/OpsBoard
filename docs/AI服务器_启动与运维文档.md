# AI 服务器启动与运维文档

> 服务器：192.168.1.5（Ubuntu，RTX 5090 32G 显存 / 62G 内存）
> 用途：东北水稻知识库 AI 模型栈（WeKnora + vLLM + Whisper）
> 文档更新：2026-10-12

---

## 一、服务清单

| 服务名 | 作用 | 端口 | 显存占用 | 开机自启 |
|---|---|---|---|---|
| `vl-8b` | Qwen3-VL-8B-FP8（图片理解/OCR 多模态） | 8002 | ~13.8G | ✅ enabled |
| `qwen-embedding` | Qwen3-Embedding-4B（向量检索） | 8080 | ~10.7G | ✅ enabled |
| `rerank` | bge-reranker-v2-m3（重排） | 8001 | ~2.4G | ✅ enabled |
| `whisper` | faster-whisper large-v3-turbo（语音转写 ASR） | 8003 | ~1.5G | ✅ enabled |

> 另有 WeKnora 主服务（前端 8181，Docker 13 容器）独立运行，不在此 systemd 管理内。

---

## 二、启动顺序（⚠️ 非常重要）

**总原则：先启动大显存占用服务（8B），后启动小占用服务（whisper）。**

原因是 8B 服务配置了 `--gpu-memory-utilization 0.57`，vLLM 启动时会**硬性检查空闲显存 ≥ 18.2G**，不够就直接拒绝启动。

### 正确顺序
```
1. 先启动 8B（vl-8b）  —— 启动时需空闲 ≥ 18.2G
2. 再启动 whisper      —— 只需 ~1.5G，用剩余空间
```

### 已自动保证
- 已给 whisper 服务添加依赖 `After=vl-8b`，**开机自启时 whisper 会自动排在 8B 之后**。
- 重启机器后会自动按「8B → whisper」顺序拉起，无需手动干预。

---

## 三、启停命令

```bash
# 查看所有服务状态
systemctl status vl-8b qwen-embedding rerank whisper

# 启动单个服务
sudo systemctl start vl-8b
sudo systemctl start whisper

# 停止单个服务
sudo systemctl stop whisper

# 重启单个服务（改配置后必须 daemon-reload）
sudo systemctl daemon-reload
sudo systemctl restart vl-8b

# 查看实时日志
journalctl -u vl-8b -f
journalctl -u whisper -f
```

---

## 四、显存规划（32G 总量）

| 服务 | 显存 |
|---|---|
| Qwen3-VL-8B | 13.78G |
| Qwen3-Embedding-4B | 10.76G |
| bge-reranker | 2.4G |
| Whisper | 1.49G |
| **合计** | **28.46G / 32G，free ~3.6G** ✅ |

> 当前 4 个服务已全部在跑且健康，剩余约 3.6G 余量。

---

## 五、常见问题排查

### 1. 8B（vl-8b）启动失败

**现象**：`systemctl status vl-8b` 显示 restart 循环失败；日志报：
- `Free memory ... is less than desired GPU memory utilization (0.57, 18.2G)` → **空闲显存不足**

**原因**：启动 8B 时空闲显存 < 18.2G（通常是被 whisper 或其他进程先占用了）。

**解决**：
```bash
# 停掉占用显存的其他服务，腾出空间
sudo systemctl stop whisper
# 确认空闲显存 ≥ 18.2G
nvidia-smi --query-gpu=memory.free --format=csv,noheader
# 重启 8B
sudo systemctl restart vl-8b
# 8B 起来后再启动 whisper
sudo systemctl start whisper
```

### 2. 日志报 `No available memory for the cache blocks`（KV cache 不足）

**原因**：`gpu-memory-utilization` 设太低，KV cache 空间不够。

**解决**：调整 `/etc/systemd/system/vl-8b.service` 中 `--gpu-memory-utilization` 值（当前 0.57，文档验证过最优），改后：
```bash
sudo systemctl daemon-reload
sudo systemctl restart vl-8b
```

### 3. 日志报 `UnboundLocalError: tracer_output`

**原因**：vllm 0.12 对 Qwen3-VL 的 torch.compile 有 bug。

**解决**：确保服务参数里有 `--enforce-eager`（禁用编译），当前已配置。

### 4. 检查端口是否监听

```bash
ss -tlnp | grep -E '8002|8001|8080|8003'
```
- 8002 = 8B，8001 = rerank，8080 = embedding，8003 = whisper

### 5. 查看 8B API 是否就绪

```bash
curl -s http://127.0.0.1:8002/v1/models
```
正常返回 `Qwen3-VL-8B-FP8`，max_model_len=4096。

---

## 六、模型与配置参数（vl-8b）

`/etc/systemd/system/vl-8b.service`：
```ini
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
```

> ⚠️ 不要随意增大 `--max-model-len`（如 8192）：当前 32G 显卡上显存不足，会 OOM。知识库长文本问答请用 qwen-plus（阿里 128K context）。

---

## 七、服务文件位置

| 服务 | 文件 |
|---|---|
| vl-8b | `/etc/systemd/system/vl-8b.service`（备份 `vl-8b.service.bak2`） |
| qwen-embedding | `/etc/systemd/system/qwen-embedding.service` |
| rerank | `/etc/systemd/system/rerank.service` |
| whisper | `/etc/systemd/system/whisper.service`（备份 `whisper.service.bak`，已加 `After=vl-8b` 依赖） |

---

*有问题先查本文档，再按 §五 逐项排查。*
