# WeKnora 水稻知识库 · 运维与恢复手册

> 本文件**仅本地保存，严禁上传 Git/GitHub**（含账号/API Key 等敏感信息）。
> 用途：知识库/智能体新增、出故障恢复、参数回滚时的权威依据。
> 最后更新：2026-10-16

---

## 一、整体架构

| 项 | 值 |
|---|---|
| 服务器 | 192.168.1.5 (ubuntu) |
| Web 入口 | http://192.168.1.5:8181（gateway 反代） |
| API 端口 | 8100（WeKnora 主服务） |
| SSH | `ssh ubuntu@192.168.1.5` |
| 数据库 | docker `WeKnora-postgres`，库名 `weknora`，user `postgres` |
| 图谱 | docker `WeKnora-neo4j`，user `neo4j` |
| gateway | `weknora_gateway/gateway.py`（setsid nohup） |

### 本地模型服务（**systemd 管理，勿用 nohup**）
| 服务 | 模型 | 端口 | 显存 | systemd 服务名 | 启停 |
|---|---|---|---|---|---|
| 8B(多模态) | Qwen3-VL-8B-FP8 | 8002 | ~13.8G | `vl-8b` | `systemctl start/stop vl-8b` |
| Embedding | Qwen3-Embedding-4B | 8080 | ~10.7G | `qwen-embedding` | `systemctl start/stop qwen-embedding` |
| Rerank | bge-reranker-v2-m3 | 8001 | ~2.4G | `rerank` | `systemctl start/stop rerank` |
| Whisper | large-v3-turbo | 8003 | ~1.5G | `whisper` | `systemctl start/stop whisper` |

**⚠️ 启停顺序（最重要）**：先 **8B**（vllm 启动时硬性检查空闲显存 ≥18.2G，不够直接拒启）→ embed → rerank → **whisper 最后**（whisper unit 已加 `After=vl-8b`，开机自启自动排 8B 后）。

**14B（Qwen3-14B-AWQ @8081）**：当前**已停**（本次需求关闭省显存）。若需用：手动 `nohup vllm serve /opt/models/Qwen3-14B-AWQ --host 0.0.0.0 --port 8081 --gpu-memory-utilization 0.45 --max-model-len 16384 --enforce-eager --served-model-name Qwen3-14B`（无 --chat-template，靠 `chat_template_kwargs:{"enable_thinking":false}` 出干净 JSON）。

---

## 二、知识库配置（4 库）

### 1. 水稻生产知识库（主库）
- **ID**：`02252b81-6aed-483a-9d83-ce533779e634`
- **向量**：Qwen3-Embedding-4B（2560 维）| **摘要/标签**：Qwen3-14B（`bb7c76a5`）
- **分块**：chunk_size 512 / overlap 80；**parent-child 开启**（parent 4096 / child 384）
- **解析规则**：pdf/docx/pptx/xlsx/epub→anydoc；md→builtin；csv/txt→simple
- **图谱抽取 extract**：**已配置**（tags 谓词：属于/育成于/审定于/适宜/抗/感/耐/防治… + 示例 text + 节点清单）→ **图谱已建 5.39 万关系**
- **Wiki**：extraction=exhaustive（按品种/栽培/病害/虫害/草害/收获 6 主题分页）；**synthesis_model 当前指向 deepseek（c3648e2c）⚠️ 改 14B 免费用**
- **问题生成 qg**：enabled，3 题，面向稻农/质检员/技术员的自然语言
- **indexing_strategy**：`wiki_enabled=false, graph_enabled=false, vector=true, keyword=true`
- **auto_tag**：enabled，max 3，model=14B（`bb7c76a5`）

### 2. 水稻国家标准库
- **ID**：`70ab5c12-cafd-4ede-a94d-93651b812b17`
- **分块**：512/80，parent-child；md→builtin
- **图谱 extract**：disabled
- **Wiki**：standard；synthesis_model=`64c3dc66`（qwen3.8-max）
- **qg**：enabled 3 | **auto_tag**：14B
- **文档数**：14 篇国标

