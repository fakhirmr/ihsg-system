$TaskName   = "IHSG_Scheduler"
$ScriptPath = "C:\Users\NITRO\Downloads\ihsg_system\start_scheduler.bat"
$LogPath    = "C:\Users\NITRO\Downloads\ihsg_system\scheduler.log"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Task lama dihapus."
}

$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument ('/c "' + $ScriptPath + '" >> "' + $LogPath + '" 2>&1')
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -RunOnlyIfNetworkAvailable
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "IHSG Trading System auto-start"

Write-Host ""
Write-Host "Task berhasil didaftarkan: $TaskName"
Write-Host "Scheduler otomatis berjalan saat login Windows."
Write-Host "Log: $LogPath"
