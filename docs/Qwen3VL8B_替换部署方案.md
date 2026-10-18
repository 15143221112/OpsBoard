# Qwen3-VL-8B 替换 Qwen2.5-VL-7B · 部署方案（AI 服务器 192.168.1.5）

> 状态：**方案待你确认，确认前不执行任何下载/停服/启动**。
> 目标：在现有服务器规范下，用 **Qwen3-VL-8B-AWQ 替换 Qwen2.5-VL-7B-AWQ**（多模态/OCR/文本均升级），全程**不动现有 embedding(8080)、rerank(8001)**，保留 7B 便于回滚。

---

## 一、现有规范（已从服务器实测核实，作为对齐基准）

| 项 | 现有实际规范 |
|---|---|
| VL-7B 服务 | `/etc/systemd/system/vl-7b.service`（active+enabled 自启） |
| VL-7B 启动命令 | `/opt/vllm-venv-new/bin/vllm serve /opt/models/Qwen2.5-VL-7B-AWQ --port 8002 --gpu-memory-utilization 0.35 --max-model-len 8192 --served-model-name Qwen2.5-VL-7B-AWQ` |
| 模型目录 | `/opt/models/`（现有所有模型均在此） |
| vLLM 环境 | `/opt/vllm-venv-new`（vllm 0.12.0；VL-7B 与 embedding/rerank 共用此环境） |
| 下载工具 | modelscope（临时环境 `/opt/vllm-venv-dl`；hf-mirror 曾超时） |
| 端口 | 8002=VL；8080=embed；8001=rerank；8100/8181=WeKnora（对外仅 8100+8181） |
| 显存现状 | embed 10.5G + rerank 2.3G + VL-7B 11.5G = 24.9G，剩 7G |
| systemd 写法 | `ExecStart=…vllm serve /opt/models/<模型> --host 0.0.0.0 --port <P> --gpu-memory-utilization X --served-model-name <名>`，`User=ubuntu`，`Restart=always` |

---

## 二、目标与策略

- **替换**：停 VL-7B（8002），起 Qwen3-VL-8B-AWQ（8002 沿用）。
- **不碰现有服务**：embedding(8080)、rerank(8001) 全程不动。
- **保留 7B 回滚**：模型目录不删、`vl-7b.service` 保留（仅 disable），8B 出问题秒回滚。

---

## 三、⚠️ 关键前置验证（第 1 步，零风险）

**风险点**：现有 vllm 0.12 发布于 2025-08，Qwen3-VL-8B 发布于 2025-11，**0.12 是否支持 Qwen3-VL 需实测**（vLLM 官方已确认 Qwen3-VL 整体被支持，但具体版本线要验证）。

**第 1 步做法**：直接用一个极小显存的测试命令，在现有 `vllm-venv-new` 里尝试加载 Qwen3-VL-8B，看是否能起来：
- **分支 A（支持）**：直接复用 `vllm-venv-new`，**零风险、不装任何东西**。
- **分支 B（不支持）**：**新建独立环境 `/opt/vllm-venv-vl8`**（装支持 Qwen3-VL 的 vllm 版本），**完全不碰** `vllm-venv-new`——你之前担心的"弄乱套"不会发生（独立 venv，只多占内存，不影响现有三个服务）。

> 前置验证先做，确认支持方式后再走下载，避免白下载。

---

## 四、模型下载（modelscope，到指定目录）

| 项 | 值 |
|---|---|
| 模型 | `Qwen/Qwen3-VL-8B-Instruct-AWQ`（下载前先在 modelscope 确认存在） |
| 目标目录 | `/opt/models/Qwen3-VL-8B-AWQ` |
| 工具 | modelscope `snapshot_download`（复用 `/opt/vllm-venv-dl`） |
| 大小 | AWQ 4bit ≈ **~6G** |
| 校验 | 完成后 `ls` 确认无 `.incomplete`、权重齐全 |

> 与现有 7B 的下载方式完全一致（modelscope 源已验证可用）。

---

## 五、服务切换（对齐现有 systemd 规范）

**① 停 7B**（释放 11.5G 显存）
```bash
sudo systemctl stop vl-7b
sudo systemctl disable vl-7b    # 保留 service 文件，不删，回滚用
```

