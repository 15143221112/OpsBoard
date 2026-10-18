# 部署说明

XH Panel 提供两种部署方式，按需选择。

## 方式一：服务器一键部署脚本（推荐）

`deploy.sh` 幂等可重跑，自动完成：更新代码、创建 venv、安装 sudoers 白名单、装 systemd 服务、启动并健康检查。

```bash
# 默认端口 8085，可用环境变量覆盖
PORT=1234 ./deploy/deploy.sh
```

可覆盖的环境变量：`SRC_DIR`、`PANEL_DIR`、`VENV_DIR`、`PORT`。

## 方式二：systemd 手工部署

编辑 `app-panel.service`（注意用你的路径/端口/环境变量），放置到 `/etc/systemd/system/`：

```bash
sudo cp deploy/app-panel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable app-panel
sudo systemctl start app-panel
```

> 部署文档（MinIO）与 JWT 密钥等凭据，务必在 service 的 `Environment=` 里注入真实值，切勿写进代码。

## 最小权限说明

`deploy/sudoers-app-panel` 只放行 `systemctl` 等启停服务所必需的命令，避免面板进程获得过高权限。安装后可用 `visudo -c` 校验语法。

## 验证

```bash
curl http://127.0.0.1:8085/api/health   # 期望 200
```

API 文档：`http://<host>:<port>/docs`
