# AI 服务器 douyin-distiller Web 部署文档

> 版本：v1.0　适用服务器：192.168.1.5（Ubuntu 24.04.4 LTS）　交付时间：2026-10-10
> 关联服务：本地 whisper 语音转写(8003)、f2 抖音采集下载、飞书多维表、三通道 ASR

---

## 1. 概述

### 1.1 目标
实现「**抖音博主主页 → 自动批量下载全部视频 → ASR 语音转写 → 写入飞书多维表**」的完整流水线，并提供 Web 控制台：
- 网页实时显示**下载进度**与**转写进度**
- 语音引擎页面可切换：**本地 whisper(8003) / 讯飞 / 飞书**
- cookie 页面录入，不写死在配置
- 飞书多维表字段自动创建、按视频链接去重、批量写入

### 1.2 设计来源
还原原版 `AppApp777/douyin-distiller`（douyin-transcript-exporter）的逐字稿采集 + 飞书写入思路，并用 **f2（采集+下载 mp4）+ whisper(8003) ASR** 补足原版"只抓字幕不下视频、依赖平台字幕"的短板，独立成可给 BladeX 等外部系统调用的 Web 服务。

### 1.3 技术选型
| 组件 | 用途 | 说明 |
|---|---|---|
| FastAPI + uvicorn | Web 框架 | 独立 venv，端口 9009 |
| f2（Johnserf-Seed） | 采集 + 下载 mp4 | 从源码 `pip install -e` 安装，版本一致 |
| 本地 whisper 8003 | ASR 引擎 1 | 复用已部署的 large-v3-turbo 服务（OpenAI 兼容） |
| 讯飞语音听写 | ASR 引擎 2 | WebSocket v2/iat，凭据待用户申请 |
| 飞书语音识别 | ASR 引擎 3 | REST API，凭据待用户申请 |
| 飞书多维表 | 写入目标 | REST API + httpx，自动建字段/去重/批量写入 |

---

## 2. 服务器现状与规范遵循

- 系统：Ubuntu 24.04.4 LTS，208 核，内存 62Gi，磁盘 3.2T 可用
- Python：3.12.3；pip 使用阿里云镜像源
- 现有 /opt 规范：**每个服务独立 venv + systemd**（whisper-venv、vllm-venv-new、h3-venv 等）
- 防火墙：ufw（deny incoming），按需放行端口
- 本次遵循同样规范：`/opt/douyin-web`（代码）+ `/opt/douyin-web-venv`（独立环境）+ `/opt/f2`（源码）+ systemd `douyin-web.service`

---

## 3. 目录结构（服务器）

```
/opt/
├── douyin-web/                  # Web 应用代码
│   ├── app.py                   # FastAPI 主程序（路由、后台任务、配置接口）
│   ├── worker.py                # 流水线编排 + 任务进度管理
│   ├── f2_worker.py             # f2 集成（解析博主、采集、逐条下载）
│   ├── asr_client.py            # 三通道语音转写（本地/讯飞/飞书）
│   ├── lark_client.py           # 飞书多维表读写（REST API）
│   ├── config.py                # 配置读写（settings.json）
│   ├── settings.json            # 运行配置（cookie/引擎/飞书凭据/字段设计）
│   ├── requirements.txt         # 依赖清单
│   ├── data/                    # 下载视频 + 转写产物（自动创建）
│   └── static/index.html        # Web 控制台前端
├── douyin-web-venv/             # 独立虚拟环境（Python 3.12.3）
├── f2/                          # f2 源码（pip install -e 安装）
├── whisper-venv/                # 既有 whisper 服务（未动）
└── ...                          # 其余既有服务（未动）
```

---

## 4. 部署步骤（已执行）

### 4.1 上传代码与源码
- 本地代码目录 `douyin-web/` → 服务器 `/home/ubuntu/douyin-web/`
- 本地 f2 源码打包（排除 .git/docs/tests）→ `/home/ubuntu/f2-src.zip`

### 4.2 拷贝 + 解压 + 建 venv
```bash
sudo cp -r /home/ubuntu/douyin-web /opt/douyin-web
sudo chown -R ubuntu:ubuntu /opt/douyin-web
sudo mkdir -p /opt/f2 && cd /opt/f2 && unzip -oq /home/ubuntu/f2-src.zip
sudo python3 -m venv /opt/douyin-web-venv
sudo chown -R ubuntu:ubuntu /opt/douyin-web-venv
```

### 4.3 安装依赖 + f2
```bash
/opt/douyin-web-venv/bin/pip install -U pip
/opt/douyin-web-venv/bin/pip install -r /opt/douyin-web/requirements.txt
cd /opt/f2 && /opt/douyin-web-venv/bin/pip install -e .
```
> 注意：`lark-oapi` 不需安装（飞书写入走 httpx REST API）。

### 4.4 验证导入
```bash
/opt/douyin-web-venv/bin/python -c "import fastapi,uvicorn,httpx,websockets; print('OK')"
/opt/douyin-web-venv/bin/python -c "from f2.apps.douyin.handler import DouyinHandler; print('F2 OK')"
cd /opt/douyin-web && /opt/douyin-web-venv/bin/python -c "import app,config,worker,f2_worker,asr_client,lark_client; print('APP OK')"
```

