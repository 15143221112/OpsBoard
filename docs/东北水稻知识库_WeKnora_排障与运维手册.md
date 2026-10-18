# 东北水稻知识库 · WeKnora 排障与运维手册（含 FAQ / 社区 / 踩坑汇总）

> 服务器：192.168.1.5（Ubuntu 24.04，RTX 5090 32G / 62G 内存）
> 用途：东北水稻知识库 WeKnora（RAG + Wiki + 知识图谱 + 图像识别 + 自动标签）
> 文档更新：2026-10-15
> 适用：WeKnora 主服务（Docker 13 容器）+ 本地模型栈（vLLM / Whisper）

---

## 〇、2026-10-15 重大故障复盘（本次事件）

### 故障现象
- 8181 前端白屏，智能体、4 个知识库全部看不见。
- 后端日志：`[gin] registered 0 routes`，登录 API 返回 404/502。

### 根因（已定位）
- `schema_migrations` 版本记录被人为 `force` 到 63/93/9999，而 **app 容器内迁移文件真实最高版本是 110**。
- app 每次启动认为有大量 pending 迁移，逐条执行，但 **DB 实际表结构早已建到最新（browser_devices、memory_subjects.extraction_state 等对象已存在）**，于是每条迁移都撞 `already exists` → 迁移失败 → `initDatabase` 中止 → **业务路由不注册 → 0 routes → 全 API 404 → 前端拿不到数据**。

### 修复（已验证）
1. 查容器内真实迁移最高版本：
   `docker exec WeKnora-app sh -c 'ls /app/migrations/versioned' | grep -oE '^[0-9]+' | sort -n | tail` → **110**
2. 把 schema 修正到该版本：
   `docker exec WeKnora-postgres psql -U postgres -d weknora -c "UPDATE schema_migrations SET version=110, dirty=false;"`
3. 重启 app：`docker restart WeKnora-app`
4. 日志确认：`Database is up to date (version: 110)`，迁移不再失败。

> ⚠️ 教训：**不要随意 `force` schema_migrations 版本**。迁移框架要求版本号必须存在于迁移文件列表；DB 表结构与版本号必须一致。正确做法是先查 `docker exec WeKnora-app sh -c 'ls /app/migrations/versioned'` 的真实最高版本，再 force 到该值。

### 遗留问题（迁移已过，但 0 routes 仍偶现）
- 迁移成功后个别启动 `registered 0 routes` 依旧出现 → 说明 0 routes 不止迁移一个原因（可能与 app 启动早期依赖组件初始化时序有关）。**最终处理：docker compose down + up 重新初始化全套容器**，见 §一。

---

## 一、正确部署 / 启动 / 重启

### 部署方式（来自部署文档，勿改）
```bash
cd /home/ubuntu/weknora
docker compose --profile neo4j --profile searxng --profile langfuse up -d
```
- 源码与配置：`/home/ubuntu/weknora`（.env、docker-compose.yml、config/builtin_models.yaml）
- Docker 数据根：`/data/docker`；预建数据目录 `/data/weknora`
- 前端：8181（Nginx/WeKnora-ui）；后端 API：8100（Go）
- 管理员：`admin@weknora.local` / `<WEPASSWORD>`（登录后请改密）

### 重启 / 重建
```bash
# 重启单个
docker restart WeKnora-app
# 重建全套（保留数据卷）
cd /home/ubuntu/weknora && docker compose --profile neo4j --profile searxng --profile langfuse down
cd /home/ubuntu/weknora && docker compose --profile neo4j --profile searxng --profile langfuse up -d
```

