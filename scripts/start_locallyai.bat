@echo off
REM ============================================================================
REM  LocallyAI one-click launcher (Windows)
REM
REM  Mirror of scripts/start_locallyai.sh. Starts the API server plus
REM  whichever UI dev server(s) match the selection argument:
REM
REM     start_locallyai.bat            -> API + both UIs (legacy default)
REM     start_locallyai.bat worker     -> API + worker-ui, opens worker tab
REM     start_locallyai.bat manager    -> API + manager-ui, opens manager tab
REM
REM  Logs land in logs\launcher\.
REM ============================================================================
setlocal EnableExtensions EnableDelayedExpansion

REM --- Selection -------------------------------------------------------------
set "SELECTION=%~1"
if "%SELECTION%"==""        set "SELECTION=both"
if /i not "%SELECTION%"=="worker" if /i not "%SELECTION%"=="manager" if /i not "%SELECTION%"=="both" (
  msg %username% "LocallyAI: usage: start_locallyai.bat [worker^|manager^|both]"
  exit /b 2
)

REM --- Resolve repo root (the directory containing api.py) -------------------
set "SELF_DIR=%~dp0"
set "REPO_DIR=%SELF_DIR%.."
pushd "%REPO_DIR%" 2>nul
set "REPO_DIR=%CD%"
popd

if not exist "%REPO_DIR%\api.py" (
  msg %username% "LocallyAI: cannot find api.py in %REPO_DIR%."
  exit /b 1
)

REM --- Pre-flight: venv + node_modules ---------------------------------------
if not exist "%REPO_DIR%\.venv\Scripts\python.exe" (
  msg %username% "LocallyAI: Python venv missing. Run install.ps1 first."
  exit /b 1
)
if /i "%SELECTION%"=="worker"  goto check_worker_only
if /i "%SELECTION%"=="manager" goto check_manager_only
REM "both"
if not exist "%REPO_DIR%\apps\worker-ui\node_modules" (
  msg %username% "LocallyAI: worker-ui dependencies missing. Run 'npm install' in apps\worker-ui."
  exit /b 1
)
if not exist "%REPO_DIR%\apps\manager-ui\node_modules" (
  msg %username% "LocallyAI: manager-ui dependencies missing. Run 'npm install' in apps\manager-ui."
  exit /b 1
)
goto post_check

:check_worker_only
if not exist "%REPO_DIR%\apps\worker-ui\node_modules" (
  msg %username% "LocallyAI: worker-ui dependencies missing. Run 'npm install' in apps\worker-ui."
  exit /b 1
)
goto post_check

:check_manager_only
if not exist "%REPO_DIR%\apps\manager-ui\node_modules" (
  msg %username% "LocallyAI: manager-ui dependencies missing. Run 'npm install' in apps\manager-ui."
  exit /b 1
)

:post_check
REM --- Paths -----------------------------------------------------------------
set "LAUNCH_DIR=%REPO_DIR%\logs\launcher"
if not exist "%LAUNCH_DIR%" mkdir "%LAUNCH_DIR%"
set "API_LOG=%LAUNCH_DIR%\api.log"
set "WORKER_LOG=%LAUNCH_DIR%\worker-ui.log"
set "MANAGER_LOG=%LAUNCH_DIR%\manager-ui.log"
set "PID_FILE=%LAUNCH_DIR%\locallyai.pids"

set "API_PORT=8000"
set "WORKER_PORT=5174"
set "MANAGER_PORT=5173"
if defined LOCALLYAI_API_PORT     set "API_PORT=%LOCALLYAI_API_PORT%"
if defined LOCALLYAI_WORKER_PORT  set "WORKER_PORT=%LOCALLYAI_WORKER_PORT%"
if defined LOCALLYAI_MANAGER_PORT set "MANAGER_PORT=%LOCALLYAI_MANAGER_PORT%"

REM Truncate PID file each launch so a hard crash doesn't leave a stale entry
REM marked as live.
type nul > "%PID_FILE%"

REM --- Start API -------------------------------------------------------------
for /f %%P in ('powershell -NoProfile -Command "$p = Start-Process -FilePath '%REPO_DIR%\.venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','api:app','--host','127.0.0.1','--port','%API_PORT%' -WorkingDirectory '%REPO_DIR%' -WindowStyle Hidden -RedirectStandardOutput '%API_LOG%' -RedirectStandardError '%API_LOG%' -PassThru; $p.Id"') do (
  echo api %%P >> "%PID_FILE%"
)

REM --- Start worker-ui (vite dev) -------------------------------------------
if /i not "%SELECTION%"=="manager" (
  for /f %%P in ('powershell -NoProfile -Command "$p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','npm run dev -- --port %WORKER_PORT% --host 127.0.0.1' -WorkingDirectory '%REPO_DIR%\apps\worker-ui' -WindowStyle Hidden -RedirectStandardOutput '%WORKER_LOG%' -RedirectStandardError '%WORKER_LOG%' -PassThru; $p.Id"') do (
    echo worker-ui %%P >> "%PID_FILE%"
  )
)

REM --- Start manager-ui (vite dev) ------------------------------------------
if /i not "%SELECTION%"=="worker" (
  for /f %%P in ('powershell -NoProfile -Command "$p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','npm run dev -- --port %MANAGER_PORT% --host 127.0.0.1' -WorkingDirectory '%REPO_DIR%\apps\manager-ui' -WindowStyle Hidden -RedirectStandardOutput '%MANAGER_LOG%' -RedirectStandardError '%MANAGER_LOG%' -PassThru; $p.Id"') do (
    echo manager-ui %%P >> "%PID_FILE%"
  )
)

REM --- Wait for ports --------------------------------------------------------
call :wait_port %API_PORT% 60
if /i not "%SELECTION%"=="manager" call :wait_port %WORKER_PORT%  90
if /i not "%SELECTION%"=="worker"  call :wait_port %MANAGER_PORT% 90

REM --- Open the requested browser tab(s) ------------------------------------
if /i not "%SELECTION%"=="manager" start "" "http://localhost:%WORKER_PORT%/"
if /i not "%SELECTION%"=="worker"  start "" "http://localhost:%MANAGER_PORT%/"

exit /b 0

:wait_port
set /a _tries=0
:wp_loop
powershell -NoProfile -Command "if ((Get-NetTCPConnection -LocalPort %1 -State Listen -ErrorAction SilentlyContinue)) { exit 0 } else { exit 1 }" >nul 2>&1
if %errorlevel%==0 exit /b 0
set /a _tries+=1
if %_tries% geq %2 exit /b 1
timeout /t 1 /nobreak >nul
goto wp_loop
