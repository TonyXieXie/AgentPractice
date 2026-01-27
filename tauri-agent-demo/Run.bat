@echo off
title LLM Chat App - Full Stack
cls
echo ========================================
echo   LLM Chat App - Full Stack Launcher
echo ========================================
echo.
echo Starting both Backend and Frontend...
echo.
echo [1/2] Starting Backend Server...
start "Backend Server" cmd /k "cd /d "%~dp0python-backend" && python main.py"
echo     Backend will run on http://127.0.0.1:8000
echo.
timeout /t 3 /nobreak >nul
echo [2/2] Starting Tauri Desktop App...
start "Tauri Desktop App" cmd /k "cd /d "%~dp0" && npm run tauri dev"
echo     Desktop window will open automatically
echo.
echo ========================================
echo.
echo Both services started in separate windows!
echo.
echo - Backend: http://127.0.0.1:8000
echo - Frontend: Tauri Desktop Window
echo.
echo Close those windows to stop the services.
echo.
echo ========================================
pause