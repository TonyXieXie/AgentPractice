@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set PYTHONUTF8=1
title tauri-agent-next Backend
cd /d "%~dp0python-backend"
set "TAURI_AGENT_NEXT_DATA_DIR=%~dp0.tauri-agent-next-data"
cls
echo ========================================
echo   tauri-agent-next backend
echo ========================================
echo.
echo Runtime data dir: %TAURI_AGENT_NEXT_DATA_DIR%
echo Starting FastAPI server on port 8000...
echo Press Ctrl+C to stop the server
echo.
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
