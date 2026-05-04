# CUI // SP-CTI
# ICDEV(tm) Genesis Daemon -- Task Scheduler Removal
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File tools\genesis\unregister_startup.ps1

$TaskName = "ICDEV-Genesis-Daemon"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Task '$TaskName' not found -- nothing to remove."
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed Task Scheduler task: $TaskName"
Write-Host "Note: the daemon process (if running) was NOT killed."
Write-Host "      To stop it: Stop-Process -Id (Get-Content .tmp\genesis\daemon.pid)"