### 关键容器 / 端口
| 组件 | 端口 | 说明 |
|---|---|---|
| app | 8100（对外 8181 前端代理） | Go 后端，健康检查 /health |
| frontend | 8181 | Vue3 + Nginx 前端入口 |
| postgres(ParadeDB) | 容器内 5432 | pgvector + BM25，不发布宿主 |
| redis | 容器内 6379 | 任务队列 |
| docreader | 容器内 50051 | 文档解析 gRPC |
| neo4j | 7474 / 7687 | 知识图谱（profile: neo4j） |
| searxng | 127.0.0.1:8888 | 联网搜索（profile: searxng） |
| langfuse | 3000 / 9100 / 9101 | 观测（profile: langfuse） |

---

## 二、本地模型栈（systemd，独立于 Docker）

| 服务 | 端口 | 显存 | 参数 |
|---|---|---|---|
| vl-8b（Qwen3-VL-8B-FP8） | 8002 | ~13.8G | `--gpu-memory-utilization 0.57 --max-model-len 4096 --enforce-eager` |
| qwen-embedding（Qwen3-Embedding-4B） | 8080 | ~10.7G | 维度 **2560**，OpenAI 兼容 /v1/embeddings |
| rerank（bge-reranker-v2-m3） | 8001 | ~2.4G | /v1/rerank |
| whisper（faster-whisper） | 8003 | ~1.5G | 语音转写 |

**启动顺序（重要）**：先 8B（需空闲显存 ≥18.2G）→ 再 whisper（`After=vl-8b` 已配）。
```bash
sudo systemctl start vl-8b    # 8B 必须先起
sudo systemctl start whisper  # 再起 whisper
sudo systemctl status vl-8b qwen-embedding rerank whisper
```
> ⚠️ **不要**把 8B `--max-model-len` 提到 8192：32G 显存会 OOM。知识库长文本问答用云端大模型（DeepSeek）。

---

## 三、知识库 / 模型 / 图谱关键参数

### 4 个知识库
| 库 | ID | 规模 |
|---|---|---|
| 水稻生产知识库（生产库） | 02252b81-6aed-483a-9d83-ce533779e634 | 581 篇 |
| 国标库 | 70ab5c12-cafd-4ede-a94d-93651b812b17 | 14 |
| 严博士库 | 32cafc8c-cda4-49aa-b648-28dd6923ffd1 | 1 |
| 祖小力库 | 08cbd5e8-e867-44dc-bfca-0eec90d7d6f6 | 1 |

### 模型
| 用途 | 模型 | 说明 |
|---|---|---|
| Wiki 解析 | DeepSeek V4.1 Flash（c3648e2c…） | 输出上限 384000，需 API 余额 |
| 本地 14B（Qwen3-14B） | bb7c76a5… | max-model-len **16384**，**wiki 32768 会报错**，勿用于 wiki |
| 图谱 / 图像 | cf30f36e… | 26 农业谓词，已挂载 prompt 模板 |
| Embedding | builtin-embedding-qwen（8080） | 2560 维 |

### 图谱
- neo4j HTTP 7474，用户 neo4j，密码在 .env `NEO4J_PASSWORD`。
- 生产库图谱已 2035 条关系，26 农业谓词（属于/育成于/审定于/抗/感/耐/防治…）。
- ⚠️ 14B 图谱偶发主宾颠倒（如"白叶枯病→抗→稻瘟病"），**生产图谱建议用 DeepSeek**。

---

## 四、FAQ

### Q1 前端 8181 白屏 / 知识库看不见？
- 看 app 日志：`docker logs WeKnora-app --since 2m | grep -iE 'registered|migration|Continuing'`
- 若 `registered 0 routes`：先按 §〇 检查 schema_migrations 是否与容器内迁移最高版本(110)一致；再 `docker compose down + up` 重建。
- 若 `registered 200+ routes` 但页面仍白：清浏览器缓存或强制刷新，改用无痕窗口。

### Q2 Wiki 报 `'max_tokens' is too large: 32768. This model's maximum context length is 16384 tokens`？
- 根因：用了本地 14B（max-model-len=16384）跑 wiki（代码固定 32768）。
- 解决：wiki 解析模型切到 **DeepSeek**（输出上限 384000）。

