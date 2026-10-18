# AI 服务器 Whisper 语音转写服务部署文档

> 服务器：192.168.1.5（Ubuntu · RTX 5090 32G 显存 · 62G 内存）
> 服务：Whisper large-v3-turbo 语音转写（faster-whisper · int8 · FastAPI）
> 端口：**8003**（OpenAI 兼容 `/v1/audio/transcriptions`）
> 部署日期：2026-10-10

---

## 1. 需求与方案

### 1.1 需求
「两者都要」：
1. **集成 WeKnora** —— 语音类型模型（音频/录音转写、语音问答）
2. **独立 OpenAI 兼容接口** —— 供 BladeX 框架调用（`/v1/audio/transcriptions`）

### 1.2 技术选型
| 候选 | 结论 | 原因 |
|---|---|---|
| `fedirz/faster-whisper-server`（已更名 Speaches） | **不采用** | 主要按 Docker 镜像分发且容器跑 GPU 需 nvidia runtime（本机 vLLM 走 systemd、未配 nvidia runtime），与现有规范冲突 |
| openai-whisper（pytorch 原版） | 不采用 | 需单独下载 torch，国内源极慢 |
| **faster-whisper + 自写 FastAPI** | **采用** | CTranslate2 int8 显存占用小、速度极快；自写服务与 vLLM 同范式（venv + systemd），完全隔离、可控 |

### 1.3 关键决策
- **int8 量化**：large-v3-turbo FP16 需 6G 显存、INT8 仅 3G（实际增量更小）；语音场景 int8 精度损失无感 → 选 **int8**
- **独立 venv** `/opt/whisper-venv`，与 vLLM 环境隔离
- **复用 vLLM 的 torch 做转换**（`PYTHONPATH` 指向 vllm-venv 的 site-packages），避免重新下载 554MB torch
- **LD_LIBRARY_PATH 复用 vllm-venv 的 nvidia CUDA 库**（服务器无系统级 CUDA）

---

## 2. 资源评估

| 资源 | 部署前 | whisper 占用 | 部署后 |
|---|---|---|---|
| 显存 | used 26962 / free 5149 MiB | **~76 MiB**（int8 加载） | used 28534 / free 3578 MiB |
| 内存 | 可用 ~36G | peak ~1.4G | 充裕 |
| 磁盘 | 剩 3.2T | 模型 ~780M + pytorch 源 1.6G | 充裕 |
| 端口 | 8003 空闲 | — | 8003 已用（防火墙已放行） |

> 注：whisper 与 3 个 vLLM 服务（qwen-embedding 8080、rerank 8001、vl-8b 8002）共存，显存余量充足。

---

## 3. 目录与规范（严格按现有服务器规范）

| 路径 | 用途 |
|---|---|
| `/opt/whisper-venv/` | 独立 Python 虚拟环境（Python 3.12） |
| `/opt/whisper-venv/whisper_server.py` | 服务源码 |
| `/opt/models/faster-whisper-large-v3-turbo/` | **CT2 int8 模型产物**（model.bin 777M） |
| `/opt/models/whisper-large-v3-turbo-pytorch/` | pytorch 源模型 1.6G（转换后**可清理**） |
| `/etc/systemd/system/whisper.service` | systemd 服务（自启） |
| `/home/ubuntu/test_whisper.wav` | 转写测试样本 |

---

## 4. 安装步骤

### 4.1 创建独立 venv
```bash
python3 -m venv /opt/whisper-venv
/opt/whisper-venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    faster-whisper fastapi uvicorn python-multipart
```

### 4.2 下载 pytorch 模型（ModelScope 直连，逐文件 curl）
服务器无法访问 HF/GitHub，ModelScope 可直连。`snapshot_download` 库静默失败，改用 curl 逐文件：
```bash
BASE=https://modelscope.cn/models/AI-ModelScope/whisper-large-v3-turbo/resolve/master
cd /opt/models/whisper-large-v3-turbo-pytorch
for f in config.json added_tokens.json tokenizer.json vocabulary.json model.safetensors; do
  curl -L -o "$f" "$BASE/$f"
done
```

### 4.3 转换为 CT2 int8（复用 vLLM 的 torch）
```bash
export PYTHONPATH=/opt/vllm-venv-new/lib/python3.12/site-packages  # 复用 torch 2.9.0+cu128
/opt/whisper-venv/bin/ct2-transformers-converter \
    --model /opt/models/whisper-large-v3-turbo-pytorch \
    --output_dir /opt/models/faster-whisper-large-v3-turbo \
    --copy_files tokenizer.json vocabulary.json \
    --quantization int8
```
> ⚠️ `--copy_files` 用**空格分隔**多个文件（逗号会被当作单一文件名报错）。
> 产物 model.bin 777M（int8）。

