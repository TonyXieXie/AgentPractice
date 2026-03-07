#!/bin/bash

clear
echo "========================================"
echo "   Backend Server (FastAPI)"
echo "========================================"
echo ""

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export TAURI_AGENT_DATA_DIR="$ROOT/.tauri-agent-data"
cd "$ROOT/python-backend" || exit 1

echo "Runtime data dir: $TAURI_AGENT_DATA_DIR"
echo "Starting FastAPI server on port 8000..."
echo "Press Ctrl+C to stop the server"
echo ""
echo "========================================"
echo ""

RELOAD=""
for arg in "$@"; do
    if [ "$arg" = "--reload" ] || [ "$arg" = "--dev" ]; then
        RELOAD="--reload"
    fi
done

if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Error: Python virtual environment not found."
    echo "Run ./setup-mac.command first."
    exit 1
fi

if [ -n "$RELOAD" ]; then
    export TAURI_AGENT_DEV=1
    python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000 --no-use-colors
else
    python -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-use-colors
fi
