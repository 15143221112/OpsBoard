# 农业AI知识库 服务器部署文档（Ubuntu + RTX 5090）

> 适用服务器：技嘉双路 8473C 104 核 / RTX 5090 32G 液冷 / 64G 内存 / 4T 固态（99033 那台）
> 服务器角色：**只装 MySQL + 向量数据库 + 向量模型 + 语音服务**，LLM 走 DeepSeek API（云上）
> 模型选型：**Qwen3-Embedding-4B（2560 维）为长期部署选型**；0.6B 仅用于测试环境验证方案可行性

---

## 0. 安装顺序总览（重要：先看这里，按序执行）

**顺序逻辑**：GPU 软件栈（驱动→CUDA→Python）必须自底向上；数据库与中间件无 GPU 依赖；向量服务是核心、最易出错，放中间调试；最后联调。

| 步骤 | 内容 | 校验 | 关键点 |
|---|---|---|---|
| **A1** | 系统就绪：分区确认 / SSH / 换国内源 | `lsb_release -a`、`df -h` | 数据目录统一放最大分区（预装分区见 4.1） |
| **A2** | NVIDIA 驱动 575 | 重启后 `nvidia-smi` 显示 RTX 5090 | ⚠️ **必须 reboot** |
| **A3** | CUDA **12.8** | `nvcc --version` = 12.8 | ⚠️ 5090 不装 12.1 |
| **A4** | Python 3.12 + venv | `python3.12 --version` | — |
| **B1** | PostgreSQL16 + pgvector（向量库） | `CREATE EXTENSION vector` | 监听 0.0.0.0 + pg_hba 放行 |
| **B2** | **TDengine 3.x**（时序库，独立服务） | `taos` CLI 能进 | 端口 **6030**（native）/ **6041**（REST） |
| **B3** | MySQL 8.0 | `SELECT VERSION()` | `bind-address=0.0.0.0` |
| **B4** | **Redis 7**（缓存） | `redis-cli ping` = PONG | 设密码、绑定内网 |
| **B5** | **MinIO**（对象存储） | 浏览器 9001 能登录 | 数据目录 /data/minio |
| **C1** | 下载 Qwen3-Embedding-**4B** | 目录有文件 | 别下 0.6B |
| **C2** | venv + cu128 torch + vLLM（可用一键脚本） | `torch.cuda.is_available()` = True | cu128 / vLLM≥0.9.0 / 不源码编译 |
| **C3** | vLLM embed（8080） | 向量长度 = **2560** | 失败走备选 C3′（7.4） |
| **D1** | voice_server（8090） | `/health` 正常 | 按 README-Linux.md |
| **E1-E6** | 平台对接（向量库/Embedding/语音/Redis/MinIO/TDengine） | 见第 9 章 | 用内网 IP，勿用 localhost |
| **F1** | 联调验收 | 见第 10 章 | 端口放行清单核对 |

---

## 1. 文档说明与结合现状的关键结论

1. **模型选型**：正式部署采用 **Qwen3-Embedding-4B（2560 维）**。测试期已用 Qwen3-Embedding-0.6B（1024 维）在 Windows 跑通整套链路（自写向量服务 + 平台 HTTP/1.1 修复），验证了方案可行性；新服务器直接上 4B 一次到位，避免后续全量重向量化。
2. **推理服务**：**vLLM 为主方案**（生产级高并发）；若 vLLM 在 RTX 5090（sm_120）上无法启动，降级到已验证的自写服务（embedding_server.py）。两者接口均 OpenAI 兼容（`/v1/embeddings`），平台对接不变。
3. **HTTP/1.1 兼容问题已修复**（trae 已落地）：后端 `OpenAIEmbeddingModelTemplate.java` 创建向量模型时强制 `HttpClient.Version.HTTP_1_1`，解决 JDK HttpClient h2c 升级被 uvicorn 拒绝导致的 `Invalid HTTP request received` 400 报错。**部署时把该修复编入 jar 即可，无需再改代码**（详见第 9.4 节）。
4. **平台检索链路是自研的**（`RagEngineServiceImpl.search` 直接调向量库，无内置重排）：向量库 + Embedding 装好后平台开箱即用；重排（Reranker）为**第二阶段**内容，需二次开发（见第 9.3 节）。
5. **维度锁定 2560**：pgvector 建表、平台向量库配置、Embedding 模型输出三者必须一致为 **2560**（Qwen3-Embedding-4B 的输出维度）。

