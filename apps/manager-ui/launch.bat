@echo off
REM Manager UI one-click launcher (Windows). Mirrors apps/worker-ui/launch.bat
REM but defaults to port 5173 and opens the administrator console.

setlocal enableextensions enabledelayedexpansion
cd /d "%~dp0"

if "%LOCALLYAI_API_BASE%"=="" set "LOCALLYAI_API_BASE=http://localhost:8000"
if "%LOCALLYAI_MANAGER_UI_PORT%"=="" set "LOCALLYAI_MANAGER_UI_PORT=5173"

echo ==^> LocallyAI Management Console launcher
echo     backend: %LOCALLYAI_API_BASE%

if not exist .env.local copy /Y .env.example .env.local >NUL

> .env.local.tmp (
  for /f "usebackq delims=" %%L in (".env.local") do (
    set "line=%%L"
    set "trimmed=!line:VITE_API_BASE_URL=!"
    if "!trimmed!"=="!line!" echo(!line!
  )
  echo VITE_API_BASE_URL=%LOCALLYAI_API_BASE%
)
move /Y .env.local.tmp .env.local >NUL

set "PM="
where bun >NUL 2>&1 && set "PM=bun"
if not defined PM where npm >NUL 2>&1 && set "PM=npm"
if not defined PM (
  echo ERROR: bun or npm is required to build the manager UI.
  echo Install bun ^(https://bun.sh^) or Node.js 20+.
  pause
  exit /b 1
)

if not exist node_modules (
  echo ==^> installing dependencies ^(%PM%^)
  call %PM% install || ( echo dependency install failed & pause & exit /b 1 )
)

if not exist dist (
  echo ==^> building production bundle
  call %PM% run build || ( echo build failed & pause & exit /b 1 )
)

set "PYTHON_BIN=python"
where py >NUL 2>&1 && set "PYTHON_BIN=py -3"

echo ==^> launching console on http://localhost:%LOCALLYAI_MANAGER_UI_PORT%
%PYTHON_BIN% "%~dp0..\serve_ui.py" "%~dp0dist" --port %LOCALLYAI_MANAGER_UI_PORT%
endlocal
