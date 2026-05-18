@echo off
REM ============================================================================
REM  LocallyAI one-click stopper (Windows)
REM
REM  Reads the PID file at logs\launcher\locallyai.pids and terminates each
REM  process tree. Safe to run when nothing is running.
REM ============================================================================
setlocal EnableExtensions

set "SELF_DIR=%~dp0"
set "REPO_DIR=%SELF_DIR%.."
pushd "%REPO_DIR%" 2>nul
set "REPO_DIR=%CD%"
popd

set "PID_FILE=%REPO_DIR%\logs\launcher\locallyai.pids"
if not exist "%PID_FILE%" (
  exit /b 0
)

for /f "tokens=1,2" %%A in (%PID_FILE%) do (
  REM /F = forceful, /T = kill child processes too (vite spawns several).
  taskkill /PID %%B /T /F >nul 2>&1
)

del "%PID_FILE%" >nul 2>&1
exit /b 0