---

## 2. 确定版本清单（总表）

| 类别 | 组件 | 版本 | 端口 | 说明 |
|---|---|---|---|---|
| 操作系统 | Ubuntu Server | 24.04 LTS | - | 内核 6.8+，官方支持 Blackwell |
| 显卡驱动 | NVIDIA Driver | 575.x | - | sm_120 必须 ≥570 |
| CUDA | CUDA Toolkit | 12.8 | - | 与驱动配套，5090 不装旧版 |
| Python | Python | 3.12 | - | vLLM 支持 3.10~3.13 |
| 向量数据库 | PostgreSQL | 16.x | **5432** | 平台原生支持 pgvector |
| 向量插件 | pgvector | 0.8.x | - | PG16 配套 |
| 业务库 | MySQL | 8.0.x | **3306** | BladeX 业务库 |
| 推理服务（**主方案**） | vLLM | ≥0.9.0（sm_120 兼容） | 8080/8081 | 生产级高并发，5090 需特定版本 |
| 推理服务（备选·已验证） | embedding_server.py（sentence-transformers + FastAPI） | torch cu128 | 8080 | 测试期已验证，Linux 部署见 7.4 |
| 向量模型（**正式选型**） | Qwen3-Embedding-**4B** | HF 官方权重 | **8080** | **2560 维** |
| 向量模型（测试用） | Qwen3-Embedding-0.6B | HF 官方权重 | - | 1024 维，仅测试验证 |
| 重排模型（第二阶段） | Qwen3-Reranker-4B | HF 官方权重 | **8081** | 见第 7.3/9.3 节 |
| 语音服务 | voice_server（sherpa-onnx） | 自带 Linux 文档 | **8090** | 见第 8 章 |
| LLM | DeepSeek API | 云端 API | - | 不占用本机资源 |
| 时序数据库 | **TDengine** | 3.3.x（stable） | **6030**（native）/ **6041**（REST） | 物联网设备时序数据，独立服务 |
| 缓存 | **Redis** | 7.x | **6379** | BladeX 平台缓存 / 鉴权 Token |
| 对象存储 | **MinIO** | 2024.x（RELEASE） | **9000**（API）/ **9001**（控制台） | BladeX OSS 文件存储，S3 兼容 |

**显存分配（5090 32G 总计）：**

| 组件 | 显存 | 备注 |
|---|---|---|
| Qwen3-Embedding-4B | ~8G | vLLM 或自写服务加载 |
| Qwen3-Reranker-4B（第二阶段） | ~10G | 注意 `--max-model-len` 控制 |
| 预留 | ~14G | KV/显存管理余量 |

---

## 3. 部署架构

```text
┌─────────────────────────────────────────────────────────┐
│                 新服务器（Ubuntu 24.04, RTX 5090 32G）    │
│                                                         │
│  MySQL 8.0 (3306)  ── BladeX 业务库                     │
│  PostgreSQL16+pgvector (5432)  ── 向量库 (2560维)        │
│  TDengine (6030/6041) ── 物联网时序数据                 │
│  Redis 7 (6379) ── 缓存                                  │
│  MinIO (9000/9001) ── 对象存储                           │
│  向量服务: Qwen3-Embedding-4B (8080) ── /v1/embeddings   │
│  语音服务: voice_server (8090)                           │
│  [第二阶段] Reranker-4B (8081) ── /v1/rerank             │
└───────────────┬─────────────────────────────────────────┘
                │ 内网访问（用 IP 勿用 localhost）
┌───────────────▼─────────────────────────────────────────┐
│              应用服务器（BladeX / blade-ai）             │
│  检索 → pgvector(2560) → [重排-第二阶段] → DeepSeek API   │
│  （OpenAIEmbeddingModelTemplate 已强制 HTTP/1.1）        │
└─────────────────────────────────────────────────────────┘
        LLM = DeepSeek API（云端，不占本机）
```

