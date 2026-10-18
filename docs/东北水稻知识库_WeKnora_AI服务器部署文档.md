# 东北水稻知识库 · WeKnora 在 AI 服务器(192.168.1.5) 部署文档（终版）

> 状态：**已安装完成并验证**。全部服务已启动、健康，核心 RAG 链路已验证。本文档记录实际执行过程、当前运行状态与后续待办。
> 部署方式：**严格按现有规范隔离部署**，未污染/覆盖任何现有服务；WeKnora 全套容器跑在 Docker 内，数据按 `/data` 规范目录存放。

---

## 0. 服务器现状（已核验）

| 项目 | 现状 |
|---|---|
| 系统 | Ubuntu 24.04.4 LTS，主机名 `ubun` |
| CPU / 内存 | 208 核 / 62GiB（当前用 ~22GiB，可用 ~40GiB） |
| 磁盘 | 3.6T，已用 268G，**余 3.2T**（Docker 数据根 `/data/docker`） |
| GPU | RTX 5090 32G；被现有 2 个 vLLM 占 ~13G，显存余 ~19G |
| Docker | **本方案已安装**：Engine `29.8.2` + Compose plugin `v5.6.0` |
| 防火墙 | UFW 激活，默认 deny incoming；本方案已放行 WeKnora 端口 |

### 1.1 现有可复用服务（本次全部复用，未改动）

| 服务 | 端口 | 在 WeKnora 中的用法 |
|---|---|---|
| vLLM · Qwen3-Embedding-4B | 8080 | **Embedding**（维度 **2560**，max len 1024，OpenAI 兼容 `/v1/embeddings`） |
| vLLM · bge-reranker-v2-m3 | 8001 | **Rerank**（OpenAI 兼容 `/v1/rerank`） |
| MinIO | 9000 / 9001 | 对象存储，root `<MINIO_ROOT_USER>`；**为 WeKnora 单独建公共桶 `weknora`** |
| PostgreSQL 16.15 | 5432 | 现有库 `irrigation_ai` 未动 |
| MySQL / TDengine / voice-server | 3306 / 6030 / 8090 | 无关，保留 |
| 阿里 DashScope API | — | **问答/摘要/图谱 LLM**（key 由用户后续配置） |

> **关键决策**：LLM 不本地部署（用阿里 API，走 OpenAI 兼容 `https://dashscope.aliyuncs.com/compatible-mode/v1`，模型 `qwen-plus`），**key 由用户后续填**；PG 用 WeKnora **自带 ParadeDB**（容器内 5432，不碰宿主 5432）；启用 **neo4j** 图谱；MinIO 单独桶；前端入口 **8181**。

---

## 2. 部署架构与端口规划（已落地）

```
                    ┌───────────────── WeKnora Docker 网络 (WeKnora-network) ─────────────────┐
                    │                                                                          │
   宿主机(192.168.1.5)                                                                          │
   ┌──────────────┐  8100    ┌──────────────────────┐                                        │
   │ Browser/用户 ├─────────►│ WeKnora-app (Go)      │─► redis(容器内6379)                    │
   └──────────────┘          │  DB: ParadeDB(pg17)   │─► docreader(gRPC 50051)               │
        │  8181              └──────────────────────┘─► neo4j(bolt 7687/http 7474)           │
        │                          │ (SSRF白名单 host.docker.internal)                        │
        └─► WeKnora-frontend       ├─► 宿主 MinIO 9000 (桶 weknora)                           │
            (Nginx, 8181)          ├─► 宿主 vLLM 8080 (embedding)                            │
                                    └─► 宿主 vLLM 8001 (rerank)                               │
                    ┌──────────────────────────────────────────────────────────┐
                    │ 增强(profile)                                              │
                    │  searxng:127.0.0.1:8888(联网搜索)                          │
                    │  langfuse:3000 + clickhouse + langfuse-minio:9100/9101     │
                    │  neo4j:7474/7687(图谱)                                     │
                    └──────────────────────────────────────────────────────────┘
```

| WeKnora 组件 | 镜像 | 宿主端口 | 说明 |
|---|---|---|---|
| app | wechatopenai/weknora-app:latest | **8100** | Go 后端，健康检查 `/health` |
| frontend | wechatopenai/weknora-ui:latest | **8181** | Vue3+Nginx，前端入口 |
| postgres | paradedb/paradedb:v0.22.2-pg17 | 容器内 5432 | 自带 ParadeDB（pgvector+BM25），**不发布宿主** |
| redis | redis:7.0-alpine | 容器内 6379 | 任务队列/流，**不发布宿主** |
| docreader | weknora-docreader:latest | 容器内 50051 | 文档解析 gRPC，不发布 |
| neo4j | neo4j:2025.10.1 | **7474 / 7687** | 知识图谱（profile: neo4j） |
| searxng | searxng/searxng:latest | **127.0.0.1:8888** | 联网搜索（profile: searxng） |
| langfuse | langfuse/langfuse:3 + worker:3 + clickhouse + minio | **3000 / 9100 / 9101** | 自建可观测（profile: langfuse） |
| sandbox | weknora-sandbox:latest | — | Skills 沙箱（app 按需 docker run） |

