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
if exist "%NODE_HOME%\node.exe" (
  set "PATH=%NODE_HOME%;%PATH%"
) else (
  set "NODE_EXE="
  for /f "delims=" %%I in ('where node 2^>nul') do (
    set "NODE_EXE=%%I"
    goto node_found
  )
  :node_found
  if not defined NODE_EXE (
    echo [Error] Node.js not found at %NODE_HOME% and not found in PATH
    echo Please install Node.js v20.19+ and retry.
    pause
    exit /b 1
  )
  for %%D in ("%NODE_EXE%") do set "NODE_HOME=%%~dpD"
  set "PATH=%NODE_HOME%;%PATH%"
)
set "CARGO_BIN=%USERPROFILE%\.cargo\bin"
if exist "%CARGO_BIN%\cargo.exe" (
  set "PATH=%CARGO_BIN%;%PATH%"
)
set "ESBUILD_BINARY_PATH=%~dp0node_modules\@esbuild\win32-x64\esbuild.exe"
npm run tauri dev
pause
