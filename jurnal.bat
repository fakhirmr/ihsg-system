@echo off
REM ============================================================
REM  Jurnal Mentor -> Papan IHSG  (klik dua kali berkas ini)
REM
REM  1. Notepad terbuka. Tempel jurnal hari ini, Simpan, lalu Tutup.
REM  2. Jurnal dibaca jadi level per emiten.
REM  3. Papan lokal dibangun ulang dengan level mentor.
REM  4. Browser terbuka di papan.
REM
REM  Jurnal TIDAK PERNAH ikut ke papan publik GitHub Pages —
REM  hanya papan lokal ini dan Telegram pribadi yang memuatnya.
REM ============================================================

cd /d "%~dp0"
setlocal

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM Tanggal hari ini (YYYY-MM-DD) dari Python supaya zona waktunya WIB
for /f %%d in ('"%PY%" -c "from datetime import datetime;from zoneinfo import ZoneInfo;print(datetime.now(ZoneInfo('Asia/Jakarta')).strftime('%%Y-%%m-%%d'))"') do set "TODAY=%%d"

set "RAW=data\journal\raw\%TODAY%.txt"
if not exist "data\journal\raw" mkdir "data\journal\raw"
if not exist "%RAW%" type nul > "%RAW%"

echo.
echo   Jurnal untuk %TODAY%
echo   Tempel jurnal di Notepad, Simpan (Ctrl+S), lalu Tutup jendelanya.
echo.
start /wait notepad "%RAW%"

for %%A in ("%RAW%") do if %%~zA LSS 50 (
  echo   Jurnal kosong. Dibatalkan.
  pause
  exit /b 1
)

echo.
echo   [1/2] Membaca jurnal...
"%PY%" ingest_journal.py "%RAW%" --date %TODAY%
if errorlevel 1 (
  echo   Gagal membaca jurnal.
  pause
  exit /b 1
)

echo.
echo   [2/2] Membangun papan lokal...
"%PY%" run_job.py --job dashboard --with-journal
if errorlevel 1 (
  echo   Gagal membangun papan.
  pause
  exit /b 1
)

REM Sajikan folder web/ lewat HTTP — fetch() tidak jalan dari file://
tasklist /fi "windowtitle eq PapanIHSG*" 2>nul | find /i "python.exe" >nul
if errorlevel 1 (
  start "PapanIHSG" /min "%PY%" -m http.server 8765 --directory web
  timeout /t 2 /nobreak >nul
)

start "" "http://127.0.0.1:8765/index.html"

echo.
echo   Selesai. Papan terbuka di http://127.0.0.1:8765/index.html
echo   (jendela server bernama PapanIHSG berjalan minimize; tutup untuk menghentikan)
echo.
pause