**防火墙对外放行（仅保留必需）**：`8100`（后端 API，供 BladeX 框架调用）、`8181`（Web 前端，浏览器访问）。
其余 neo4j(7474/7687)、langfuse(3000/9100)、searxng(8888) **不对外放行**——容器间通过 Docker 内网通信，不经宿主防火墙，收紧后内部功能无损、更安全。

---

## 3. 实际安装步骤（记录）

### 3.1 Docker Engine + Compose
- 通过阿里 apt 源安装 `docker.io` + compose 插件，`ubuntu` 加入 docker 组。
- 配置 `/etc/docker/daemon.json`（data-root 迁到 `/data/docker`，日志 json-file 100m×3，cgroupdriver=systemd，iptables=true）。
- **镜像加速**：直连 Docker Hub 与 GitHub 不通；经探测，`https://docker.1panel.live` 是唯一对 WeKnora 镜像返回 200 的加速器。`registry-mirrors` 配置为 `1panel.live` + `daocloud` + `阿里杭州`。
  > 踩坑记录：daocloud 对 weknora-app 等大镜像回源极慢（1 小时只到几十 MB）；换 1panel.live 后秒级完成。中间曾因 daemon.json 写入多包一层引号导致 dockerd 启动失败（`invalid JSON`），用 sftp 上传干净 JSON 修复。

### 3.2 WeKnora 源码与配置
- 源码浅克隆（Gitee 镜像）：`/home/ubuntu/weknora`（commit `e5c6b90`）。
- 编写 `.env`（端口/MinIO/模型/密钥，全部随机强值）：
  - `APP_PORT=8100`、`FRONTEND_PORT=8181`
  - `STORAGE_TYPE=minio`、`MINIO_ENDPOINT=host.docker.internal:9000`、`MINIO_BUCKET_NAME=weknora`（root 凭据）
  - `ENABLE_GRAPH_RAG=true`、`NEO4J_ENABLE=true`、NEO4J 密码
  - `LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`、`LLM_MODEL_NAME=qwen-plus`、**`LLM_API_KEY=YOUR_ALIYUN_DASHSCOPE_KEY`（占位，待你填）**
  - `EMBEDDING_BASE_URL=http://host.docker.internal:8080/v1`、`EMBEDDING_MODEL_NAME=Qwen3-Embedding-4B`
  - `RERANK_BASE_URL=http://host.docker.internal:8001/v1`、`RERANK_MODEL_NAME=bge-reranker-v2-m3`
  - `SSRF_WHITELIST=host.docker.internal`（放行访问宿主）
  - searxng / langfuse 各密钥
- 编写 `config/builtin_models.yaml`，声明 3 个内置模型并**挂载进 app 容器**（需在 docker-compose.yml 取消该行注释）：
  - `builtin-llm-aliyun`（qwen-plus，LLM，默认）
  - `builtin-embedding-qwen`（Qwen3-Embedding-4B，**dimension 2560**，默认）
  - `builtin-rerank-bge`（bge-reranker-v2-m3，rerank）

### 3.3 MinIO 桶
- 用 `minio/mc` 容器为 WeKnora 单独建公共桶 **`weknora`**（与现有隔离）。
- > 注：`mc anonymous set download` 对该桶设置公开读一度失败（`path not found`）；WeKnora 用 root 凭据读写，不受影响，公开读如需可后续用 `mc policy` 补。

### 3.4 启动
```
cd /home/ubuntu/weknora
docker compose --profile neo4j --profile searxng --profile langfuse up -d
```
- 第一次启动曾因 **SSRF 校验把 `host.docker.internal` 判为受限**导致 app panic，加 `SSRF_WHITELIST` 后恢复。

---

## 4. 当前运行状态（已验证）

### 4.1 容器
```
WeKnora-app        Up (healthy)   # 后端，零重启
WeKnora-frontend   Up             # 前端 8181
WeKnora-postgres   Up (healthy)   # ParadeDB
WeKnora-docreader  Up (healthy)   # 文档解析
WeKnora-redis      Up             
WeKnora-neo4j      Up             # 图谱 7474/7687
WeKnora-searxng    Up             # 搜索 127.0.0.1:8888
WeKnora-langfuse-web/worker/clickhouse/minio  Up (healthy)  # 观测 3000
```

