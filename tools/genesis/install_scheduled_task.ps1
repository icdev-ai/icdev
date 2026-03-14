# Genesis v2.0 - Install Windows Scheduled Task
# Run this script as Administrator (right-click > Run as Administrator)
#
# Creates a scheduled task that starts the Genesis daemon on user logon.
# The daemon runs all 12 reflexes on their configured schedules.

$taskName = "ICDEV Genesis Daemon"
$batPath = "C:\Users\schuo\Downloads\ICDev\tools\genesis\start_daemon.bat"
$workDir = "C:\Users\schuo\Downloads\ICDev"

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing task: $taskName" -ForegroundColor Yellow
}

# Create the task
$action = New-ScheduledTaskAction -Execute $batPath -WorkingDirectory $workDir
$trigger = New-ScheduledTaskTrigger -AtLogon
$restartInterval = New-TimeSpan -Minutes 5
$execLimit = New-TimeSpan -Days 365
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval $restartInterval -ExecutionTimeLimit $execLimit
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Genesis v2.0 Autonomous Research Lab"

$q = [char]34
Write-Host ""
Write-Host "Scheduled task created successfully!" -ForegroundColor Green
Write-Host "  Name:    $taskName" -ForegroundColor Cyan
Write-Host "  Trigger: At logon" -ForegroundColor Cyan
Write-Host "  Action:  $batPath" -ForegroundColor Cyan
Write-Host "  Restarts on failure: 3 retries, 5 min interval" -ForegroundColor Cyan
Write-Host ""
Write-Host "To start it now without rebooting:" -ForegroundColor Yellow
Write-Host "  schtasks /run /tn ${q}ICDEV Genesis Daemon${q}" -ForegroundColor White
Write-Host ""
Write-Host "To check status:" -ForegroundColor Yellow
Write-Host "  schtasks /query /tn ${q}ICDEV Genesis Daemon${q} /v" -ForegroundColor White
Write-Host ""
Write-Host "To remove:" -ForegroundColor Yellow
Write-Host "  schtasks /delete /tn ${q}ICDEV Genesis Daemon${q} /f" -ForegroundColor White