### 4.4 补 preprocessor 配置（关键）
whisper-large-v3-turbo 编码器是 **128 mel**（非小模型的 80）。转换产物缺 `preprocessor_config.json` 会导致 faster-whisper 用默认 80 而报维度错。创建：
```bash
cat > /opt/models/faster-whisper-large-v3-turbo/preprocessor_config.json <<'EOF'
{
  "feature_size": 128,
  "hop_length": 160,
  "n_fft": 400,
  "n_samples": 480000,
  "padding_value": 0.0,
  "return_attention_mask": false,
  "sampling_rate": 16000
}
EOF
```

### 4.5 服务代码 `whisper_server.py`
FastAPI，OpenAI 兼容 `/v1/audio/transcriptions` + `/health`。关键点：
- 全局模型变量命名 **`asr_model`**（避开 FastAPI Form 参数名 `model` 的遮蔽坑）
- 转写用 `try/except IndexError` 兜底（int8 下语言自动检测偶发返回空，回落中文）

### 4.6 CUDA 库隔离（whisper 完全独立，不依赖 vllm-venv）

服务器无系统级 CUDA（vLLM 用 venv 内 pip 装的 nvidia 包）。为保证 whisper 运行时**不依赖 vllm-venv**（vllm-venv 曾出现问题、仅留备份），将 whisper 需要的 CUDA 运行库**复制到 whisper-venv 独立目录**：
```bash
mkdir -p /opt/whisper-venv/cuda-libs
cp /opt/vllm-venv-new/lib/python3.12/site-packages/nvidia/cublas/lib/libcublas.so.12 \
   /opt/vllm-venv-new/lib/python3.12/site-packages/nvidia/cublas/lib/libcublasLt.so.12 \
   /opt/vllm-venv-new/lib/python3.12/site-packages/nvidia/cublas/lib/libnvblas.so.12 \
   /opt/vllm-venv-new/lib/python3.12/site-packages/nvidia/cuda_runtime/lib/libcudart.so.12 \
   /opt/whisper-venv/cuda-libs/
chown -R ubuntu:ubuntu /opt/whisper-venv/cuda-libs
```
> 复制后即使 vllm-venv 被删除/损坏，whisper 不受影响。复制完成后 LD_LIBRARY_PATH 指向 `/opt/whisper-venv/cuda-libs`（见 4.6 systemd 配置）。

### 4.7 systemd 服务 `whisper.service`
```ini
[Unit]
Description=Whisper large-v3-turbo ASR (faster-whisper int8)
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/whisper-venv
Environment=PYTHONUNBUFFERED=1
Environment=WHISPER_MODEL=/opt/models/faster-whisper-large-v3-turbo
Environment=WHISPER_DEVICE=cuda
Environment=WHISPER_COMPUTE=int8
Environment=LD_LIBRARY_PATH=/opt/whisper-venv/cuda-libs
ExecStart=/opt/whisper-venv/bin/python /opt/whisper-venv/whisper_server.py --host 0.0.0.0 --port 8003
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
```bash
systemctl daemon-reload
systemctl enable --now whisper
```

### 4.8 防火墙放行 8003（BladeX 跨机调用必需）
```bash
ufw allow 8003/tcp
```

---

## 5. 接口说明（OpenAI 兼容）

### 5.1 转写
```
POST http://192.168.1.5:8003/v1/audio/transcriptions
Content-Type: multipart/form-data
```
| 参数 | 必填 | 说明 |
|---|---|---|
| `file` | 是 | 音频/视频文件（wav/mp3/m4a/ogg/flac/**mp4/mov/flv** 等；视频自动提取音轨，ffmpeg 解码） |
| `language` | 否 | 语言代码（如 zh/en），留空自动检测 |
| `model` | 否 | 兼容参数，忽略 |
| `prompt` | 否 | 初始提示词 |
| `temperature` | 否 | 采样温度，默认 0 |
| `response_format` | 否 | json（默认）/ verbose_json / srt / vtt |

返回（json）：
```json
{"text": "转写文本"}
```
返回（verbose_json）：
```json
{"text": "...", "language": "zh", "duration": 5.0, "elapsed": 0.23}
```

### 5.2 健康检查
```
GET http://192.168.1.5:8003/health
→ {"status":"ok","model":"/opt/models/faster-whisper-large-v3-turbo","device":"cuda","compute":"int8"}
```

### 5.3 视频直接转写（mp4/mov/flv，已实测）
faster-whisper 的 ffmpeg 解码**自动提取视频中的音轨**再转写，**无需先剥离音频**。实测（模拟抖音 mp4，H.264+AAC）：
```bash
# 直接传 mp4 视频文件
curl -s -m 90 -F 'file=@/home/ubuntu/test_douyin.mp4' \
     -F 'response_format=verbose_json' \
     http://192.168.1.5:8003/v1/audio/transcriptions
