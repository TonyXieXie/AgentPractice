@echo off
setlocal EnableExtensions
chcp 65001 >nul
title tauri-agent-next Desktop Launcher

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev-all.ps1"
exit /b %errorlevel%