---

## 4. 基础环境准备（必做，顺序不可乱）

### 4.1 安装 Ubuntu 24.04 LTS
- 使用 Server 版镜像安装，磁盘建议：`/` 至少 500G（模型 + 数据库）
- 安装时勾选 OpenSSH Server
- 换国内源（可选，加速）：
- **预装系统已分好区怎么办**：先 `df -h` + `lsblk` 看现状。① 整盘一个根分区（最常见）→ 直接用，数据目录建在根分区下即可；② LVM → 以后可在线扩容，最理想；③ 根分区太小＋有未分配空间 → 新建分区挂 `/data`，不动系统；④ 只有实在没法用才考虑重装。**不要贸然重装**（可能丢 OEM 的 RAID/驱动配置）。所有数据目录统一放空间最大的挂载点：`/data/models`、`/data/postgres`、`/data/mysql`、`/data/minio`、`/data/taos`、`/data/voice_server`。
  ```bash
  sudo sed -i 's@//.*archive.ubuntu.com@//mirrors.aliyun.com@g; s@//security.ubuntu.com@//mirrors.aliyun.com@g' /etc/apt/sources.list.d/ubuntu.sources
  sudo apt update
  ```

### 4.2 安装 NVIDIA 驱动（必须 ≥570，推荐 575.x）
```bash
lspci | grep -i nvidia
sudo add-apt-repository ppa:graphics-drivers/ppa
sudo apt update
sudo apt install -y nvidia-driver-575
sudo reboot
nvidia-smi   # 应显示 RTX 5090, Driver 575.x, CUDA 12.8
```

### 4.3 安装 CUDA 12.8（注意：不要装 CUDA 12.1，5090 不支持）
```bash
wget https://developer.download.nvidia.com/compute/cuda/12.8.0/local_installers/cuda_12.8.0_575.51_linux.run
sudo sh cuda_12.8.0_575.51_linux.run --toolkit --silent
echo 'export PATH=/usr/local/cuda-12.8/bin:$PATH' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda-12.8/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
source ~/.bashrc
nvcc --version
```

### 4.4 安装 Python 3.12 + venv
```bash
sudo apt install -y python3.12 python3.12-venv python3-pip
python3.12 --version
```

---

## 5. PostgreSQL 16 + pgvector 安装

### 5.1 安装 PostgreSQL 16
```bash
sudo apt install -y postgresql-common
sudo sh -c 'echo "deb https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
sudo apt update
sudo apt install -y postgresql-16
```

### 5.2 安装 pgvector 扩展（0.8.x）
```bash
sudo apt install -y postgresql-16-pgvector
# 若 APT 无包，源码编译：
# sudo apt install -y postgresql-server-dev-16 build-essential git
# git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git
# cd pgvector && make && sudo make install
```

### 5.3 配置数据库与用户（向量库）
```bash
sudo -u postgres psql
```
```sql
CREATE USER pgvector_user WITH PASSWORD '你的强密码';
CREATE DATABASE irrigation_ai OWNER pgvector_user;
\c irrigation_ai
CREATE EXTENSION IF NOT EXISTS vector;
SELECT extversion FROM pg_extension WHERE extname='vector';  -- 应显示 0.8.x
GRANT ALL ON SCHEMA public TO pgvector_user;
```
> 向量表由平台（EmbeddingStore）首次向量化时自动创建，维度 **2560**（Qwen3-Embedding-4B）。