### 3. 严博士·专家语料库
- **ID**：`32caf8c4-cda4-49aa-b648-28dd6923ffd1`
- 简单配置（分块 512/80），extract/wiki/qg 关闭，auto_tag 14B，文档 1 篇

### 4. 祖小力·专家语料库
- **ID**：`08cbd5e8-e867-44dc-bfca-0eec90d7d6f6`
- 同严博士，文档 1 篇

---

## 三、模型配置（models 表）

| ID | name | 用途 | 端点 | 说明 |
|---|---|---|---|---|
| `builtin-rerank-bge` | bge-reranker | Rerank | host.docker.internal:8001 | 重排序 |
| `builtin-embedding-qwen` | Qwen3-Embedding-4B | Embedding | host.docker.internal:8080 | **2560 维**，default |
| `builtin-llm-aliyun` | qwen-plus | LLM(问答) | dashscope | default |
| `bb7c76a5-…` | **Qwen3-14B** | KnowledgeQA | 192.168.1.5:8081 | **本地免费**，摘要/标签/问答主用，max 4096 |
| `cf30f36e-…` | Qwen3-14B(VLLM) | VLLM | 192.168.1.5:8081 | supports_vision=true，给 wiki-researcher 当 vlm |
| `c3648e2c-…` | **deepseek-flash** | KnowledgeQA | api.deepseek.com | **余额不足(402)烧钱，勿用于批量**；wiki synthesis 在用 |
| `64c3dc66-…` | qwen3.8-max | KnowledgeQA | dashscope | 国标库 wiki synthesis |
| `glm-4.5-flash` (`da23e33b`) | glm-4.5-flash | KnowledgeQA | bigmodel.cn | 用户新加，**免费额度候选** |
| `e8613a83-…` | large-v3-turbo | ASR | 192.168.1.5:8003 | whisper（当前停） |

---

## 四、智能体配置（custom_agents）

| 智能体 | 类型 | 模型 | 温度 | 绑定库 | 特点 |
|---|---|---|---|---|---|
| `f7908d78` 通用水稻问答 | smart-reasoning | 14B | 0.3 | 生产库+国标库 | 通用问答，citation 开 |
| `4284d732` 严博士·水稻专家 | hybrid-rag-wiki | 14B | 0.7 | - | thinking=false，蒸馏自严博士 452 条，rerank_top_k=5 |
| `ee2df5b7` 祖小力·水稻专家 | hybrid-rag-wiki | 14B | 0.7 | - | 四攻四防框架，thinking=false |
| `a998dc55` 水稻文档知识库 | hybrid-rag-wiki | 14B | 0 | - | asr=whisper，vlm=14B |
| `builtin-wiki-researcher` | wiki-qa | 64c3dc66 | 0 | - | vlm=14B |
| builtin-quick-answer | rag-qa | 64c3dc66 | 0 | - | 快速问答 |

**system_prompt 要点**：严/祖智能体已内置完整"心智模型+决策启发式"框架（从逐字稿蒸馏）；国标问答内置"先定义→逐条指标→表格对比→溯源"严谨结构。

---

## 五、摘要 / 标签 / 元数据 分工（底层源码确认）

| 功能 | 底层实现 | 用途 | 现状 |
|---|---|---|---|
| **摘要** | 解析流水线 summary 阶段，存 `knowledges.description` | 检索概括、问答引用、图谱/wiki 素材 | ✅ 566 篇全完成（14B，质量好） |
| **标签** | WeKnora `auto_tag` **只在 datasource 同步时自动打**；普通文档要人工 tag_ids。存 `knowledge_tags`+`knowledge_tag_relations` | 分类筛选、检索过滤 | ⏳ 我用 14B 批量生成（写 DB），进行中 |
| **元数据** | 上传时 API 传 metadata，存 `custom_metadata`(jsonb)；**无 AI 自动生成** | 结构化精确过滤（品种/区域/技术） | ⏳ 我用 14B 批量生成（写 DB），进行中 |

> 关键：**摘要 WeKnora 自动做**；**标签/元数据必须靠外部批量脚本生成写入**（本项目用 14B 免费批量）。

