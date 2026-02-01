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
set "NODE_HOME=%~dp0..\.tools\node-v20.19.0-win-x64"
if not exist "%NODE_HOME%\node.exe" (
  echo [Error] Node.js not found at %NODE_HOME%
  echo Please install Node.js v20.19+ and retry.
  pause
  exit /b 1
)
set "PATH=%NODE_HOME%;%PATH%"
set "CARGO_BIN=%USERPROFILE%\.cargo\bin"
if exist "%CARGO_BIN%\cargo.exe" (
  set "PATH=%CARGO_BIN%;%PATH%"
)
npm run tauri dev
pause
