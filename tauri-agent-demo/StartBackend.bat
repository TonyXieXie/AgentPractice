@echo off
setlocal EnableExtensions EnableDelayedExpansion
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
  call :FindPython312
  if not defined SYS_PY (
    echo [Error] Python 3.12 not found. Install Python 3.12 and retry.
    pause
    exit /b 1
  )
  echo [Setup] Using Python: !SYS_PY!
  "!SYS_PY!" -m venv "%~dp0python-backend\venv"
  "%~dp0python-backend\venv\Scripts\python.exe" -m pip install -r "%~dp0python-backend\requirements.txt"
)
"%PY%" -m uvicorn main:app --reload --port 8000 --no-use-colors
pause
goto :eof

:FindPython312
set "SYS_PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "SYS_PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined SYS_PY if exist "%ProgramFiles%\Python312\python.exe" set "SYS_PY=%ProgramFiles%\Python312\python.exe"
if not defined SYS_PY if exist "%ProgramFiles(x86)%\Python312\python.exe" set "SYS_PY=%ProgramFiles(x86)%\Python312\python.exe"
if not defined SYS_PY for /f "tokens=3,*" %%A in ('reg query "HKCU\Software\Python\PythonCore\3.12\InstallPath" /ve 2^>nul') do if exist "%%A%%Bpython.exe" set "SYS_PY=%%A%%Bpython.exe"
if not defined SYS_PY for /f "tokens=3,*" %%A in ('reg query "HKLM\Software\Python\PythonCore\3.12\InstallPath" /ve 2^>nul') do if exist "%%A%%Bpython.exe" set "SYS_PY=%%A%%Bpython.exe"
if not defined SYS_PY for /f "tokens=3,*" %%A in ('reg query "HKLM\Software\WOW6432Node\Python\PythonCore\3.12\InstallPath" /ve 2^>nul') do if exist "%%A%%Bpython.exe" set "SYS_PY=%%A%%Bpython.exe"
if not defined SYS_PY if exist "%SystemRoot%\py.exe" for /f "tokens=2,*" %%A in ('"%SystemRoot%\py.exe" -0p 2^>nul ^| findstr /i "3.12"') do set "SYS_PY=%%B"
if not defined SYS_PY if exist "%SystemRoot%\py.exe" for /f "delims=" %%P in ('"%SystemRoot%\py.exe" -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "SYS_PY=%%P"
exit /b 0
