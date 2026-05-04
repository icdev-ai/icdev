# CUI // SP-CTI
# ICDEV(tm) Genesis Daemon -- Windows Task Scheduler Registration
#
# Run once (as the logged-in user) to register the daemon as a startup task.
# The task fires at logon, runs hidden, and restarts up to 3x on failure.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File tools\genesis\register_startup.ps1
#
# Remove:
#   powershell -ExecutionPolicy Bypass -File tools\genesis\unregister_startup.ps1

$TaskName  = "ICDEV-Genesis-Daemon"
$Project   = "C:\Users\schuo\Downloads\ICDev"
$Script    = "$Project\tools\genesis\start_daemon.ps1"
$User      = $env:USERNAME

$action = New-ScheduledTaskAction `
    -Execute          "powershell.exe" `
    -Argument         "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Script`"" `
    -WorkingDirectory $Project

# Fire at logon; also fire immediately if a scheduled start was missed (e.g. machine was off)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit  (New-TimeSpan -Hours 0) `
    -RestartCount        3 `
    -RestartInterval     (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable `
    -MultipleInstances   IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId   $User `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName    $TaskName `
    -Action      $action `
    -Trigger     $trigger `
    -Settings    $settings `
    -Principal   $principal `
    -Description "ICDEV(tm) Genesis Daemon -- autonomous reflexes: failure_triage (30m), oracle_triage (3h), awareness (3h), heal (5m), and 20+ others. Logs: .tmp/genesis_daemon.log" `
    -Force | Out-Null

Write-Host ""
Write-Host "Registered Task Scheduler task: $TaskName"
Write-Host "  Trigger : At logon for $User"
Write-Host "  Action  : pythonw tools\genesis\daemon.py (hidden)"
Write-Host "  Restarts: up to 3x, 2 min apart"
Write-Host "  Log     : $Project\.tmp\genesis_daemon.log"
Write-Host ""
Write-Host "To verify : Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "To run now: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove : powershell -File tools\genesis\unregister_startup.ps1"
