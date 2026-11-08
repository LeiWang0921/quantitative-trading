@echo off
rem Called by Windows Task Scheduler "QuantTrading-DailyRun" (14:45 and 20:00, Mon-Fri).
rem Everything (log file, failure popup) is done in tools\daily_run.py -- see the comments there.
rem Keep this file English-only with CRLF line endings: cmd misreads Chinese text in .bat files.
rem Manual test without placing orders:  tools\daily_run.bat --no-trade
cd /d "%~dp0.."
".venv\Scripts\python.exe" tools\daily_run.py %*
