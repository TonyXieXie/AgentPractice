#!/bin/bash

# ========================================
#   LLM Chat App - Full Stack Launcher
# ========================================

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1

clear
echo "========================================"
echo "   LLM Chat App - Full Stack Launcher"
echo "========================================"
echo ""
echo "Starting both Backend (dev) and Frontend (dev)..."
echo ""

# 检查是否安装了 tmux 或使用后台进程
if command -v osascript &> /dev/null; then
    # 使用 macOS 的 osascript 在新终端窗口中启动
    
    echo "[1/2] Starting Backend Server (dev, auto-reload)..."
    osascript -e 'tell application "Terminal" to do script "cd \"'"$ROOT"'\" && ./start-backend.command --reload"'
    echo "    Backend will run on http://127.0.0.1:8000"
    echo ""
    
    sleep 3
    
    echo "[2/2] Starting Tauri Desktop App (dev)..."
    osascript -e 'tell application "Terminal" to do script "cd \"'"$ROOT"'\" && TAURI_AGENT_EXTERNAL_BACKEND=1 ./start-frontend.command"'
    echo "    Desktop window will open automatically"
    echo ""
    
else
    # 备用方案：使用后台进程
    echo "[1/2] Starting Backend Server (dev, auto-reload)..."
    "$ROOT/start-backend.command" --reload &
    BACKEND_PID=$!
    echo "    Backend PID: $BACKEND_PID"
    echo "    Backend will run on http://127.0.0.1:8000"
    echo ""
    
    sleep 3
    
    echo "[2/2] Starting Tauri Desktop App (dev)..."
    TAURI_AGENT_EXTERNAL_BACKEND=1 "$ROOT/start-frontend.command"
fi

echo ""
echo "========================================"
echo ""
echo "Both services started!"
echo ""
echo "- Backend: http://127.0.0.1:8000"
echo "- Frontend: Tauri Desktop Window"
echo ""
echo "Close the terminal windows to stop the services."
echo ""
echo "========================================"
