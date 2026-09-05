' Menjalankan pemantau rencana mentor TANPA jendela terlihat.
' Dipanggil Task Scheduler tiap 15 menit di jam pasar.
' Job-nya sendiri yang memutuskan berhenti kalau di luar jam bursa,
' akhir pekan, atau jurnal belum dimasukkan.
Dim shell, fso, base, py
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
py = base & "\venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then py = "pythonw.exe"
shell.CurrentDirectory = base
' 0 = jendela disembunyikan, False = jangan tunggu selesai
shell.Run """" & py & """ run_job.py --job mentor", 0, False
