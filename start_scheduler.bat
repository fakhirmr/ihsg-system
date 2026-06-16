@echo off
title IHSG Scheduler
cd /d "%~dp0"
echo Starting IHSG Scheduler...
echo Log: %~dp0logs\scheduler_console.log
echo Tekan Ctrl+C untuk menghentikan.
echo.
venv\Scripts\python.exe scheduler.py 2>&1
pause
