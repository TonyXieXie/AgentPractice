#!/bin/bash

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export TAURI_AGENT_DATA_DIR="$ROOT/.tauri-agent-data"
cd "$ROOT" || exit 1

clear
echo "========================================"
echo "   LLM Chat App - Full Stack Launcher"
echo "========================================"
echo ""
echo "Starting both Backend (dev) and Frontend (dev)..."
echo ""

if command -v osascript &> /dev/null; then
    echo "[1/2] Starting Backend Server (dev, auto-reload)..."
    osascript -e 'tell application "Terminal" to do script "cd \"'"$ROOT"'\" && ./start-backend.command --reload"'
    echo "    Backend will run on http://127.0.0.1:8000"
    echo ""

    sleep 3

    echo "[2/2] Starting Tauri Desktop App (dev)..."
    osascript -e 'tell application "Terminal" to do script "cd \"'"$ROOT"'\" && ./start-frontend.command"'
    echo "    Desktop window will open automatically"
    echo ""
else
    echo "[1/2] Starting Backend Server (dev, auto-reload)..."
    "$ROOT/start-backend.command" --reload &
    BACKEND_PID=$!
    echo "    Backend PID: $BACKEND_PID"
    echo "    Backend will run on http://127.0.0.1:8000"
    echo ""

    sleep 3

    echo "[2/2] Starting Tauri Desktop App (dev)..."
    "$ROOT/start-frontend.command"
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