### 标签/元数据批量生成脚本（本地，可恢复）
- 位置：`D:\zhihong\ssh_genall.py`（生产库 550 篇）、`ssh_genmeta3.py`（单篇测试模板）
- 流程：SSH 取文档(title+description) → 本地调 14B(8081, enable_thinking=false) 生成 `{"tags":[...],"metadata":{...}}` → 组装 SQL → `docker cp` 进容器 → `psql -f`
- **knowledge_tags 写入要点**：`id` 是 varchar 无默认 → 用 `gen_random_uuid()::text`；`tenant_id` 是 **integer**（生产库=10000）；insert 用 `WHERE NOT EXISTS` 去重；relation 用 `ON CONFLICT DO NOTHING`
- 进度文件：`D:\zhihong\genall_progress.txt`（当前约 101/566 篇完成）

---

## 六、当前进度快照（2026-10-15 更新）
- 文档总数 **566** | 已有元数据 **201** | 标签 **594** | 关系 **965**（批量续跑中，预计再 30 分钟）
- 摘要全部 completed；**图谱 61260 节点 / 53901 关系**；**wiki 1265 页（1148 已发布）**
- **wiki 稻飞虱 vs 灰飞虱别名混用已清理**（aliases 置空，wiki_page_issues 清零）
- **14B 上下文上限 = 16384**（Qwen3-14B-AWQ 模型限制；32768 时 vllm Engine 初始化失败无法启动，**只有云端大模型支持 32768+**）
- **2026-10-16 更新**：模型服务改用 systemd 管理；当前运行 **vl-8b(8002)+qwen-embedding(8080)+rerank(8001)+whisper(8003)** 全 active；**14B 已停**（省显存）；详见 §十。

---

### 2026-10-16 复核（标签全库覆盖 + 图谱归并 + 题库测试通过）
- **4 库 parse 全 completed**：生产 550 / 国标 14 / 严 1 / 祖 1；error 无
- **摘要**：生产 547/550（3 篇 none，用户确认暂不补）；其余 3 库 completed
- **标签全库覆盖**：生产 550/550、国标 14/14、祖 1/1、严 1/1（本次用 14B 补齐国标 14 篇、祖小力 1 篇、生产库雨久花图片 1 篇标签）
- **图谱归并完成**：nodes 63136→21742、rels 54096→33358；自造谓词清洗（删除交叉引用/发布/来源/涉及/影响/防治时期等 7 个）；dup_names=0；吉粳88 归并到单节点（27→1）
- **wiki**：生产库 757 篇全发布（117 draft 已 publish）
- **智能体引用指令已加回**（f7908d78 system_prompt：段落末尾来源标注 + wiki 直达链接）
- **glm-4.5-flash 测试**：429 账户限流（智谱 bigmodel），非配置问题，问答仍用 14B
- **问答 agent-chat API**：POST `/api/v1/sessions` 建会话 → POST `/api/v1/agent-chat/{session_id}`，body=`{"query":"问题"}`（QARequest.Query 字段，**不是 message**）
- **题库测试 10/10 通过**：详见 `D:\zhihong\水稻知识库测试报告.md`

---

## 七、优化建议（对标豆包回答水平）

### 已达标项
- 检索链路：`向量(2560维) + 关键词 + Rerank` 三路混合；阈值 vector 0.5 / rerank 0.3
- 智能体 system_prompt 已高度专业化（国标严谨结构、严博士/祖小力心智框架）
- 摘要/标签/元数据 14B 免费补齐 → 检索精度大幅提升

### 建议优化（按优先级）
1. **问答模型升级（最关键）**：14B 本地问答能力弱于豆包云端大模型。
   - 首选：问答路由到 **glm-4.5-flash（新加，免费额度）** 或充值后的 **deepseek**；14B 只做解析/摘要/标签（免费后台）。
   - 改法：把生产问答智能体 `f7908d78`、严/祖的 `model_id` 从 `bb7c76a5` 改成目标模型，测试回答质量。
