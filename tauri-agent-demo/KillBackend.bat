@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Kill Backend (Port 8000)
echo ========================================
echo   Kill Backend on Port 8000
echo ========================================
echo.

set "PIDS="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":8000"') do (
  if "%%a" neq "" (
    set "PIDS=!PIDS! %%a"
  )
)

if not defined PIDS (
  echo No process found on port 8000.
  echo.
  pause
  exit /b 0
)

echo Found process(es) on port 8000: !PIDS!
for %%p in (!PIDS!) do (
  echo Killing PID %%p ...
  taskkill /PID %%p /F >nul 2>&1
)

echo.
echo Done.
pause