### 5.4 开放内网访问
```bash
sudo nano /etc/postgresql/16/main/postgresql.conf
#    listen_addresses = 'localhost'  →  '0.0.0.0'
sudo nano /etc/postgresql/16/main/pg_hba.conf
# 追加（内网网段按实际改）：host all all 192.168.1.0/24 md5
sudo systemctl restart postgresql
ss -lntp | grep 5432
```

---

## 6. MySQL 8.0 安装

```bash
sudo apt install -y mysql-server
sudo systemctl enable --now mysql
mysql_secure_installation   # 设置 root 密码
mysql --version             # 8.0.x
sudo nano /etc/mysql/mysql.conf.d/mysqld.cnf
# [mysqld] 段追加：
#   character-set-server = utf8mb4
#   bind-address = 0.0.0.0
sudo systemctl restart mysql
```
```sql
CREATE DATABASE IF NOT EXISTS bladex-links-pro DEFAULT CHARACTER SET utf8mb4;
```

### 6.5 TDengine 时序数据库安装（物联网设备数据）

```bash
# 方式一：官方 deb 仓库安装（推荐）
wget -qO - https://repos.taosdata.com/tdengine.key | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/tdengine.gpg
echo "deb [signed-by=/etc/apt/trusted.gpg.d/tdengine.gpg] https://repos.taosdata.com/tdengine-stable/ bookworm main" | sudo tee /etc/apt/sources.list.d/tdengine.list
sudo apt update
sudo apt install -y tdengine
# 方式二：直接下载 deb 包（按需选版本）
# wget https://www.taosdata.com/assets-download/3.0/TDengine-server-3.3.2.0-Linux-x64.deb
# sudo dpkg -i TDengine-server-3.3.2.0-Linux-x64.deb
sudo systemctl enable --now taosd
systemctl status taosd
# 验证：CLI 进入
taos
# 建库示例（keep 365 天、每 10 天一个分片）：
#   CREATE DATABASE iot_tsdb KEEP 365 DURATION 10;
```
> - 数据目录默认 `/var/lib/taos`，建议软链或配置到 `/data/taos`；
> - 平台对接：BladeX 数据智能引擎（SQL 查询）连 `新服务器IP:6030`（native 驱动），REST 用 `http://新服务器IP:6041`；
> - 端口放行：6030（native）/ 6041（REST），仅内网。

### 6.6 Redis 7 安装（BladeX 缓存）

```bash
sudo apt install -y redis-server
sudo nano /etc/redis/redis.conf
#   requirepass 你的强密码
#   bind 127.0.0.1 内网IP   （内网可访问时放开）
sudo systemctl enable --now redis-server
redis-cli -a 你的强密码 ping   # 返回 PONG
```
> BladeX 各服务 `spring.data.redis.host/port/password` 指向 `新服务器IP:6379`；Redis 是平台强依赖（缓存/Token/分布式锁），务必设置密码并仅内网放行。

### 6.7 MinIO 对象存储安装（BladeX OSS）

```bash
wget https://dl.min.io/server/minio/release/linux-amd64/minio -O /usr/local/bin/minio
chmod +x /usr/local/bin/minio
sudo mkdir -p /data/minio
cat > /etc/systemd/system/minio.service <<'EOF'
[Unit]
Description=MinIO Object Storage
After=network.target
[Service]
ExecStart=/usr/local/bin/minio server /data/minio --address ":9000" --console-address ":9001"
Environment=MINIO_ROOT_USER=minioadmin
Environment=MINIO_ROOT_PASSWORD=你的强密码
Restart=always
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now minio
# 验证：浏览器 http://IP:9001 用 minioadmin/你的强密码 登录
curl http://127.0.0.1:9000/minio/health/live
```
> 平台对接：blade-ai 的 `oss.name: minio`，`endpoint: http://新服务器IP:9000`，access-key/secret-key 用上面设置的；bucket 由平台自动创建或手动建 `device` 等。

