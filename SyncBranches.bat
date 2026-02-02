@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Worktree paths (edit if your locations change)
set "MAIN_WT=E:\AI\Agent\AgentPractice\tauri-agent-demo"
set "UI_WT=E:\AI\Agent\AgentParctice_2\AgentPractice\tauri-agent-demo"
set "MAIN_BRANCH=main"
set "UI_BRANCH=UI"

if /i "%~1"=="ui-from-main" (
  set "MODE=UI_FROM_MAIN"
  goto :run
)
if /i "%~1"=="main-from-ui" (
  set "MODE=MAIN_FROM_UI"
  goto :run
)

if not "%~1"=="" (
  echo Usage: %~nx0 ui-from-main ^| main-from-ui
  exit /b 1
)

echo 1) main -- UI (merge main into UI)
echo 2) UI -- main (merge UI into main)
set /p CHOICE=Select [1-2]:
if "%CHOICE%"=="1" set "MODE=UI_FROM_MAIN"
if "%CHOICE%"=="2" set "MODE=MAIN_FROM_UI"
if not defined MODE (
  echo Invalid choice.
  exit /b 1
)

:run
call :ensure_repo "%MAIN_WT%" || goto :pause_fail
call :ensure_repo "%UI_WT%" || goto :pause_fail
call :ensure_clean "%MAIN_WT%" "MAIN" || goto :pause_fail
call :ensure_clean "%UI_WT%" "UI" || goto :pause_fail

if "%MODE%"=="UI_FROM_MAIN" (
  call :do_merge "%UI_WT%" "%UI_BRANCH%" "%MAIN_BRANCH%"
  goto :pause_exit
)

call :do_merge "%MAIN_WT%" "%MAIN_BRANCH%" "%UI_BRANCH%"
goto :pause_exit

:pause_exit
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%

:pause_fail
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%

:ensure_repo
set "WT=%~1"
git -C "%WT%" rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
  echo Not a git worktree: %WT%
  exit /b 1
)
exit /b 0

:ensure_clean
set "WT=%~1"
set "LABEL=%~2"
git -C "%WT%" status --porcelain | findstr /r "." >nul
if not errorlevel 1 (
  echo %LABEL% worktree has uncommitted changes: %WT%
  echo Commit or stash before syncing.
  exit /b 1
)
exit /b 0

:do_merge
set "WT=%~1"
set "TARGET_BRANCH=%~2"
set "SOURCE_BRANCH=%~3"

for /f %%i in ('git -C "%WT%" rev-parse HEAD') do set "BEFORE=%%i"
git -C "%WT%" switch "%TARGET_BRANCH%"
if errorlevel 1 exit /b 1

git -C "%WT%" merge "%SOURCE_BRANCH%" --no-edit
if errorlevel 1 (
  echo Merge conflict. Aborting and restoring previous state...
  git -C "%WT%" merge --abort >nul 2>&1
  git -C "%WT%" reset --hard "!BEFORE!" >nul 2>&1
  exit /b 1
)

echo Merge done.
exit /b 0
