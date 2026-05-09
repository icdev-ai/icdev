# Genesis v2.0 - Install Windows Scheduled Task
# Run this script as Administrator (right-click PowerShell > Run as Administrator)
#
# Creates a scheduled task that:
#   1. Starts on user logon
#   2. Auto-restarts on failure (3 retries, 5 min interval)
#   3. Runs indefinitely (365-day execution limit)
#   4. Keeps running on battery
#   5. Starts Ollama + Dashboard + Genesis Daemon

$taskName = "ICDEV Genesis Daemon"
$workDir  = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$batPath  = Join-Path $workDir "tools\genesis\start_daemon.bat"

# Check admin
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal $identity
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: This script must run as Administrator." -ForegroundColor Red
    Write-Host "Right-click PowerShell > Run as Administrator, then re-run." -ForegroundColor Yellow
    exit 1
}

# Validate bat file exists
if (-not (Test-Path $batPath)) {
    Write-Host "ERROR: $batPath not found" -ForegroundColor Red
    exit 1
}

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
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval $restartInterval `
    -ExecutionTimeLimit $execLimit `
    -DontStopOnIdleEnd
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "ICDEV Genesis v2.0 — Dashboard (port 5050) + 13-Reflex Daemon + auto-restart"

$q = [char]34
Write-Host ""
Write-Host "Scheduled task created!" -ForegroundColor Green
Write-Host ""
Write-Host "  Task:        $taskName" -ForegroundColor Cyan
Write-Host "  Trigger:     At logon (any user session)" -ForegroundColor Cyan
Write-Host "  Services:    Ollama + Dashboard (:5050) + Genesis Daemon" -ForegroundColor Cyan
Write-Host "  Restarts:    3 retries, 5 min interval" -ForegroundColor Cyan
Write-Host "  Logs:        .tmp/genesis/launcher.log" -ForegroundColor Cyan
Write-Host "               .tmp/genesis/daemon.log" -ForegroundColor Cyan
Write-Host "               .tmp/dashboard.log" -ForegroundColor Cyan
Write-Host ""
Write-Host "Commands:" -ForegroundColor Yellow
Write-Host "  Start now:   schtasks /run /tn ${q}ICDEV Genesis Daemon${q}" -ForegroundColor White
Write-Host "  Status:      schtasks /query /tn ${q}ICDEV Genesis Daemon${q} /v" -ForegroundColor White
Write-Host "  Stop:        schtasks /end /tn ${q}ICDEV Genesis Daemon${q}" -ForegroundColor White
Write-Host "  Remove:      schtasks /delete /tn ${q}ICDEV Genesis Daemon${q} /f" -ForegroundColor White
Write-Host ""
Write-Host "  Daemon log:  type .tmp\genesis\daemon.log" -ForegroundColor White
Write-Host "  Reflex status: python tools\genesis\daemon.py --status" -ForegroundColor White