---

## 7. 向量服务部署（核心）

### 7.1 方案说明

| 方案 | 说明 | 状态 |
|---|---|---|
| **主方案：vLLM** | 生产级高并发，部署见 7.3 | 正式部署采用 |
| **备选：embedding_server.py（sentence-transformers + FastAPI）** | 测试期已验证跑通（0.6B + HTTP/1.1 修复），部署见 7.4 | 若 vLLM 在 5090 上无法启动时启用 |

**模型统一使用 Qwen3-Embedding-4B（2560 维）**，两种服务方式加载同一模型，接口均 OpenAI 兼容，平台对接零改动。

### 7.2 下载模型

```bash
pip install -U "huggingface_hub[cli]" modelscope
# 正式模型（4B）
huggingface-cli download Qwen/Qwen3-Embedding-4B --local-dir /opt/models/Qwen3-Embedding-4B
# 国内加速：modelscope download --model Qwen/Qwen3-Embedding-4B --local_dir /opt/models/Qwen3-Embedding-4B
# 第二阶段再下：Qwen3-Reranker-4B
# huggingface-cli download Qwen/Qwen3-Reranker-4B --local-dir /opt/models/Qwen3-Reranker-4B
```

### 7.3 vLLM 主方案部署（RTX 5090 / sm_120 专用版本组合）

#### 7.3.1 创建 vLLM 环境
```bash
python3.12 -m venv /opt/vllm-venv
source /opt/vllm-venv/bin/activate
pip install --upgrade pip
# 关键：5090 是 Blackwell sm_120，必须 cu128 的 torch + 支持它的 vLLM
pip install torch==2.9.0 --index-url https://download.pytorch.org/whl/cu128
pip install "vllm>=0.9.0"    # 若 wheel 运行时报 ComputeCapability 错误，改用 7.4 备选
python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"
```

#### 7.3.2 启动 Embedding 服务（第一阶段必做，端口 8080）
```bash
cat > /etc/systemd/system/qwen-embedding.service <<'EOF'
[Unit]
Description=Qwen3-Embedding-4B vLLM
After=network.target
[Service]
ExecStart=/opt/vllm-venv/bin/vllm serve /opt/models/Qwen3-Embedding-4B \
  --task embed \
  --host 0.0.0.0 --port 8080 \
  --max-model-len 32768 \
  --served-model-name Qwen3-Embedding-4B
Restart=always
User=root
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now qwen-embedding
# 验证（等模型加载完成）
curl http://127.0.0.1:8080/v1/embeddings -H "Content-Type: application/json" -d '{"model":"Qwen3-Embedding-4B","input":"东北水稻分蘖期如何管理"}'
# 返回 data[0].embedding 数组长度应为 2560
```

#### 7.3.3 启动 Reranker 服务（第二阶段再做，端口 8081）
```bash
cat > /etc/systemd/system/qwen-reranker.service <<'EOF'
[Unit]
Description=Qwen3-Reranker-4B vLLM
After=network.target
[Service]
ExecStart=/opt/vllm-venv/bin/vllm serve /opt/models/Qwen3-Reranker-4B \
  --task rerank \
  --host 0.0.0.0 --port 8081 \
  --max-model-len 8192 \
  --served-model-name Qwen3-Reranker-4B
Restart=always
User=root
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now qwen-reranker
curl http://127.0.0.1:8081/v1/rerank -H "Content-Type: application/json" -d '{"model":"Qwen3-Reranker-4B","query":"东北水稻分蘖期施肥","documents":["分蘖期追施氮肥","抽穗期防治稻瘟病"]}'
```
> **注意 Reranker 显存坑**：`--max-model-len` 别设太大（默认可能飙高显存），若 OOM 降到 4096。

### 7.4 备选方案：自写服务 embedding_server.py（已验证，Linux 部署）