**② 新建 `/etc/systemd/system/vl-8b.service`**（完全复刻 vl-7b 写法）
```
[Unit]
Description=Qwen3-VL-8B-AWQ vLLM
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu
ExecStart=/opt/<vllm环境>/bin/vllm serve /opt/models/Qwen3-VL-8B-AWQ \
  --host 0.0.0.0 --port 8002 \
  --gpu-memory-utilization 0.35 \
  --max-model-len 8192 \
  --served-model-name Qwen3-VL-8B-AWQ
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```
> `<vllm环境>` = 前置验证结果：A 则 `vllm-venv-new`，B 则 `vllm-venv-vl8`。

**③ 启动并自启**
```bash
sudo systemctl enable --now vl-8b
```

**④ 端口**：8002 沿用（7B 停止后已空闲；对外防火墙本来就只放 8100+8181，8002 属内网，无需动防火墙）。

---

## 六、显存账（替换后）

| 项 | 显存 |
|---|---|
| 停 7B 后 | embed 10.5 + rerank 2.3 = 12.8G，剩 **19.2G** |
| Qwen3-VL-8B-AWQ（0.35×32=11.2G 上限） | 实际 ~**9–11G** |
| 启动后剩余 | **~8–9G**（安全，比 7B 时更宽裕） |

---

## 七、WeKnora 对接（替换后）

- 8002 现在跑 `Qwen3-VL-8B-AWQ`。
- WeKnora **VLM 模型配置**指向 `http://host.docker.internal:8002/v1`、模型名 `Qwen3-VL-8B-AWQ`（`SSRF_WHITELIST=host.docker.internal` 已含，无需再改）。
- 注意：你之前 VLM 模型在 WeKnora 里**并未真正保存成功**（只是问过怎么填）——所以这一步是**真正把 8B 接进知识库**的入口，需确认要不要接、按哪种填法。

---

## 八、验证清单（执行后逐项核对）

1. `curl http://127.0.0.1:8002/v1/models` → 返回 `Qwen3-VL-8B-AWQ`
2. 上传一张水稻病害图 → `POST /v1/chat/completions` 能返回图片描述（多模态生效）
3. 8080 / 8001 服务**不受影响**（仍在跑）
4. `nvidia-smi` 确认剩 ~8–9G
5. `/opt/models/Qwen3-VL-8B-AWQ` 无 `.incomplete`
6. `systemctl status vl-8b` → active、enabled 自启

---

## 九、风险与回滚

| 风险 | 应对 |
|---|---|
| vllm 0.12 不支持 Qwen3-VL-8B | 分支 B：新建独立环境 `vllm-venv-vl8`，不碰现有 |
| modelscope 下载慢/断 | 断点续传、可换源 |
| 显存不足 | 已设 0.35 保守；8B 比 7B 更省 |
| 8B 效果不如预期 | **秒回滚**（见下） |

**回滚（保留 7B，随时可用）**：
```bash
sudo systemctl stop vl-8b
sudo systemctl enable --now vl-7b
# WeKnora VLM 模型名改回 Qwen2.5-VL-7B-AWQ
```
> `/opt/models/Qwen2.5-VL-7B-AWQ` 与 `vl-7b.service` 均保留，回滚零损失。

---

## 十、待你确认

1. **先做前置验证**（第 3 节，看 vllm 0.12 是否支持 Qwen3-VL-8B，再定环境）——OK？
2. **模型下载源**用 modelscope（`Qwen/Qwen3-VL-8B-Instruct-AWQ`），下载到 `/opt/models/Qwen3-VL-8B-AWQ`——OK？
3. **gpu-memory-utilization 0.35、max-model-len 8192、端口 8002** 沿用 7B 参数——OK？
4. **保留 7B 作回滚**（不删模型、service 仅 disable）——OK？
5. 替换后**是否要正式把 8B 接入 WeKnora VLM**（第 7 节，之前没真正接成功过）？

> 你逐项确认后我再执行。执行顺序：**前置验证 → 下载模型 → 停 7B → 起 8B → 验证 → （可选）接 WeKnora**。
