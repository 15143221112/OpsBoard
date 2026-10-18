# XH Panel · 服务器部署文档集

本目录收集 XH Panel 所管理的 AI 服务器上各服务的**部署 / 运维 / 排障文档**，供面板「文档」列直接引用。

> ⚠️ 公开仓库已脱敏：所有真实账号、密码、Token 均已替换为 `<PLACEHOLDER>`。生产凭据只保存在服务器环境变量与内部运维文档中，请勿在本仓库提交任何敏感信息。

## 文档索引（按应用）

| 应用 | 文档 |
|------|------|
| **rerank** | `bge-reranker-v2-m3部署文档.md` |
| **vl-8b** | `Qwen3-VL-8B_多模态部署文档.md` · `Qwen3VL8B_替换部署方案.md` · `Qwen2.5VL7B_多模态部署方案.md` · `Qwen3-VL-8B_压力测试报告.md` |
| **whisper** | `AI服务器_Whisper语音转写部署文档.md` |
| **xh-distiller** | `AI服务器_douyin-distiller_部署文档.md` |
| **weknora** | `WeKnora_运维手册.md` · `东北水稻知识库_WeKnora_AI服务器部署文档.md` · `东北水稻知识库_WeKnora_排障与运维手册.md` · `农业AI知识库服务器部署文档（Ubuntu）.md` |
| **prometheus** | `AI服务器_监控系统_Prometheus部署文档.md` |
| **服务器整体** | `AI服务器_启动与运维文档.md` |
| **面板自身** | `App_Panel_部署说明.md` · `服务器应用管理面板_开发方案.md` |

## 部署总览（XH Panel 管理范围）

面板通过 systemd / Docker 统一管理服务器上的 **30 个应用**，涵盖：AI 推理（rerank / vl-8b / qwen3-14b / whisper / embedding / voice-server）、RAG 知识库（weknora 及其依赖集群）、对象存储（minio）、监控（prometheus / grafana / alertmanager / node-exporter / dcgm-exporter）、数据库（mysql / postgresql / tdengine）与工具链（xh-distiller / comfyui / langfuse）。

- 查看运行状态、端口、显存 / 内存占用：`XH Panel 首页`
- 启停 / 重启 / 日志：`XH Panel 应用操作`
- 详细部署方式：见上方各应用文档
