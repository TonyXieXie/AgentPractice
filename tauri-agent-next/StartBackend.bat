@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set PYTHONUTF8=1
set "TAURI_AGENT_NEXT_LOG_BACKEND_LOGIC=1"
set "TAURI_AGENT_NEXT_LOG_FRONTEND_BACKEND=0"
title tauri-agent-next Backend
set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%python-backend"
set "TAURI_AGENT_NEXT_DATA_DIR=%ROOT_DIR%.tauri-agent-next-data"
if not exist "%TAURI_AGENT_NEXT_DATA_DIR%" mkdir "%TAURI_AGENT_NEXT_DATA_DIR%" >nul 2>&1

set "BACKEND_PORT=%~1"
if not defined BACKEND_PORT call :get_free_port BACKEND_PORT

cd /d "%BACKEND_DIR%"
cls
echo ========================================
echo   tauri-agent-next backend
echo ========================================
echo.
echo Runtime data dir: %TAURI_AGENT_NEXT_DATA_DIR%
echo Starting FastAPI server on port %BACKEND_PORT%...
echo Backend logic log: enabled
echo Frontend-backend log: disabled
echo Press Ctrl+C to stop the server
echo.
python -m uvicorn main:app --reload --host 127.0.0.1 --port %BACKEND_PORT%
exit /b %errorlevel%

:get_free_port
for /f %%i in ('powershell -NoProfile -Command "$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0); $listener.Start(); $port = $listener.LocalEndpoint.Port; $listener.Stop(); $port"') do set "%~1=%%i"
exit /b 0
