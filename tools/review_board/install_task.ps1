# CUI // SP-CTI
# Install ICDEV Review Board as a Windows Scheduled Task
# Run as Administrator: powershell -ExecutionPolicy Bypass -File tools\review_board\install_task.ps1

$TaskName = "ICDEV Review Board"
$BatPath = "C:\Users\schuo\Downloads\ICDev\tools\review_board\run_scheduled.bat"
$WorkDir = "C:\Users\schuo\Downloads\ICDev"

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Create action
$Action = New-ScheduledTaskAction -Execute $BatPath -WorkingDirectory $WorkDir

# Trigger: every 30 minutes, repeat for 365 days (re-register annually)
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 365)

# Settings: run on battery, start if missed, 10 min max, skip if already running
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew

# Register the task
$Desc = "ICDEV Engineering Review Board - runs 7 engineering personas (SRE, QA, Security, Perf, UX, Docs, Product) every 30 minutes"
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description $Desc

Write-Host ""
Write-Host "Scheduled task created successfully." -ForegroundColor Green
Write-Host "  Task:     $TaskName"
Write-Host "  Schedule: Every 30 minutes"
Write-Host "  Script:   $BatPath"
Write-Host "  Log:      $WorkDir\.tmp\review_board\scheduled_runs.log"
Write-Host ""
Write-Host "Verify:  schtasks /query /tn ""ICDEV Review Board"" /v /fo list"
Write-Host "Delete:  schtasks /delete /tn ""ICDEV Review Board"" /f"
Write-Host "Run now: schtasks /run /tn ""ICDEV Review Board"""
