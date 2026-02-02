@echo off
title LLM Chat App - Restart
cls
echo ========================================
echo   LLM Chat App - Restart Script
echo ========================================
echo.
echo [Step 1/4] Stopping existing processes...
echo.

REM 方法1: 关闭所有运行python main.py的进程及其父CMD
echo Stopping Python Backend processes...
for /f "tokens=2" %%a in ('wmic process where "name='python.exe' and commandline like '%%main.py%%'" get ProcessId 2^>nul ^| findstr /r "[0-9]"') do (
    echo     Killing Python PID: %%a
    taskkill /F /PID %%a >nul 2>&1
    REM 同时关闭父进程（CMD窗口）
    for /f "tokens=2" %%b in ('wmic process where "ProcessId=%%a" get ParentProcessId 2^>nul ^| findstr /r "[0-9]"') do (
        echo     Killing parent CMD PID: %%b
        taskkill /F /PID %%b >nul 2>&1
    )
)

REM 方法2: 关闭占用8000端口的进程
echo Stopping processes on port 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do (
    echo     Killing process on port 8000: %%a
    taskkill /F /PID %%a >nul 2>&1
)

echo.
echo Stopping Node/NPM/Tauri processes...

REM 关闭所有npm相关进程
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq npm.exe" 2^>nul ^| findstr "npm.exe"') do (
    echo     Killing npm PID: %%a
    taskkill /F /PID %%a >nul 2>&1
    REM 关闭父CMD
    for /f "tokens=2" %%b in ('wmic process where "ProcessId=%%a" get ParentProcessId 2^>nul ^| findstr /r "[0-9]"') do (
        taskkill /F /PID %%b >nul 2>&1
    )
)

REM 关闭所有node进程
taskkill /F /IM node.exe >nul 2>&1

REM 关闭cargo和rust进程
taskkill /F /IM cargo.exe >nul 2>&1
taskkill /F /IM rustc.exe >nul 2>&1

REM 关闭Tauri进程
taskkill /F /IM tauri-agent-demo.exe >nul 2>&1

echo     All processes stopped
echo.

echo [Step 2/4] Waiting for cleanup...
timeout /t 3 /nobreak >nul

echo.
echo [Step 3/4] Starting Backend Server...
start "Backend Server" cmd /k "cd /d "%~dp0python-backend" && python main.py"
echo     Backend will run on http://127.0.0.1:8000
echo.

timeout /t 4 /nobreak >nul

echo [Step 4/4] Starting Tauri Desktop App...
start "Tauri Desktop App" cmd /k "cd /d "%~dp0" && npm run tauri dev"
echo     Desktop window will open automatically
echo.

echo ========================================
echo.
echo ✅ Services restarted successfully!
echo.
echo - Backend: http://127.0.0.1:8000
echo - Frontend: Tauri Desktop Window
echo.
echo Close those windows to stop the services.
echo.
echo ========================================
pause