### 4.5 systemd 服务
`/etc/systemd/system/douyin-web.service`：
```ini
[Unit]
Description=douyin-distiller Web (FastAPI :9009)
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/douyin-web
ExecStart=/opt/douyin-web-venv/bin/uvicorn app:app --host 0.0.0.0 --port 9009
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable douyin-web
sudo systemctl start douyin-web
systemctl is-active douyin-web   # active
systemctl is-enabled douyin-web  # enabled
```

### 4.6 防火墙放行
```bash
sudo ufw allow 9009/tcp
```

### 4.7 验证
```bash
curl http://127.0.0.1:9009/          # HTTP 200
curl http://127.0.0.1:9009/health    # {"ok":true,"service":"douyin-distiller-web"}
```
局域网访问：`http://192.168.1.5:9009`（已验证 HTTP 200）。

---

## 5. 配置（Web 页面设置）

访问 `http://192.168.1.5:9009` → 「设置」页，配置保存在 `/opt/douyin-web/settings.json`：

| 配置项 | 路径 | 说明 |
|---|---|---|
| 抖音 cookie | `douyin.cookie` | 采集/下载必需，浏览器登录 douyin.com 抓取，页面录入 |
| 语音引擎 | `asr.engine` | `local`(默认) / `xfyun` / `feishu` |
| 本地 whisper | `asr.local.url` | 默认 `http://192.168.1.5:8003/v1/audio/transcriptions` |
| 讯飞 | `asr.xfyun.*` | app_id / api_key / api_secret（待申请） |
| 飞书语音 | `asr.feishu.*` | app_id / app_secret / api_endpoint（待申请） |
| 飞书写表 | `feishu_table.*` | app_id / app_secret / base_token / table_id（待申请，我来建表） |

> 敏感项在 `/api/settings` 读取时脱敏（打码）。

---

## 6. 多维表字段设计（自动创建）

| 字段名 | 类型 | 备注 |
|---|---|---|
| video_title | 文本 | 作品标题 |
| video_url | 超链接 | `{"text":"..","link":".."}` |
| publish_time | 日期 | 毫秒时间戳 |
| like_count / comment_count / share_count / collect_count | 数字 | 互动数据 |
| duration | 数字 | 单位秒 |
| transcript | 长文本 | 转写逐字稿 |
| status | 单选 | 未下载/已下载/转写完成/失败 |
| local_file_path | 文本 | 本地 mp4 路径 |
| author_nickname | 文本 | 博主昵称 |
| desc | 长文本 | 作品描述 |
| aweme_id | 文本 | 视频 ID |

---

## 7. Web 使用流程

1. 打开 `http://192.168.1.5:9009` → 设置页录入 cookie（语音引擎选本地 whisper）
2. 任务页粘贴博主主页链接 → 开始任务
3. 流水线自动执行：采集全部作品 → 逐条下载 mp4 → 转写 → 写入飞书多维表
4. 页面实时刷新每条视频的下载/转写状态，可展开查看逐字稿

## 8. API 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 控制台页面 |
| GET | `/health` | 健康检查 |
| POST | `/api/task` | 创建任务（body: `{"url":"博主主页"}`）→ 返回 task_id |
| GET | `/api/tasks` | 任务列表（含每条视频进度） |
| GET | `/api/task/{id}` | 单任务详情 |
| GET | `/api/settings` | 读取配置（脱敏） |
| POST | `/api/settings` | 更新配置（body: `{"values":{"douyin.cookie":".."}}`） |

---

## 9. 联调与验收

前置：cookie、飞书凭据、讯飞凭据（如用）。

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1 | 设置页录入 cookie，选本地 whisper | 保存成功 |
| 2 | 任务页输入博主主页链接，开始任务 | 任务创建，进入运行中 |
| 3 | 观察采集进度 | 作品数逐条增加 |
| 4 | 观察下载进度 | mp4 逐条下载到 `/opt/douyin-web/data/<task>/` |
| 5 | 观察转写进度 | 逐条转写（whisper 8003），逐字稿可查看 |
| 6 | 飞书配置后 | 多维表出现记录，video_url 去重生效 |
| 7 | 切换讯飞/飞书引擎 | 重新任务，走对应引擎转写 |

## 10. 故障排查

| 问题 | 处理 |
|---|---|
| 页面打不开 | `systemctl status douyin-web`、`journalctl -u douyin-web -n 30`；确认 9009 监听、防火墙已放行 |
| 采集不到作品 | cookie 过期/无效，重新录入 |
| 下载失败 | 检查抖音 cookie、`/opt/douyin-web/data` 写权限、网络 |
| 转写失败 | `curl http://192.168.1.5:8003/health` 确认 whisper 存活；日志看错误 |
| 飞书写入失败 | 核对 app_id/app_secret/base_token/table_id 权限与字段 |

---

## 12. 飞书多维表接入与联调（实测记录）