### Q3 报 `402 Insufficient Balance`？
- DeepSeek API 余额不足，需在 DeepSeek 平台充值（建议一次性充 ¥50，4 库 wiki 全量预算 ¥20–40）。

### Q4 图谱实体/关系不准或主宾颠倒？
- 弃用本地 14B，图谱抽取模型改用 DeepSeek；prompt 模板已内置 26 农业谓词。

### Q5 自动打标签？
- 生产库/国标库 `auto_tag_config` enabled；已建定时任务检查重跑完成后触发打标签（cron `0 */6 * * *`）。

### Q6 批量重跑怎么提交？
- `POST /api/v1/knowledge/batch-reparse {kb_id, ids}`，每批 ≤200 个。

### Q7 队列卡住怎么清？
- `docker exec WeKnora-redis redis-cli -a <REDIS_PASSWORD> --no-auth-warning`，然后 `DEL $(... KEYS 'asynq:*')`（密码在 /home/ubuntu/weknora/.env）。

---

## 五、社区 / 官方文档 / FAQ 地址

> 以下为 WeKnora 开源项目官方入口（需联网访问）：

- **GitHub 主仓库**：`https://github.com/wechatopenai/weknora`
- **Issues（社区问题反馈/搜索）**：`https://github.com/wechatopenai/weknora/issues`
- **官方文档（技术手册）**：仓库 `docs/` 目录，部署/API 说明见仓库 README 与 wiki。
- **Changelog / 版本发布**：仓库 `CHANGELOG.md` / Releases。
- **Docker Hub 镜像**：`wechatopenai/weknora-app:latest`、`wechatopenai/weknora-ui:latest`
- **ParadeDB（PostgreSQL 向量库）**：`https://paradedb.com`
- **Neo4j 文档**：`https://neo4j.com/docs`
- **Langfuse（可观测）**：`https://langfuse.com`
- **DeepSeek API 文档/控制台**：`https://platform.deepseek.com`（充值、余额、模型：DeepSeek V4.1 Flash）

> 排查优先顺序：先查本手册 §〇/§四 → 再看 GitHub Issues 搜索同类报错 → 最后查官方 docs。

---

## 六、踩坑与注意事项汇总（务必遵守）

1. **不要乱改 schema_migrations**：版本必须等于 app 容器内迁移文件真实最高版本（当前 110）。改前备份：`CREATE TABLE schema_migrations_bak AS SELECT * FROM schema_migrations;`
2. **不要用本地 14B 跑 wiki**（max-model-len 16384 < 32768，必报错）；wiki/图谱用 DeepSeek。
3. **不要增大 8B max-model-len**（4096 是最优，8192 会 OOM）；长文本用云端大模型。
4. **模型启动顺序**：先 8B → 再 whisper（8B 需空闲 ≥18.2G）。
5. **镜像加速**依赖第三方 `1panel.live`；daemon.json 若写坏（多包引号）会导致 dockerd 起不来，用 sftp 传干净 JSON 修复。
6. **SSH 执行命令**：paramiko `chan.recv` 有时返回空/退出码异常，命令输出先重定向到 `/tmp` 文件再 sftp 读取，失败重试。
7. **改动服务器文件前先备份**（`.bak` 后缀）。
8. **前端恢复验证链路**：登录 API 200 带 token → GET /api/v1/knowledge-bases 返回 4 库 → 浏览器 8181 渲染出页面。
9. **新镜像 max_tokens patch 未上线**：`wiki_ingest.go` 的 `MaxTokens:4096` 补丁因 MinIO `resource://` 校验不过被回退，勿强行启用。
10. **图谱谓词模板已挂载**：`config/prompt_templates/graph_extraction.yaml` 在 docker-compose 挂载，改后需重启 app 生效。

---

*有问题先查本手册，再按 §〇→§四→GitHub Issues 顺序排查。*
