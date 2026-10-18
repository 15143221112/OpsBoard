#!/usr/bin/env bash
# app-panel 服务器部署脚本（幂等可重跑）
set -e

SRC_DIR="${SRC_DIR:-/home/ubuntu/app-panel-tmp}"   # 上传的临时目录
PANEL_DIR="${PANEL_DIR:-/opt/app-panel}"
VENV_DIR="${VENV_DIR:-/opt/app-panel-venv}"
PORT="${PORT:-8085}"

echo "==> [0/6] 更新 backend（保留 data 目录与 SQLite 数据）"
sudo mkdir -p "$PANEL_DIR/backend" "$PANEL_DIR/data"
sudo cp -r "$SRC_DIR/backend/app" "$SRC_DIR/backend/static" "$SRC_DIR/backend/requirements.txt" "$PANEL_DIR/backend/"
sudo chown -R ubuntu:ubuntu "$PANEL_DIR"
echo "拷贝完成"

echo "==> [1/6] 创建/复用 venv"
if [ ! -d "$VENV_DIR" ]; then
  python3.12 -m venv "$VENV_DIR" 2>/dev/null || python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -r "$PANEL_DIR/backend/requirements.txt"
echo "venv ok: $($VENV_DIR/bin/python --version)"

echo "==> [2/6] 安装 sudoers 白名单"
sudo install -o root -g root -m 440 "$SRC_DIR/deploy/sudoers-app-panel" /etc/sudoers.d/app-panel
sudo chmod 440 /etc/sudoers.d/app-panel
# 校验语法，出错则回滚
if ! sudo visudo -c -f /etc/sudoers.d/app-panel 2>/dev/null; then
  echo "sudoers 语法错误，回滚删除"
  sudo rm -f /etc/sudoers.d/app-panel
  exit 1
fi
echo "sudoers ok"

echo "==> [3/6] 安装 systemd 服务"
sudo cp "$SRC_DIR/deploy/app-panel.service" /etc/systemd/system/app-panel.service
sudo systemctl daemon-reload
sudo systemctl enable app-panel
echo "systemd ok"

echo "==> [4/6] 启动服务"
sudo systemctl restart app-panel
sleep 4

echo "==> [5/6] 健康检查"
CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/health" 2>/dev/null || echo 000)
echo "health http_code=$CODE"
if [ "$CODE" != "200" ]; then
  echo "服务未正常启动，查看日志："
  sudo journalctl -u app-panel --no-pager -n 30 || true
  exit 1
fi

echo "==> [6/6] 清理临时目录"
sudo rm -rf "$SRC_DIR"
echo "清理完成"

echo ""
echo "======================================"
echo "✅ app-panel 部署完成"
echo "   面板地址: http://<SERVER_IP>:$PORT"
echo "   登录账号: admin（初始密码见环境变量 APP_PANEL_ADMIN_PASSWORD，部署后请立即修改）"
echo "======================================"
