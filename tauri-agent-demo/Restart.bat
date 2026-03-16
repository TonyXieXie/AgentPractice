@echo off
title LLM Chat App - Restart
setlocal EnableExtensions EnableDelayedExpansion
cls
echo ========================================
echo   LLM Chat App - Restart Script
echo ========================================
echo.
echo [Step 1/4] Stopping existing processes...
echo.

REM 方法1: 关闭所有运行 uvicorn main:app 的 Python 进程及其父 CMD
echo Stopping Python Backend processes...
for /f "tokens=2" %%a in ('wmic process where "name='python.exe' and commandline like '%%uvicorn%%' and commandline like '%%main:app%%'" get ProcessId 2^>nul ^| findstr /r "[0-9]"') do (
    echo     Killing Python PID: %%a
    taskkill /F /PID %%a >nul 2>&1
    for /f "tokens=2" %%b in ('wmic process where "ProcessId=%%a" get ParentProcessId 2^>nul ^| findstr /r "[0-9]"') do (
        echo     Killing parent CMD PID: %%b
        taskkill /F /PID %%b >nul 2>&1
    )
)

REM 方法2: 兜底关闭当前监听 8000 端口的进程
echo Stopping processes on port 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do (
    echo     Killing process on port 8000: %%a
    taskkill /F /PID %%a >nul 2>&1
)

echo.
echo Stopping Node/NPM/Tauri processes...

REM 关闭所有 npm 相关进程
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq npm.exe" 2^>nul ^| findstr "npm.exe"') do (
    echo     Killing npm PID: %%a
    taskkill /F /PID %%a >nul 2>&1
    for /f "tokens=2" %%b in ('wmic process where "ProcessId=%%a" get ParentProcessId 2^>nul ^| findstr /r "[0-9]"') do (
        taskkill /F /PID %%b >nul 2>&1
    )
)

REM 关闭所有 node 进程
taskkill /F /IM node.exe >nul 2>&1

REM 关闭 cargo 和 rust 进程
taskkill /F /IM cargo.exe >nul 2>&1
taskkill /F /IM rustc.exe >nul 2>&1

REM 关闭 Tauri 进程
taskkill /F /IM tauri-agent-demo.exe >nul 2>&1

echo     All processes stopped
echo.

echo [Step 2/4] Waiting for cleanup...
timeout /t 3 /nobreak >nul

call :PickFreePort
if not defined TAURI_AGENT_PORT (
    echo [Warn] Failed to pick a free port dynamically. Falling back to 8000.
    set "TAURI_AGENT_PORT=8000"
)
echo [Info] Selected backend port: !TAURI_AGENT_PORT!
echo.

echo [Step 3/4] Starting Backend Server...
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

echo [Step 4/4] Starting Tauri Desktop App...
start "Tauri Desktop App" "%~dp0StartFrontend.bat" !TAURI_AGENT_PORT!
echo     Desktop window will open automatically
echo.

echo ========================================
echo.
echo Services restarted successfully!
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