2. **开启图谱检索**：图谱已建 5.39 万关系，`indexing_strategy.graph_enabled` 从 false→true，问答可给"品种↔病害↔防治"关联答案（Hybrid 已含 query_knowledge_graph 工具）。
3. **开启 Wiki 检索**：wiki 已 1265 页，`wiki_enabled` false→true；**wiki synthesis 生产库已改 14B**（`bb7c76a5`，避免 402 烧钱）。✅ 索引已全部开启。
4. **国标库 wiki synthesis 用 14B**：把 `64c3dc66` 改 `bb7c76a5`，避免依赖云端（待执行）。
5. **重启后自检**：三个 vllm 端口（8001/8080/8081）+ gateway(8181) 全部存活；embedding 维度保持 2560；**14B 用 max-model-len 16384（32768 无法启动）**。

---

## 八、故障恢复指南

### 服务重启命令（服务器）
```bash
# systemd 模型服务（8B 先，whisper 最后；sudo 需密码）
echo '<SSH_SUDO_PASSWORD>' | sudo -S systemctl start vl-8b        # 8B，需空闲≥18.2G
echo '<SSH_SUDO_PASSWORD>' | sudo -S systemctl start qwen-embedding
echo '<SSH_SUDO_PASSWORD>' | sudo -S systemctl start rerank
echo '<SSH_SUDO_PASSWORD>' | sudo -S systemctl start whisper      # 最后
systemctl status vl-8b qwen-embedding rerank whisper     # 查状态

# 重启 gateway
cd /home/ubuntu/weknora_gateway && setsid nohup python3 gateway.py > gateway.log 2>&1 &
# 检查 WeKnora 容器
docker ps | grep WeKnora
```

### 摘要/标签/元数据丢失后重跑
1. 确认 14B 在 8081 存活：`curl http://192.168.1.5:8081/v1/chat/completions`
2. 摘要：改 `knowledge_bases.summary_model_id=bb7c76a5` → 对 `summary_status IN ('failed','pending')` 的文档 POST `/api/v1/knowledge/batch-reparse`（脚本 `ssh_resubmit.py`）
3. 标签/元数据：跑 `ssh_genall.py`（含去重，安全幂等）

### 关键 DB 脚本命令
```bash
docker exec WeKnora-postgres psql -U postgres -d weknora -c "UPDATE knowledge_bases SET summary_model_id='bb7c76a5-c8dc-4338-8b78-9c3b5da6bab3' WHERE id IN ('02252b81-6aed-483a-9d83-ce533779e634','70ab5c12-cafd-4ede-a94d-93651b812b17','32caf8c4-cda4-49aa-b648-28dd6923ffd1','08cbd5e8-e867-44dc-bfca-0eec90d7d6f6');"
```
（auto_tag 用 `jsonb_build_object('enabled',true,'max_tags',3,'model_id','bb7c76a5-…','skip_if_tagged',true)`）

---

## 九、操作过程记录（2026 水稻知识库建设全程）
1. 配置 4 个知识库基本信息（名称/简介/所属）→ 模型 → 解析引擎与分块 → 图像与知识图谱 → 高级设置并保存。
2. 清洗 14 个国标 md 文件（去脏数据/统一格式）后入库。
3. 定位并重传 failed 文档；补抽主栽品种图谱。
4. 清洗自造谓词（图谱关系名统一，避免错别/自造关系）。
5. 修智能体会话 404（gateway/路由）。
6. 批量摘要最初用 deepseek → **余额不足 402 全部失败** → 用户充值仍 402。
7. **决策：放弃付费，全切本地 14B**：关 8B+whisper 省显存，4 库摘要模型+auto_tag 改 14B，重跑摘要 → 566 篇全部 completed，质量好。
8. 发现标签/元数据为空（WeKnora 不对普通文档自动生成）→ 用 14B 批量生成标签+元数据写入 DB（进行中）。
9. 图谱已建 5.39 万关系、wiki 1265 页；wiki synthesis 仍指向 deepseek（⚠️ 待改 14B）。
10. **2026-10-16**：补齐国标/祖小力/雨久花图片标签（14B，全库覆盖）；图谱自造谓词清洗 + 实体归并（nodes 21742/rels 33358）；117 篇 draft wiki 发布（757 全发布）；智能体引用指令加回；**题库测试 10/10 通过**（报告见 `水稻知识库测试报告.md`）。**删除待办**：3 篇摘要 none 与 subspan failed 重跑（用户确认不补）。

