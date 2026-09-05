@echo off
REM ============================================================
REM  Pemantau rencana mentor — jalankan pagi, biarkan terbuka.
REM
REM  Mengawasi level mentor tiap 15 menit sepanjang jam pasar dan
REM  mengabari Telegram saat harga masuk zona beli, menyentuh target,
REM  atau menjebol stop.
REM
REM  Ini HARUS jalan di komputer ini, bukan di GitHub Actions:
REM  jurnalnya rahasia dan sengaja tidak pernah ikut ke repo.
REM ============================================================
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo   Pemantau berjalan. Tutup jendela ini untuk berhenti.
echo.
"%PY%" scheduler.py
pause
