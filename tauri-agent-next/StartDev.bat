@echo off
setlocal EnableExtensions
chcp 65001 >nul
title tauri-agent-next Dev Launcher

set "PS_ARGS="

:parse_args
if "%~1"=="" goto run_launcher
if /i "%~1"=="--no-browser" (
  set "PS_ARGS=%PS_ARGS% -NoBrowser"
  shift
  goto parse_args
)

echo [error] Unknown argument: %~1
echo Usage: StartDev.bat [--no-browser]
exit /b 1

:run_launcher
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-dev.ps1"%PS_ARGS%
exit /b %errorlevel%