### 12.1 必要凭据
- **app_id / app_secret**：飞书开放平台创建企业自建应用，凭证与基础信息页获取
- **多维表链接**：用户给的 wiki 链接 `https://my.feishu.cn/wiki/{token}?table=xxx`
- **关键结论**：wiki 的 **wiki_token 可直接当作 base_token** 使用（不需要 wiki 权限解析 obj_token）；但**URL 里的 `table=xxx` 可能不是真正的 table_id**，需用 API 列表确认（本项目实测正确的 table_id=`tbltvfCg5kVk4WyL`，URL 里的 `wbpdYXgLSd` 报 WrongTableId）

### 12.2 应用身份写多维表的三个前提（缺一不可，官方文档确认）
1. 开通 **应用身份(tenant_access_token)** 的 `bitable:app`（查看、评论、编辑和管理多维表格）权限——用户身份的不算
2. **发布版本**使权限生效（可用范围需包含多维表所有者）
3. **把应用添加为多维表的协作者**（右上角「···」→「···更多」→「添加文档应用」搜应用名）；**开启高级权限的多维表需给应用「可管理」权限**
   > 未满足则建字段/写记录返回 **91403 Forbidden**（文档明确：91403 = 调用身份无文档读写权限）

### 12.3 建字段 API 参数（实测踩坑）
- `type`：数字（text=1, number=2, select=3, datetime=5, **url=15**）——**url 不是 14**！
- `ui_type`：**字符串枚举**（Text/Number/SingleSelect/DateTime/Url）——不是数字字符串
- select 字段需带 `property={"options":[{"name":"未下载"},...]}`
- 建字段报 `99992402 field validation failed` + `ui_type is optional` = ui_type 传错；报 `type is optional` = type 传错

### 12.4 记录写入字段格式（实测）
- text：字符串
- number：数字
- datetime：毫秒时间戳
- url：`{"text":"显示文本","link":"https://..."}`
- select：字符串（选项名）
- 主字段（primary）需填值否则可能报错——本项目主字段「文本」同步填 video_title
- 飞书单选字段值：字符串或单元素数组均可

### 12.5 联调结果（已实测通过）
采集严博士号 20 作品 → 下载 mp4 → whisper 转写 → 写入飞书，2 条真实记录成功落库，表内字段完整（标题/链接/时间/时长/逐字稿/状态/昵称/描述/aweme_id）。

### 12.6 已知限制
- f2 主页列表接口**不返回点赞/评论/分享/收藏统计**（statistics=null），like_count 等字段为 0；如需可后续按单条视频详情接口补齐
- 采集/下载依赖抖音 cookie 登录态，cookie 会过期需定期更新（页面重新录入）
- 讯飞/飞书语音通道为预留（未提供凭据），默认用本地 whisper

---

## 13. 性能与安全建议

- 下载/转写为后台任务，博主作品多时（数百条）耗时较长，页面轮询 3s 刷新
- 语音转写复用 whisper 8003（GPU），不额外占用显存
- 敏感凭据存 `settings.json`，页面读取脱敏；建议限制服务器 9009 仅内网访问
- 如长期大批量采集，注意抖音接口风控与频率

---

## 14. v2 功能升级（2026-10-10）

### 14.1 转写文本标点/断句恢复
- whisper 默认输出无标点连续文本，新增标点恢复步骤：转写后调用本地多模态 LLM 补充中文标点与断句
- LLM 配置（settings.json `llm` 段）：默认 `http://192.168.1.5:8002/v1` + `Qwen3-VL-8B-FP8`（本地 vLLM，复用多模态模型做文本标点），页面「设置→标点恢复(LLM)」可改
- 长文本按 1500 字分批，避免超本地模型上下文（max_model_len=4096）；未配置 LLM 或失败则原样返回
- 实测：`水稻苗期一共就30天。稻苗是从一粒种子长到15公分这么高，它能吸收多少肥……`（标点/断句正常）

### 14.2 video_url 存本地下载地址
- video_url 字段改为存**本地 mp4 可访问地址**（不再是抖音链接）：`http://192.168.1.5:9009/data/<task>/<file>.mp4`（URL 编码）
- 实现：app.py 挂载 `/data` 静态目录（指向下载根目录）；worker 下载后生成本地 URL 存入任务状态与飞书 video_url；页面「查看视频」可播放/下载
- server.base_url 在 settings.json 配置，默认 `http://192.168.1.5:9009`（已验证 /data 静态访问 200）

### 14.3 前端界面美化
- 渐变顶栏、卡片布局、任务进度条、状态徽章、视频查看按钮、逐字稿展开；设置页新增「标点恢复(LLM)」

### 14.4 部署注意（已修正）
- systemd 重启需带 sudo 密码：`echo '<密码>' | sudo -S systemctl restart douyin-web`（SSH 无 tty 时 sudo 静默失败→服务仍跑旧代码；已用 PID 变化验证修复）
- 整文件覆盖上传 settings.json 会清空服务器已配的 cookie/飞书凭据 → 已改为服务器侧修补，勿再整文件覆盖
