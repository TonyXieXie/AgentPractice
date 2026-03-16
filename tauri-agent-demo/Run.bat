@echo off
title LLM Chat App - Full Stack
setlocal EnableExtensions EnableDelayedExpansion
set "TAURI_AGENT_DATA_DIR=%~dp0.tauri-agent-data"
cls

echo ========================================
echo   LLM Chat App - Full Stack Launcher
echo ========================================
echo.
echo Starting both Backend and Frontend...
echo.

call :PickFreePort
if not defined TAURI_AGENT_PORT (
  echo [Warn] Failed to pick a free port dynamically. Falling back to 8000.
  set "TAURI_AGENT_PORT=8000"
)
echo [Info] Selected backend port: !TAURI_AGENT_PORT!
echo.

echo [1/2] Starting Backend Server...
start "Backend Server" "%~dp0StartBackend.bat" !TAURI_AGENT_PORT!
echo     Backend will run on http://127.0.0.1:!TAURI_AGENT_PORT!
echo.

echo [Info] Waiting for backend readiness...
set "HEALTH_WAIT_MAX=30"
set /a HEALTH_WAIT_COUNT=0
:WAIT_FOR_BACKEND
set "HTTP_STATUS=0"
for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 -Uri 'http://127.0.0.1:!TAURI_AGENT_PORT!/').StatusCode } catch { 0 }"`) do set "HTTP_STATUS=%%S"
if "%HTTP_STATUS%"=="200" goto BACKEND_READY
set /a HEALTH_WAIT_COUNT+=1
if !HEALTH_WAIT_COUNT! GEQ %HEALTH_WAIT_MAX% goto BACKEND_READY
timeout /t 1 /nobreak >nul
goto WAIT_FOR_BACKEND
:BACKEND_READY

echo [2/2] Starting Tauri Desktop App...
start "Tauri Desktop App" "%~dp0StartFrontend.bat" !TAURI_AGENT_PORT!
echo     Desktop window will open automatically

echo.
echo ========================================
echo.
echo Both services started in separate windows!
echo.
echo - Backend: http://127.0.0.1:!TAURI_AGENT_PORT!
echo - Frontend: Tauri Desktop Window
echo.
echo Close those windows to stop the services.
echo.
echo ========================================
pause
goto :eof

:PickFreePort
set "TAURI_AGENT_PORT="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0); $listener.Start(); $port = $listener.LocalEndpoint.Port; $listener.Stop(); Write-Output $port"`) do (
  set "TAURI_AGENT_PORT=%%P"
)
exit /b 0