**仅当 vLLM 在 sm_120 无法启动时启用**。此方案已在测试环境用 0.6B 完整验证（含 HTTP/1.1 修复），正式部署换成 4B 即可，代码无需改动。

#### 7.4.1 上传模型与服务文件
| Windows 路径（测试环境） | Linux 目标 |
|---|---|
| `d:\model\Qwen3-Embedding-4B\`（4B 权重） | `/opt/models/Qwen3-Embedding-4B/` |
| `D:\zhihong\voiceAI\voice\Qwen3-Embedding-0.6B\embedding_server.py` | `/opt/ai-services/embedding_server.py` |

```bash
sudo mkdir -p /opt/models /opt/ai-services
# 在 Windows PowerShell 用 scp 上传：
# scp -r d:\model\Qwen3-Embedding-4B root@<服务器IP>:/opt/models/
# scp D:\zhihong\voiceAI\voice\Qwen3-Embedding-0.6B\embedding_server.py root@<服务器IP>:/opt/ai-services/
```

#### 7.4.2 修改模型路径（Windows → Linux）
编辑 `/opt/ai-services/embedding_server.py` 第 37 行：
```python
MODEL_PATH = r"d:\model\Qwen3-Embedding-0.6B"
```
改为：
```python
MODEL_PATH = "/opt/models/Qwen3-Embedding-4B"
```
> 只改这一行，其余逻辑（鉴权、query 前缀、容错、并发控制）无需改动。

#### 7.4.3 创建环境并启动
```bash
python3.12 -m venv /opt/ai-venv
source /opt/ai-venv/bin/activate
pip install --upgrade pip
pip install torch==2.9.0 --index-url https://download.pytorch.org/whl/cu128   # GPU
# pip install torch --index-url https://download.pytorch.org/whl/cpu         # CPU 兜底
pip install sentence-transformers fastapi uvicorn pydantic numpy

cat > /etc/systemd/system/qwen-embedding.service <<'EOF'
[Unit]
Description=Qwen3-Embedding OpenAI Compatible Service
After=network.target
[Service]
ExecStart=/opt/ai-venv/bin/python /opt/ai-services/embedding_server.py --host 0.0.0.0 --port 8080 --api-key sk-zhkj-embedding
WorkingDirectory=/opt/ai-services
Restart=always
Environment=PYTHONUNBUFFERED=1
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now qwen-embedding
```

#### 7.4.4 验证（维度 2560）
```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/v1/embeddings -H "Content-Type: application/json" -H "Authorization: Bearer sk-zhkj-embedding" -d '{"model":"Qwen3-Embedding-4B","input":"东北水稻分蘖期如何管理"}'
# 期望：data[0].embedding 数组长度 = 2560
curl http://127.0.0.1:8080/stats
sudo journalctl -u qwen-embedding -f
```

### 7.5 测试环境说明（0.6B）
测试环境用 0.6B（1024 维）验证了整套链路（自写服务 + HTTP/1.1 修复），证明方案可行。正式部署**直接用 4B（2560 维）**，注意 pgvector 表维度与平台配置均为 **2560**，与测试环境（1024）不同，按新库全新初始化。

---

## 8. 语音服务部署（voice_server，自带 Linux 指南）

voiceAI 的 `voice/voice_server` 自带完整 Linux 部署文档（`README-Linux.md`），要点如下：

```bash
# 1) 复制项目到服务器（不含 .venv 和 models）
sudo mkdir -p /opt/voice_server && sudo chown $USER:$USER /opt/voice_server
# 2) 系统依赖
sudo apt install -y python3-venv libsndfile1 ffmpeg
# 3) venv + 依赖
cd /opt/voice_server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 4) 下载模型（首次约 535MB）
python scripts/download_models.py
# 5) 编辑 config.yaml 确认各引擎 enabled: true
# 6) 生产部署（systemd）
sudo cp deploy/voice-server.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now voice-server
# 7) 验证
curl http://127.0.0.1:8090/health
sudo journalctl -u voice-server -f
```
> 详细步骤以 `D:\zhihong\voiceAI\voice\voice_server\README-Linux.md` 为准。

---

## 9. 平台（blade-ai）对接配置

### 9.1 向量库配置（平台页面/库配置）
- 向量库类型：**PgVector**
- 连接地址：**新服务器内网 IP**（勿用 localhost）
- 端口：5432
- 向量库名：`irrigation_ai`
- 用户名/密码：`pgvector_user` / 你设的密码
- **向量维度：2560**（Qwen3-Embedding-4B）

### 9.2 Embedding 模型配置（平台模型配置 LlmConfig）
- 类型：**openai**（vLLM 或自写服务均提供 OpenAI 兼容接口）
- baseUrl：`http://<新服务器IP>:8080/v1`（用 IP，勿用 localhost——IPv6 解析坑）
- 模型名：`Qwen3-Embedding-4B`
- **维度：2560**
- apiKey：vLLM 可任意占位；自写服务填 `sk-zhkj-embedding`（`--no-auth` 时可任意）