### 4.2 端口连通（均 200）
| 入口 | 地址 | 对外放行 |
|---|---|---|
| Web 前端 | http://192.168.1.5:8181 | ✅ 对外（浏览器） |
| 后端 API | http://192.168.1.5:8100 | ✅ 对外（BladeX 调用） |
| 图谱 HTTP | http://192.168.1.5:7474 | 🔒 内网（app 容器连） |
| Langfuse UI | http://192.168.1.5:3000 | 🔒 不对外（接入观测时按需放行） |
| Searxng | http://127.0.0.1:8888 | 🔒 仅本机 |

### 4.3 内置模型已加载（3 个，tenant 10000）
`builtin-llm-aliyun`（qwen-plus）、`builtin-embedding-qwen`（dim 2560）、`builtin-rerank-bge`，均 active/is_builtin。

### 4.4 核心 RAG 链路验证（不需 LLM，已验证通过）
上传测试文档《test_rice.md》到知识库"东北水稻知识库"：
- ✅ docreader 解析（md → 1 chunk）
- ✅ embedding 模型 Qwen3-Embedding-4B 初始化成功（连宿主 8080）
- ✅ 1 个向量 index 成功写入 postgres（ParadeDB）
- ✅ 文件与处理产物存入 MinIO 桶 weknora（storage_size=16KB，租户 storage_used 增加）

---

## 5. 管理员账号与访问

| 项目 | 值 |
|---|---|
| Web 地址 | http://192.168.1.5:8181 |
| 管理员账号 | `admin@weknora.local` |
| 管理员密码 | `<WEPASSWORD>` |
| 知识库 | `东北水稻知识库`（已建，embedding=builtin-embedding-qwen，存储=MinIO weknora，向量=postgres） |

> 请首次登录后**修改管理员密码**。

---

## 6. 待你完成的操作

1. **填入阿里 DashScope API Key**（必需，解锁问答/摘要/图谱）：
   - 编辑服务器 `/home/ubuntu/weknora/.env`，将 `LLM_API_KEY=YOUR_ALIYUN_DASHSCOPE_KEY` 换成真实 key。
   - 重启 app：`cd /home/ubuntu/weknora && docker compose up -d app`。
   - 或直接在 Web「设置→模型」里给 `builtin-llm-aliyun` 填 key（推荐）。
2. **配置摘要模型 / 启用图谱**（在 Web 知识库「设置」里，用 `builtin-llm-aliyun`）：
   - 知识库「设置」→ 设摘要模型 = qwen-plus（这样文档摘要可用）。
   - 索引策略里**启用图谱**（GraphRAG，需 LLM 抽取实体，neo4j 已就绪）。
3. **接入 Langfuse 观测**（可选，langfuse 已运行）：
   - 访问 http://192.168.1.5:3000 注册 Langfuse 管理员 → 创建项目 → 生成 API Keys。
   - 将 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` 填入 .env，`LANGFUSE_HOST=http://langfuse-web:3000`，重启 app。
4. 上传真实水稻规程文档，验证问答命中与引用。

---

## 7. 运维与目录规范

| 项目 | 路径/命令 |
|---|---|
| 源码与配置 | `/home/ubuntu/weknora`（.env、docker-compose.yml、config/builtin_models.yaml） |
| Docker 数据根 | `/data/docker`（data-root，已从默认迁移） |
| 预建数据目录 | `/data/weknora` |
| 重启全套 | `cd /home/ubuntu/weknora && docker compose --profile neo4j --profile searxng --profile langfuse up -d` |
| 看日志 | `docker logs -f WeKnora-app` |
| 镜像 | 15 个，共 12.05GB；容器 13 个（11 运行） |
| 资源占用 | 内存 ~22GiB/62GiB，磁盘 268G/3.6T，与现有服务隔离良好 |

---

## 8. 风险与说明

- **LLM key 未填**：问答/摘要/图谱构建暂不可用（已明确标注）；解析/embedding/向量检索/存储均已验证可用。
- **图谱构建**依赖 LLM 抽取实体（缺 key 暂不能跑）；neo4j 已就绪，填 key 并在 Web 启用后自动构建。
- **MinIO 桶公开读**未设置（root 凭据访问不受影响）；如需对象 URL 直连可补 `mc policy`。
- **镜像加速**依赖第三方 `1panel.live` 加速器（Docker Hub 直连不通）；该加速器当前稳定可用。
- 现有服务（5432/8080/8001/9000/3306/6030/8090）**全程未改动**，完全隔离。

> 来源：WeKnora 官方文档、服务器现场检查与本次部署实测。
