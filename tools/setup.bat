@echo off
rem First-time setup on a new PC: create .venv and install requirements.txt.
rem Needs Python 3.11+ from https://www.python.org/downloads/ (tick "Add python.exe to PATH").
rem In mainland China run "tools\setup.bat cn" to download packages from the Tsinghua mirror.
rem Keep this file English-only with CRLF line endings: cmd misreads Chinese text in .bat files.
cd /d "%~dp0.."
if /i "%~1"=="cn" set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"

set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 or newer was not found.
  echo Download it from https://www.python.org/downloads/ and tick "Add python.exe to PATH" during install.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv ...
  %PY% -m venv .venv || goto fail
)
echo Installing packages from requirements.txt ...
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto fail
echo Creating desktop and Start menu shortcuts ...
".venv\Scripts\python.exe" tools\make_shortcut.py
echo.
echo Done. Double-click the new desktop icon to open the monitoring window - its Status tab lists anything still missing.
pause
exit /b 0

:fail
echo Setup failed, see the messages above.
pause
exit /b 1