### 9.3 重排（Reranker）接入——第二阶段，需二次开发
> 本小节属第二阶段，第一阶段可跳过。
平台 `RagEngineServiceImpl.search()` 返回 Top-K 后新增一步：
1. `search` 把 `maxResults` 放大到 20（粗召回）
2. 调 `http://<新服务器IP>:8081/v1/rerank`，入参 `query` + 20 条文本，取 Top-5
3. 用 Top-5 拼 context 交给 LLM
> langchain4j-core 1.16.1 已内置 `ScoringModel`/`ReRankingContentAggregator`，可直接引用或自研 HTTP 调用。

### 9.4 Java 侧 HTTP/1.1 修复（已实施，无需再改）

**问题根因**：JDK HttpClient 默认发起 HTTP/2 明文升级（`Upgrade: h2c`），uvicorn/h11 不支持该握手，返回 400 `Invalid HTTP request received`，导致向量化报 `dev.langchain4j.exception.InvalidRequestException`。

**修复（trae 已落地）**：后端 `OpenAIEmbeddingModelTemplate.java` 创建向量模型时强制 HTTP/1.1：
```java
// OpenAIEmbeddingModelTemplate.createOpenAIEmbeddingModel()
// 强制 HTTP/1.1：JDK HttpClient 默认会发起 h2c 明文升级(Upgrade: h2c)，
// uvicorn/h11 不支持该握手会返回 400 "Invalid HTTP request received"
JdkHttpClientBuilder httpClientBuilder = new JdkHttpClientBuilder()
    .httpClientBuilder(HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1));

OpenAiEmbeddingModel.OpenAiEmbeddingModelBuilder builder = OpenAiEmbeddingModel.builder()
    .modelName(...)
    .dimensions(dimension)
    .apiKey(apiKey)
    .httpClientBuilder(httpClientBuilder);   // 关键：注入 HTTP/1.1 client
```
- 代码位置：`voiceAI/AI/src/main/java/org/springblade/modules/aigc/rag/engine/provider/model/OpenAIEmbeddingModelTemplate.java`（第 84~93 行）
- **部署动作**：把该修复随后端一起编译打包进 jar 部署即可，服务器端无需任何特殊配置（vLLM/自写服务均 HTTP/1.1）
- **验证**：平台上传一篇文档 → 向量化成功（不再报 400）

### 9.5 语音接口对接
- AI-UI 前端 `/voice-api` 代理指向 `http://<新服务器IP>:8090`（替代原 localhost）

### 9.6 Redis / MinIO / TDengine 对接（应用服务器指向新服务器 IP）

- **Redis**：BladeX 各服务 `spring.data.redis.host/port/password/database` 指向 `新服务器IP:6379`；
- **MinIO**：blade-ai `oss.name: minio`，endpoint `http://新服务器IP:9000`，access-key/secret-key 用 6.7 设置的；
- **TDengine**：物联网数据智能引擎（SQL 查询）连 `新服务器IP:6030`（native）/ `http://新服务器IP:6041`（REST），建库建表后统计页面读取时序数据。

