@echo off
chcp 65001 >nul
set PYTHONUTF8=1
title Backend Server - FastAPI
cd /d "%~dp0python-backend"
cls
echo ========================================
echo   Backend Server (FastAPI)
echo ========================================
echo.
echo Starting FastAPI server on port 8000 (auto-reload)...
echo Press Ctrl+C to stop the server
echo.
echo ========================================
echo.
set "PY=%~dp0python-backend\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [Setup] venv not found. Creating...
  set "SYS_PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  if not exist "%SYS_PY%" (
    echo [Error] Python not found. Install Python 3.12 and retry.
    pause
    exit /b 1
  )
  "%SYS_PY%" -m venv "%~dp0python-backend\venv"
  "%~dp0python-backend\venv\Scripts\python.exe" -m pip install -r "%~dp0python-backend\requirements.txt"
)
"%PY%" -m uvicorn main:app --reload --port 8000 --no-use-colors
pause
