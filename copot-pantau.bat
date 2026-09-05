@echo off
REM Mencopot pemantau mentor dari Task Scheduler.
schtasks /delete /tn "IHSG Pantau Mentor" /f
if errorlevel 1 (
  echo   Tugas tidak ditemukan atau gagal dihapus.
) else (
  echo   Pemantau dicopot. Tidak ada lagi yang berjalan di latar.
)
pause