# → {"text":"今天天气很好,我们开始测试语音识别系统。","language":"zh","duration":5.0,"elapsed":0.3}
```

**抖音/短视频转写注意事项**：
- 抖音视频一般是 H.264 视频 + AAC 音频轨，直接传即可
- 服务已开 `vad_filter`（语音活动检测），能自动跳过背景音乐/无语音的空白段
- **人声被音乐盖过、多人叠声、方言**时转写精度下降——这是所有语音转写模型的共性，与模型本身无关；如需更好效果可先降噪/分离人声再转写


---

## 6. 集成方式

### 6.1 WeKnora（语音类型模型）
在 WeKnora 后台「模型设置」添加**语音类型模型**：
- 类型：语音（语音转文本）
- 模型名称：`large-v3-turbo`
- Base URL：`http://192.168.1.5:8003/v1`
- 服务商：自定义（OpenAI 兼容接口）

**已接入完成（2026-10-10）**：测试连接返回「连接成功」，保存后模型列表「语音(1)」，新增 `large-v3-turbo`。WeKnora 语音模型用此接口做音频/录音转写与语音问答。

### 6.2 BladeX（独立 OpenAI 兼容接口）
BladeX 直接按 OpenAI `audio/transcriptions` 调用，Base URL 填 `http://192.168.1.5:8003/v1`。防火墙 8003 已放行，跨机可调。

### 6.3 BladeX / Java 调用示例

**Java（Spring Boot RestTemplate，BladeX 可直接用）**
```java
// 转写音频：POST http://192.168.1.5:8003/v1/audio/transcriptions (multipart/form-data)
HttpHeaders headers = new HttpHeaders();
headers.setContentType(MediaType.MULTIPART_FORM_DATA);

MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
body.add("file", new FileSystemResource("/tmp/test_whisper.wav")); // 音频文件
body.add("language", "zh");          // 可选：语言代码，留空自动检测
body.add("response_format", "json"); // 可选：json / verbose_json / srt / vtt

HttpEntity<MultiValueMap<String, Object>> req = new HttpEntity<>(body, headers);
ResponseEntity<Map> resp = restTemplate.postForEntity(
    "http://192.168.1.5:8003/v1/audio/transcriptions", req, Map.class);

String text = (String) resp.getBody().get("text");  // 转写结果
```
> 若转写文件较大（如录音），建议 `RestTemplate` 设超时：`setConnectTimeout(5000); setReadTimeout(120000)`。

**Python 示例（OpenAI SDK / requests）**
```python
import requests
resp = requests.post(
    "http://192.168.1.5:8003/v1/audio/transcriptions",
    files={"file": open("test_whisper.wav", "rb")},
    data={"language": "zh", "response_format": "json"},
    timeout=90,
)
print(resp.json()["text"])

# 或直接用 openai 官方 SDK
from openai import OpenAI
client = OpenAI(base_url="http://192.168.1.5:8003/v1", api_key="sk-unused")
result = client.audio.transcriptions.create(
    model="large-v3-turbo", file=open("test_whisper.wav", "rb"), language="zh")
print(result.text)
```

### 6.4 BladeX 联调验收步骤

| 步骤 | 操作 | 通过标准 |
|---|---|---|
| 1 | BladeX 机器 `curl http://192.168.1.5:8003/health` | 返回 `{"status":"ok",...}` |
| 2 | 用测试 wav/mp4 调转写接口 | 返回正确转写文本 |
| 3 | 传 `language=zh` 转写中文录音 | 中文正确 |
| 4 | 不传 language（自动检测） | 服务兜底成功，不报 500 |
| 5 | 传一段较长音频（>1 分钟） | 服务不超时，正确转写 |
| 6 | 并发 3~5 个转写请求 | 全部成功（int8 单卡足够） |


---

## 7. 排障记录（已解决的关键卡点）

| 症状 | 根因 | 解决 |
|---|---|---|
| 500 NoneType.transcribe | FastAPI Form 参数 `model` 遮蔽全局模型实例 | 全局模型改名 `asr_model` |
| 500 av open() unexpected `metadata_errors` | av 19.0.1 过新，faster-whisper 1.2.1 调已移除参数 | 改 `faster_whisper/audio.py` 源码去掉 `metadata_errors="ignore"` |
| 500 Invalid features (1,80) vs (1,128) | 转换产物缺 `preprocessor_config.json`，faster-whisper 用默认 80 mel | 补 `preprocessor_config.json` 设 `feature_size:128` |
| 500 libcublas.so.12 not found | 服务器无系统级 CUDA 库 | `LD_LIBRARY_PATH` 指向 vllm-venv 的 nvidia 库 |
| verbose_json 500 list index out of range | int8 下语言自动检测偶发返回空 | 服务端 `try/except IndexError` 兜底中文 |

