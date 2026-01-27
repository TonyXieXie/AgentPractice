@echo off
title Backend Server - FastAPI
cd /d "%~dp0python-backend"
cls
echo ========================================
echo   Backend Server (FastAPI)
echo ========================================
echo.
echo Starting FastAPI server on port 8000...
echo Press Ctrl+C to stop the server
echo.
echo ========================================
echo.
python main.py
pause
