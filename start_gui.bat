@echo off
rem Start the monitoring window (read-only, never places orders).
rem Keep this file English-only with CRLF line endings: cmd misreads Chinese text in .bat files.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo .venv not found. Run tools\setup.bat first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" gui.py