---

## 10. 部署后自检清单

```bash
# 1. 显卡
nvidia-smi
# 2. PostgreSQL + pgvector
sudo -u postgres psql -d irrigation_ai -c "SELECT extversion FROM pg_extension WHERE extname='vector'"
# 3. MySQL
mysql -uroot -p -e "SELECT VERSION()"
# 4. Embedding（维度必须 2560）
curl http://127.0.0.1:8080/v1/embeddings -H "Content-Type: application/json" -d '{"model":"Qwen3-Embedding-4B","input":"水稻分蘖期"}'
# 5. 语音
curl http://127.0.0.1:8090/health
# 6. TDengine
taos -s "SELECT server_version();"
# 7. Redis
redis-cli -a 你的强密码 ping
# 8. MinIO
curl http://127.0.0.1:9000/minio/health/live
# 9. 平台连通性：应用服务器 → 新服务器 5432/3306/6379/9000/6030/8080/8090 端口 telnet 通
```

**验收通过标准：**
- 平台知识库上传一篇文档 → 向量化成功（**无 400 报错**）→ 提问能命中并回答
- 语音问答往返正常
- （第二阶段）检索 Top-20 → Reranker 重排 → Top-5 进 context

---

## 11. 常见坑与排障

| 现象 | 原因 | 解决 |
|---|---|---|
| vLLM 启动报 `sm_120`/ComputeCapability 不支持 | torch/vLLM 版本旧，不支持 Blackwell | 用 torch 2.9+cu128 + vLLM ≥0.9.0 或官方 Docker 镜像；仍失败改用 7.4 备选方案 |
| 向量化报 `Invalid HTTP request received` | Java 侧未用 HTTP/1.1 | 确认后端 `OpenAIEmbeddingModelTemplate` 已强制 HTTP/1.1（见 9.4）并重新打包部署 |
| embedding 维度不对（返回 1024/1536） | 模型/配置维度不匹配 | 4B=2560，平台向量库配置保持一致 |
| 平台连不上 8080 | 防火墙/安全组未放行、或用了 localhost（IPv6） | 放行端口，用内网 IP |
| 自写服务报 401 | 鉴权开启但 key 不对 | 用 `sk-zhkj-embedding` 或临时 `--no-auth` 调试 |
| pgvector 建扩展报错 | PG 版本太老 / 未装扩展 | PG16 + pgvector 0.8，`CREATE EXTENSION vector` |
| 语音 TTS 中文报错 | espeak-ng 数据路径/编码 | 按 voice_server README-Linux.md 设 `ESPEAK_DATA_DIR` |
| GPU 版 torch 装不上 | 网络/源问题 | 用国内镜像或 CPU 版 torch 兜底 |
| TDengine 连不上 6030/6041 | 服务未启动 / 端口未放行 | `systemctl status taosd`；放行 6030/6041（仅内网） |
| Redis 连不上（AUTH 报错） | 密码错 / 绑定地址 | 核对 `requirepass`、`bind`；`redis-cli -a 密码 ping` |
| MinIO 上传 403 | key 错 / bucket 不存在 | 核对 MINIO_ROOT_USER/PASSWORD；确认平台 oss.endpoint 指向 9000 |

---

*本部署文档基于 voiceAI 已验证方案（HTTP/1.1 修复 + 自写向量服务测试通过）与 BladeX 框架（links_ai_cloud/blade-ai）编制，正式部署采用 Qwen3-Embedding-4B（2560 维）+ vLLM 主方案；已并入时序数据库 TDengine（6030/6041）、缓存 Redis（6379）、对象存储 MinIO（9000/9001）及安装顺序总览（第 0 章）。所有命令均已按 RTX 5090（sm_120）与 Ubuntu 24.04 校准。*
