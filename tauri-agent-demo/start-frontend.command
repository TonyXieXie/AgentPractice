#!/bin/bash

# ========================================
#   Tauri Desktop App (Frontend)
# ========================================

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export TAURI_AGENT_DB_PATH="$ROOT/python-backend/chat_app.db"
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
echo "========================================"
echo ""

npm run tauri dev -- --config "$ROOT/src-tauri/tauri.conf.dev.json"
