# App Panel · 服务器应用管理面板 · 部署与使用说明

> 部署时间：2026-10-18 · 服务器：192.168.1.5（Ubuntu 24.04）
> 状态：✅ 已部署并真实验证通过

## 功能清单（P0 + 1/2/3/6 已全部实现）

- ✅ 登录鉴权（SQLite users 表存账号，bcrypt 加密，JWT token）
- ✅ 应用总览：卡片式网格，每应用独立区域块
- ✅ 卡片 / 列表 双视图切换，列表带排序 + 搜索 + 分组/状态/部署方式筛选
- ✅ 端口号、部署方式（venv/docker/native）、部署路径、备注
- ✅ 显存占用（按 PID 匹配 nvidia-smi，真实数据）
- ✅ 内存占用（按 PID 匹配 ps）
- ✅ 运行/停止/健康状态（systemctl is-active + curl /health 探活）
- ✅ 启停/重启按钮（sudoers 白名单，最小权限）
- ✅ 开机自启管理（enable/disable，显示 enabled/disabled 状态）
- ✅ 实时日志弹窗（SSE 推送 + 历史日志）
- ✅ 网页直达（网页类服务一键打开）
- ✅ 分组筛选（全部/AI/数据库/Docker/Web/存储）
- ✅ 顶部 GPU 显存分配饼图 + 内存占用条 + 服务统计
- ✅ 历史趋势图（GPU 显存/利用率/内存，后台每30s采样存SQLite，保留7天，支持1h/6h/24h）
- ✅ 操作日志（记录启停/登录/自启/增删改等操作）
- ✅ 应用新增 / 编辑 / 删除（面板内可视化维护）
- ✅ 修改密码（旧密码验证 + 新密码）

## 访问入口

| 项 | 值 |
|----|-----|
| **面板地址** | **http://192.168.1.5:8085** |
| 登录账号 | `admin` |
| 登录密码 | `123456`（可在面板「改密」修改） |
| PC/手机 | 均适配（响应式） |

## 技术栈

- 后端：FastAPI (Python 3.12) + SQLite + JWT
- 前端：Vue 3 + Element Plus + ECharts（CDN 引入，响应式）
- 部署：systemd 服务 + 独立 venv，端口 8085

## 服务器部署位置

```
/opt/app-panel/backend/app/     # Python 后端代码
/opt/app-panel/backend/static/  # 前端 index.html
/opt/app-panel-venv/            # 独立 venv (Python 3.12)
/opt/app-panel/data/            # SQLite 数据库 (data.db)
/etc/systemd/system/app-panel.service
/etc/sudoers.d/app-panel        # 权限白名单
/opt/app-panel/backend/app.log  # 面板自身日志
```

## 常用命令（服务器上）

```bash
sudo systemctl status app-panel     # 状态
sudo systemctl restart app-panel    # 重启面板
sudo systemctl stop app-panel       # 停止面板
sudo journalctl -u app-panel -f     # 看面板日志
```

## 已验证（服务器真实数据）

- 登录 admin / <CHANGE_ME> ✅
- 应用状态采集：whisper 显存 1490MB（与 nvidia-smi 实测一致）、rerank/vl-8b/embedding 均 running ✅
- 重启 rerank → `ok:true`，服务真实重启，日志见 journal ✅
- 日志接口返回真实 journal 内容 ✅
- 内网 http://192.168.1.5:8085/ → HTTP 200 ✅
- 服务 active + enabled（开机自启）✅

## 权限说明（安全）

- 面板以 ubuntu 用户运行，通过 `/etc/sudoers.d/app-panel` 白名单调用 systemctl/journalctl
- 未放开 root shell，最小权限
- 面板本身有登录鉴权，服务启停均需登录

## 待办 / 建议（P1，可后续做）

- 告警（显存/内存阈值，可选飞书推送）
- 多用户角色权限（admin/viewer）
- GPU 历史保留时长可配置
- 趋势图导出

## 已上线功能验证（服务器真实环境 2026-10-18）

- 启停/重启：重启 rerank → 服务真实重启（journal 见 Started/Stopped）✅
- 自启状态：rerank=enabled、whisper=disabled 真实采集 ✅
- 趋势采样：后台线程启动，每30s采样，sample_started=true ✅
- 改密码 / 应用增删改：本地集成测试全过，已部署 ✅
- 数据保留：15 应用清单 + 8 条操作日志未因升级丢失 ✅
