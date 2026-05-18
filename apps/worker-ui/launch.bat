@echo off
REM Worker UI one-click launcher (Windows).
REM Builds the TanStack Start app on first run and serves the built worker
REM via wrangler dev (no static index.html — this is an SSR Cloudflare
REM Worker, dist/server/index.js is the entrypoint).

setlocal enableextensions enabledelayedexpansion
cd /d "%~dp0"

REM Default to https:// when install.sh generated a TLS cert; otherwise http://.
if "%LOCALLYAI_API_BASE%"=="" (
  if exist "%~dp0..\..\tls\cert.pem" (
    set "LOCALLYAI_API_BASE=https://localhost:8000"
  ) else (
    set "LOCALLYAI_API_BASE=http://localhost:8000"
  )
)
if "%LOCALLYAI_WORKER_UI_PORT%"=="" set "LOCALLYAI_WORKER_UI_PORT=5174"

echo ==^> LocallyAI Workspace launcher
echo     backend: %LOCALLYAI_API_BASE%

REM Probe the backend and start the supervisor if it isn't responding.
REM install.sh doesn't support Windows, so the launcher only knows about a
REM local venv at ..\..\\.venv\Scripts\python.exe with supervisor.py.
curl -skf -o NUL --max-time 2 "%LOCALLYAI_API_BASE%/healthz" >NUL 2>&1
if not errorlevel 1 goto backend_ok
echo ==^> backend not responding at %LOCALLYAI_API_BASE% — bringing it up
if exist "%~dp0..\..\.venv\Scripts\python.exe" (
  if exist "%~dp0..\..\supervisor.py" (
    if not exist "%~dp0..\..\logs" mkdir "%~dp0..\..\logs"
    start "" /B "%~dp0..\..\.venv\Scripts\python.exe" "%~dp0..\..\supervisor.py" >>"%~dp0..\..\logs\launchd.log" 2>>"%~dp0..\..\logs\launchd_error.log"
  ) else ( echo ERROR: %~dp0..\..\supervisor.py not found. Run install.sh first. & pause & exit /b 1 )
) else ( echo ERROR: %~dp0..\..\.venv\Scripts\python.exe not found. Run install.sh first. & pause & exit /b 1 )
echo ==^> waiting for %LOCALLYAI_API_BASE%/healthz ^(up to 120s; first start loads models^)
for /L %%i in (1,1,120) do (
  curl -skf -o NUL --max-time 2 "%LOCALLYAI_API_BASE%/healthz" >NUL 2>&1
  if not errorlevel 1 goto backend_ok
  ping -n 2 127.0.0.1 >NUL
)
echo WARN: backend did not respond within 120s. The UI may show 'Could not reach'.
:backend_ok

if not exist .env.local copy /Y .env.example .env.local >NUL

REM Pin VITE_API_BASE_URL in .env.local without bash tooling.
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
  echo ERROR: bun or npm is required to build the worker UI.
  echo Install bun ^(https://bun.sh^) or Node.js 20+.
  pause
  exit /b 1
)

if not exist node_modules (
  echo ==^> installing dependencies ^(%PM%^)
  call %PM% install || ( echo dependency install failed & pause & exit /b 1 )
)

if not exist dist\server\index.js (
  echo ==^> building production bundle
  call %PM% run build || ( echo build failed & pause & exit /b 1 )
)
if not exist dist\server\wrangler.json (
  echo ==^> building production bundle
  call %PM% run build || ( echo build failed & pause & exit /b 1 )
)

REM Prefer the locally-installed wrangler so the version matches the lockfile.
set "WRANGLER=node_modules\.bin\wrangler.cmd"
if not exist "%WRANGLER%" (
  where wrangler >NUL 2>&1 && set "WRANGLER=wrangler"
)
if not exist "%WRANGLER%" (
  if "%PM%"=="bun" ( set "WRANGLER=bunx wrangler" ) else ( set "WRANGLER=npx --yes wrangler" )
)

echo ==^> launching workspace on http://localhost:%LOCALLYAI_WORKER_UI_PORT%
start "" "http://localhost:%LOCALLYAI_WORKER_UI_PORT%"
%WRANGLER% dev --config dist\server\wrangler.json --ip 127.0.0.1 --port %LOCALLYAI_WORKER_UI_PORT%
endlocal
