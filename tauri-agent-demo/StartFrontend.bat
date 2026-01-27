@echo off
title Frontend - Tauri Desktop App
cd /d "%~dp0"
cls
echo ========================================
echo   Tauri Desktop App (Frontend)
echo ========================================
echo.
echo Starting Tauri desktop application...
echo This will start both Vite and open the desktop window
echo Press Ctrl+C to stop the app
echo.
echo ========================================
echo.
npm run tauri dev
pause
