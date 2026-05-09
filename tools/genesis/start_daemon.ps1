# CUI // SP-CTI
# ICDEV™ Genesis Daemon -- Startup Wrapper (cross-platform safe)
#
# Derives project root from this script's own location — no hardcoded paths.
# Launched manually or via Windows Task Scheduler.
#
# Usage (manual):
#   powershell -ExecutionPolicy Bypass -File tools\genesis\start_daemon.ps1
# Usage (hidden window, no console):
#   powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File tools\genesis\start_daemon.ps1

$ProjectDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $ProjectDir

$env:ICDEV_GENESIS_ENABLED = "true"
$env:PYTHONIOENCODING      = "utf-8"
$env:PYTHONDONTWRITEBYTECODE = "1"

# Delegate to the cross-platform Python launcher
& python tools\genesis\launch.py @args
