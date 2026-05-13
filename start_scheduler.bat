@echo off
title IHSG Scheduler
cd /d "C:\Users\NITRO\Downloads\ihsg_system\ihsg_system"

echo ============================================
echo   IHSG Trading System — Scheduler
echo ============================================

:LOOP
echo [%date% %time%] Starting scheduler...
"C:\Users\NITRO\AppData\Local\Programs\Python\Python314\python.exe" scheduler.py

echo [%date% %time%] Scheduler berhenti. Restart dalam 10 detik...
timeout /t 10 /nobreak >nul
goto LOOP
