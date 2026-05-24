#!/bin/bash
# ============================================================
# LLM Proxy systemd 服务安装脚本
# 用法: sudo ./install.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_FILE="${SCRIPT_DIR}/llmproxy.service"
SERVICE_NAME="llmproxy"
SERVICE_DEST="/etc/systemd/system/${SERVICE_NAME}.service"

if [ "$(id -u)" -ne 0 ]; then
    echo "请使用 sudo 运行: sudo $0"
    exit 1
fi

USER=$(stat -c '%U' "$SCRIPT_DIR")
PYTHON=$(which python3)

echo "=== LLM Proxy systemd 服务安装 ==="
echo "工作目录: $SCRIPT_DIR"
echo "运行用户: $USER"
echo "Python:   $PYTHON"
echo ""

# 替换模板变量
sed -e "s|__USER__|$USER|g" \
    -e "s|__WORKDIR__|$SCRIPT_DIR|g" \
    -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__LOGDIR__|logs|g" \
    -e "s|__REPORTDIR__|reports|g" \
    "$SERVICE_FILE" > "$SERVICE_DEST"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

echo "✅ 服务已安装到 $SERVICE_DEST"
echo ""
echo "启动:   sudo systemctl start $SERVICE_NAME"
echo "停止:   sudo systemctl stop $SERVICE_NAME"
echo "状态:   sudo systemctl status $SERVICE_NAME"
echo "日志:   journalctl -u $SERVICE_NAME -f"
