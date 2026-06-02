@echo off
REM Arteq Hospital Voice Agent - one-command launcher (Windows).
REM Usage:  start.bat               (web only)
REM         start.bat --with-agent  (run Arya too, full end-to-end)
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py run.py %*
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python run.py %*
  ) else (
    echo Python 3.10+ is required but was not found on PATH.
    exit /b 1
  )
)
