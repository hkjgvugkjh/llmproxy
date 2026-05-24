#!/bin/bash
# ============================================================
# LLM Proxy 后台启动脚本
# 用法: ./start.sh          # 启动
#       ./start.sh stop     # 停止
#       ./start.sh status   # 查看状态
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="${SCRIPT_DIR}/llm_proxy.pid"
SERVER_LOG="${SCRIPT_DIR}/server.log"

case "${1:-start}" in
    start)
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "LLM Proxy 已在运行 (PID: $(cat "$PIDFILE"))"
            exit 0
        fi
        echo "启动 LLM_PROXY..."
        cd "$SCRIPT_DIR"
        nohup python3 llm_proxy.py >> "$SERVER_LOG" 2>&1 &
        echo $! > "$PIDFILE"
        sleep 1
        if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "启动成功 (PID: $(cat "$PIDFILE"))"
            echo "日志: $SERVER_LOG"
        else
            echo "启动失败，请检查 $SERVER_LOG"
            rm -f "$PIDFILE"
            exit 1
        fi
        ;;
    stop)
        if [ -f "$PIDFILE" ]; then
            PID=$(cat "$PIDFILE")
            if kill -0 "$PID" 2>/dev/null; then
                kill "$PID"
                rm -f "$PIDFILE"
                echo "已停止 (PID: $PID)"
            else
                echo "进程不存在，清理 pidfile"
                rm -f "$PIDFILE"
            fi
        else
            echo "未找到 pidfile，尝试 pkill..."
            pkill -f "llm_proxy.py" 2>/dev/null && echo "已停止" || echo "未找到运行中的进程"
        fi
        ;;
    status)
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "运行中 (PID: $(cat "$PIDFILE"))"
        else
            echo "未运行"
        fi
        ;;
    restart)
        "$0" stop
        sleep 2
        "$0" start
        ;;
    *)
        echo "用法: $0 {start|stop|status|restart}"
        exit 1
        ;;
esac
