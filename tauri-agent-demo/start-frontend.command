#!/bin/bash

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export TAURI_AGENT_DATA_DIR="$ROOT/.tauri-agent-data"
export TAURI_AGENT_EXTERNAL_BACKEND=1
export VITE_API_BASE_URL="http://127.0.0.1:8000"
cd "$ROOT" || exit 1

clear
echo "========================================"
echo "   Tauri Desktop App (Frontend)"
echo "========================================"
echo ""
echo "Starting Tauri desktop application..."
echo "This will start both Vite and open the desktop window"
echo "Press Ctrl+C to stop the app"
echo ""
echo "Using backend: $VITE_API_BASE_URL"
echo "Runtime data dir: $TAURI_AGENT_DATA_DIR"
echo ""
echo "========================================"
echo ""

npm run tauri dev -- --config "$ROOT/src-tauri/tauri.conf.dev.json"
