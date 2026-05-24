#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="${SCRIPT_DIR}/llm_proxy.pid"
SERVER_LOG="${SCRIPT_DIR}/server.log"

case "${1:-start}" in
    start)
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "LLM Proxy already running (PID: $(cat "$PIDFILE"))"
            exit 0
        fi
        echo "Starting LLM Proxy..."
        cd "$SCRIPT_DIR"
        nohup python3 -m llmproxy >> "$SERVER_LOG" 2>&1 &
        echo $! > "$PIDFILE"
        sleep 1
        if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "Started (PID: $(cat "$PIDFILE"))"
        else
            echo "Failed to start, check $SERVER_LOG"
            rm -f "$PIDFILE"
            exit 1
        fi
        ;;
    stop)
        if [ -f "$PIDFILE" ]; then
            PID=$(cat "$PIDFILE")
            if kill -0 "$PID" 2>/dev/null; then kill "$PID"; echo "Stopped (PID: $PID)"
            else echo "Process not found"; fi
            rm -f "$PIDFILE"
        else
            pkill -f "llmproxy" 2>/dev/null && echo "Stopped" || echo "Not running"
        fi
        ;;
    status)
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "Running (PID: $(cat "$PIDFILE"))"
        else
            echo "Not running"
        fi
        ;;
    restart) "$0" stop; sleep 2; "$0" start ;;
    *) echo "Usage: $0 {start|stop|status|restart}"; exit 1 ;;
esac