---

## 十、2026-10-16 模型服务 systemd 启停记录（重要，勿再踩坑）

> 背景：用户要求「停 14B、启 embed/8B/whisper 按顺序」。**正确方式见 `AI服务器_启动与运维文档.md`（systemd 服务）**，勿手动 nohup。

### 1. 权威做法（直接照做即可）
- 这些都是 **systemd 服务**（`vl-8b` / `qwen-embedding` / `rerank` / `whisper`），**不是 nohup**。
- **顺序**：先 `systemctl start vl-8b`（启动需空闲显存 ≥18.2G）→ `qwen-embedding` → `rerank` → **最后 `whisper`**。
- 完整一条脚本（本机可复用，存于 `D:\zhihong\models_reset.sh` 思路）：
  ```bash
  PW='<SSH_SUDO_PASSWORD>'; S(){ echo "$PW" | sudo -S "$@"; }
  S systemctl start vl-8b; sleep 60   # 8B 加载较久，用 curl /v1/models 等就绪
  S systemctl start qwen-embedding; sleep 40
  S systemctl start rerank; sleep 30
  S systemctl start whisper; sleep 15
  # 验证：curl http://127.0.0.1:8002/v1/models（8B）、8080/v1/models（embed）、8001/health、8003/health
  ```

### 2. 本次踩的坑（已全部定位，切勿重犯）
| 坑 | 现象 | 根因 | 正确做法 |
|---|---|---|---|
| **手动 nohup 启动模型** | 服务起了但和 systemd 重复，显存翻倍 | 这些服务本应是 systemd 管理 | 一律用 `systemctl`，不要 nohup |
| **pkill -f '模型名' 自匹配** | SSH 会话被杀、命令无返回 | `pkill -f` 会匹配到执行命令自身（命令行含该串） | 把 pkill 写进 `.sh` 脚本文件再 `bash xx.sh` 执行，或 `pkill -f '[Q]wen3-14B'`（字符类技巧） |
| **8B 反复启动失败** | 每次失败残留 ~15G 引擎进程，叠加占满 GPU | 失败后残留 `VLLM::EngineCore` 孤儿进程未清，free 只剩 2.9G < 17.87G | 先 `pkill -9 -f 'vllm serve'` 清空残留，确认 `nvidia-smi` free ≥18.2G 再启 8B |
| **sudo 密码不生效** | `[sudo] password` 后无结果 | paramiko exec_command 无 stdin，`echo | sudo -S` 管道失效 | 脚本文件内 `echo '密码' | sudo -S 命令`；或 paramiko `chan.send('密码\n')` |
| **embed 端口/engine 残留** | 8080 无响应但 GPU 有 2 个 ~10G engine | 手动 embed 与 systemd embed 重复 | 全停后统一用 systemctl 拉，只保留一个 |

### 3. 最终恢复结果（2026-10-16）
- **重启服务器**（`sudo reboot`）后，systemd 自动拉起 embed/rerank；再按「8B→whisper」补完。
- 最终全部就绪：**vl-8b(8002) / qwen-embedding(8080) / rerank(8001) / whisper(8003) 全 active 且 API 健康**；**14B 已停**（进程=0）。
- GPU 占用 27.8G/32G（free ~4.2G），符合运维文档显存规划。
- 服务文件：`/etc/systemd/system/vl-8b.service`（备份 .bak2）、`qwen-embedding.service`、`rerank.service`、`whisper.service`（备份 whisper.service.bak，含 `After=vl-8b`）。

### 4. 以后管理口诀
1. 模型服务只用 `systemctl`，**永不手动 nohup**。
2. 启 8B 前先看 `nvidia-smi` free，≥18.2G 才启动；否则先清残留。
3. 任何 pkill 模型名，**写进脚本文件再执行**，防误杀会话。
4. 顺序固定：8B → embed → rerank → **whisper 最后**。
