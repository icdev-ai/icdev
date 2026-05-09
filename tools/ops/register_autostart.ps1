# ICDEV Task Scheduler Registration Script (Windows)
# Registers autostart_windows.ps1 to run at user logon via Windows Task Scheduler.
#
# Run ONCE (no elevated privileges required — runs as current user):
#   powershell.exe -ExecutionPolicy Bypass -File register_autostart.ps1
#
# To remove the scheduled task:
#   Unregister-ScheduledTask -TaskName "ICDEV-Dashboard-Autostart" -Confirm:$false

$ProjectDir      = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$AutostartScript = Join-Path $ProjectDir "tools\ops\autostart_windows.ps1"

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-WindowStyle Hidden -File `"$AutostartScript`""

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd

Register-ScheduledTask `
    -TaskName "ICDEV-Dashboard-Autostart" `
    -Action   $action `
    -Trigger  $trigger `
    -Settings $settings `
    -RunLevel Limited `
    -Force

Write-Host "Task 'ICDEV-Dashboard-Autostart' registered successfully."
Write-Host "It will run at next logon for user: $env:USERNAME"
