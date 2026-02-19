#!/bin/bash

# ========================================
#   Backend Server (FastAPI)
# ========================================

clear
echo "========================================"
echo "   Backend Server (FastAPI)"
echo "========================================"
echo ""
echo "Starting FastAPI server on port 8000..."
echo "Press Ctrl+C to stop the server"
echo ""
echo "========================================"
echo ""

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export TAURI_AGENT_DB_PATH="$ROOT/python-backend/chat_app.db"
cd "$ROOT/python-backend" || exit 1
# Log to terminal (no redirection)

# Dev mode flag
RELOAD=""
for arg in "$@"; do
    if [ "$arg" = "--reload" ] || [ "$arg" = "--dev" ]; then
        RELOAD="--reload"
    fi
done

# 激活虚拟环境
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "错误: Python 虚拟环境不存在"
    echo "请先运行 ./setup-mac.sh 安装环境"
    exit 1
fi

# 启动服务器
if [ -n "$RELOAD" ]; then
    python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000 --no-use-colors
else
    python -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-use-colors
fi