---

## 8. 验证结果

| 项 | 结果 |
|---|---|
| 服务状态 | enabled + active，8003 监听 |
| 转写正确性 | 「今天天气很好,我们开始测试语音识别系统。」✅ |
| 转写耗时 | 8 秒音频 0.23s（int8, CUDA） |
| 语言检测 | zh（自动检测正常） |
| 格式 | json / verbose_json / srt 均通过 |
| 视频转写 | mp4（H.264+AAC，模拟抖音）0.3s 转写成功，m4a 亦通过 |
| 显存增量 | ~76 MiB（int8） |
| 内存 peak | ~1.4G |
| 防火墙 | 8003/tcp ALLOW（IPv4+IPv6） |
| WeKnora 接入 | 语音模型 large-v3-turbo 已添加，连接测试成功，模型列表「语音(1)」 |
| BladeX 接口 | `/v1/audio/transcriptions` OpenAI 兼容，跨机可调 |

---

## 9. 运维

```bash
# 状态
systemctl status whisper
# 日志
journalctl -u whisper -f
# 重启 / 停止 / 启动
systemctl restart whisper
systemctl stop whisper
systemctl start whisper
# 验证
curl -s http://192.168.1.5:8003/health
```

**清理**：pytorch 源模型 `/opt/models/whisper-large-v3-turbo-pytorch`（1.6G）转换后已不需要，可删除：
```bash
rm -rf /opt/models/whisper-large-v3-turbo-pytorch
```

### 9.1 部署核对清单

| 检查项 | 命令 | 期望 |
|---|---|---|
| 服务运行 | `systemctl is-active whisper` | `active` |
| 开机自启 | `systemctl is-enabled whisper` | `enabled` |
| 端口监听 | `ss -tlnp \| grep 8003` | `0.0.0.0:8003` |
| 防火墙放行 | `ufw status \| grep 8003` | `ALLOW` |
| 健康检查 | `curl -s http://192.168.1.5:8003/health` | `{"status":"ok",...}` |
| 显存占用 | `nvidia-smi --query-gpu=memory.used` | whisper 增量 ~76 MiB |
| 进程归属 | `ps aux \| grep whisper_server` | User=ubuntu |

### 9.2 监控（可选接入 Prometheus）
whisper 无原生 metrics 端口，可用以下方式纳入现有监控：
- **存活探活**：在 Prometheus `blackbox` 或 cron 中定期 `curl http://192.168.1.5:8003/health`，异常告警
- **显存/内存**：已有 dcgm-exporter（9400）与 node_exporter（9102）覆盖 GPU/主机指标，whisper 占用小，无需单独埋点
- **转写失败率**：如需统计，可在服务端日志（`journalctl -u whisper`）加白名单规则，或后续在 FastAPI 加 `/metrics` 暴露转写计数（Prometheus client）

---

## 10. 关键注意事项

1. **whisper 运行时完全独立**：独立 venv `/opt/whisper-venv` + 独立 CUDA 库 `/opt/whisper-venv/cuda-libs`（已复制），**不依赖 vllm-venv**。仅转换时用 `PYTHONPATH` 复用过一次 vllm 的 torch，转换完成后不再需要。vllm-venv 可保留备份、不受 whisper 影响，whisper 也不受 vllm-venv 状态影响。
2. **av 版本**：whisper-venv 内 av 保持 19.0.1（已改源码绕过），勿再升级/降级，否则可能回归。
3. **端口 8003** 已按「只放必要的接口」口径放行（BladeX 调用需要）；其余端口不放。
4. 若后续重装 faster-whisper 会覆盖 audio.py 修改，需重打补丁。

---

## 11. 性能参考与安全建议

### 11.1 性能参考（RTX 5090 · int8）
| 场景 | 实测 |
|---|---|
| 5~8 秒音频转写 | ~0.23~0.33s（实时率约 20~25 倍） |
| 并发 | 单卡 int8 轻松支撑 3~5 并发转写，显存余量充足 |
| 1 分钟音频 | 预计 ~2~3s 转完（未实测，按实时率推算） |

### 11.2 安全建议
- **8003 仅内网开放**：BladeX/WeKnora 都在内网，端口 8003 只对可信网段放行（当前 ufw `Anywhere`，如仅需内网可收紧为指定网段：`ufw allow from 192.168.1.0/24 to any port 8003`）
- **鉴权（可选）**：接口目前无鉴权。若需保护，可在服务端增加简单 API Key 校验（如 Header 校验），BladeX 调用时带上
- **文件大小限制**：当前无上传限制，若担心超大文件，可在服务端对 `file.size` 加上限（如 500MB），超出返回 413
