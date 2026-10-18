# bge-reranker-v2-m3 重排模型部署文档
## 一、部署基本信息
| 项目 | 配置 |
|---|---|
| 模型名称 | BAAI/bge-reranker-v2-m3 |
| 服务器地址 | 192.168.1.5 |
| 服务端口 | 8001 |
| API地址 | http://192.168.1.5:8001/v1 |
| 模型路径 | /opt/models/bge-reranker-v2-m3 |
| 运行虚拟环境 | /opt/vllm-venv |
| systemd服务名 | rerank.service |
| 显存占用 | 约2GB（GPU：RTX5090 32G） |
| 最大上下文长度 | 8192 tokens |
| 启动时间 | 约15秒 |

---
## 二、服务管理命令
```bash
# 启动服务
sudo systemctl start rerank.service

# 停止服务
sudo systemctl stop rerank.service

# 重启服务
sudo systemctl restart rerank.service

# 查看服务状态
sudo systemctl status rerank.service

# 查看实时日志
tail -f /home/ubuntu/rerank.log

# 开机自启状态：已默认开启，无需额外配置
```

---
## 三、接口调用说明
### 1. 查询可用模型
```bash
curl http://192.168.1.5:8001/v1/models
```
返回：
```json
{
  "object": "list",
  "data": [
    {
      "id": "bge-reranker-v2-m3",
      "object": "model"
    }
  ]
}
```

### 2. 重排打分接口
```bash
curl -X POST http://192.168.1.5:8001/v1/score \
  -H "Content-Type: application/json" \
  -d '{
    "model": "bge-reranker-v2-m3",
    "text_1": "水稻分蘖期水层怎么管理",
    "text_2": "水稻分蘖期保持3-5cm浅水层促进分蘖"
  }'
```
返回分数越高代表文本和问题越相关，范围0-1。

---
## 四、资源占用
| 资源项 | 占用值 |
|---|---|
| 显存 | 2GB（模型加载+计算） |
| 内存 | 约3GB |
| CPU | 无计算压力，主要走GPU |
| 并发支持 | 同时支持10-20个重排请求无压力 |

---
## 五、配套运行服务清单
| 服务 | 端口 | 用途 | 开机自启 |
|---|---|---|---|
| Qwen3-Embedding-4B | 8080 | 向量嵌入 | 已配置 |
| bge-reranker-v2-m3 | 8001 | 重排打分 | 已配置 |
| PostgreSQL (pgvector) | 5432 | 向量数据库 | 系统自启 |
| MySQL | 3306 | 业务数据库 | 系统自启 |
| Redis | 3379 | 缓存 | 系统自启 |

---
## 六、注意事项
1. 服务已配置崩溃自动重启（Restart=always），异常退出后10秒自动拉起
2. 显存占用固定，不会随着请求数上涨
3. 首次请求会触发模型warmup，响应稍慢，后续请求正常<50ms返回
4. 后续需要启动Qwen3-14B大模型时，调整启动参数gpu-memory-utilization即可，不会影响现有服务
