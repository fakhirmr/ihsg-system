@echo off
REM ============================================================
REM  Pasang pemantau mentor sebagai tugas terjadwal Windows.
REM  Cukup dijalankan SEKALI. Setelah ini tidak ada jendela yang
REM  perlu dibuka — pemantau berjalan sendiri di latar.
REM
REM  Jadwal : tiap 15 menit, 09:00-16:00, Senin-Jumat
REM  Yang dijalankan HANYA job mentor. Technical, sentiment, macro,
REM  dan supervisor sengaja TIDAK ikut — semuanya sudah berjalan di
REM  GitHub Actions, dan menjalankannya lagi di sini akan membuat
REM  setiap alert Telegram terkirim dua kali.
REM
REM  Mencopot:  copot-pantau.bat
REM ============================================================

cd /d "%~dp0"
set "TASK=IHSG Pantau Mentor"
set "VBS=%~dp0pantau-mentor.vbs"

if not exist "%VBS%" (
  echo   pantau-mentor.vbs tidak ditemukan. Batal.
  pause
  exit /b 1
)

echo.
echo   Memasang tugas "%TASK%"...
echo.

schtasks /create ^
  /tn "%TASK%" ^
  /tr "wscript.exe \"%VBS%\"" ^
  /sc weekly ^
  /d MON,TUE,WED,THU,FRI ^
  /st 09:00 ^
  /ri 15 ^
  /du 07:00 ^
  /f

if errorlevel 1 (
  echo.
  echo   Gagal memasang. Coba jalankan berkas ini sebagai Administrator.
  pause
  exit /b 1
)

echo.
echo   Terpasang. Pemantau akan berjalan sendiri tiap 15 menit
echo   pada jam pasar, tanpa jendela apa pun.
echo.
echo   Cek isi tugas   : schtasks /query /tn "%TASK%" /v /fo LIST
echo   Coba jalan sekarang: schtasks /run /tn "%TASK%"
echo   Copot           : copot-pantau.bat
echo.
pause
