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
start "Backend Server" "%~dp0StartBackend.bat"
echo     Backend will select a free port (default 8000)
echo.

set "PORT_FILE=%~dp0backend_port.txt"
set "BACKEND_PORT="
set "PORT_WAIT_MAX=20"
set /a PORT_WAIT_COUNT=0
:WAIT_FOR_PORT_FILE
if exist "%PORT_FILE%" (
  for /f "usebackq delims=" %%P in ("%PORT_FILE%") do set "BACKEND_PORT=%%P"
)
if defined BACKEND_PORT (
  set /a PORT_TEST=%BACKEND_PORT% >nul 2>&1
  if errorlevel 1 set "BACKEND_PORT="
)
if not defined BACKEND_PORT (
  set /a PORT_WAIT_COUNT+=1
  if !PORT_WAIT_COUNT! GEQ %PORT_WAIT_MAX% (
    set "BACKEND_PORT=8000"
    goto PORT_READY
  )
  timeout /t 1 /nobreak >nul
  goto WAIT_FOR_PORT_FILE
)
:PORT_READY

echo [Info] Waiting for backend readiness...
set "HEALTH_WAIT_MAX=30"
set /a HEALTH_WAIT_COUNT=0
:WAIT_FOR_BACKEND
set "HTTP_STATUS=0"
for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 -Uri 'http://127.0.0.1:%BACKEND_PORT%/').StatusCode } catch { 0 }"`) do set "HTTP_STATUS=%%S"
if "%HTTP_STATUS%"=="200" goto BACKEND_READY
set /a HEALTH_WAIT_COUNT+=1
if !HEALTH_WAIT_COUNT! GEQ %HEALTH_WAIT_MAX% goto BACKEND_READY
timeout /t 1 /nobreak >nul
goto WAIT_FOR_BACKEND
:BACKEND_READY

echo [2/2] Starting Tauri Desktop App...
start "Tauri Desktop App" "%~dp0StartFrontend.bat"
echo     Desktop window will open automatically

echo.
echo ========================================
echo.
echo Both services started in separate windows!
echo.
echo - Backend: http://127.0.0.1:%BACKEND_PORT%
echo - Frontend: Tauri Desktop Window
echo.
echo Close those windows to stop the services.
echo.
echo ========================================
pause
