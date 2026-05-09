# CUI // SP-CTI
# ICDEV™ Dashboard Autostart Script (Windows)
# Starts the ICDEV dashboard and verifies it is reachable.
# Intended to be called by the Windows Task Scheduler at user logon
# (registered by register_autostart.ps1).
#
# Derives project root from script location — no hardcoded paths.
#
# Usage: powershell.exe -WindowStyle Hidden -File tools\ops\autostart_windows.ps1

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

# ------------------------------------------------------------------
# 1. Change to project root
# ------------------------------------------------------------------
Set-Location -Path $ProjectDir

# ------------------------------------------------------------------
# 2. Ensure .tmp directory exists for log files
# ------------------------------------------------------------------
$TmpDir = Join-Path $ProjectDir ".tmp"
if (-not (Test-Path $TmpDir)) {
    New-Item -ItemType Directory -Path $TmpDir | Out-Null
}

$DashboardLog = Join-Path $TmpDir "dashboard.log"

# ------------------------------------------------------------------
# 3. Start the dashboard (non-blocking, output redirected to log)
# ------------------------------------------------------------------
Write-Host "[ICDEV-autostart] Starting dashboard..."
Start-Process python `
    -ArgumentList "tools/dashboard/app.py" `
    -WorkingDirectory (Get-Location) `
    -RedirectStandardOutput $DashboardLog `
    -NoNewWindow

# ------------------------------------------------------------------
# 4. Wait up to 15 seconds for dashboard to become reachable
# ------------------------------------------------------------------
$HealthUrl  = "http://localhost:5050/health"
$MaxSeconds = 15
$Interval   = 2
$Elapsed    = 0
$IsUp       = $false

Write-Host "[ICDEV-autostart] Waiting up to ${MaxSeconds}s for dashboard at $HealthUrl ..."

while ($Elapsed -lt $MaxSeconds) {
    Start-Sleep -Seconds $Interval
    $Elapsed += $Interval
    try {
        $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3
        if ($response.StatusCode -eq 200) {
            $IsUp = $true
            break
        }
    } catch {
        # Not up yet — keep waiting
    }
}

# ------------------------------------------------------------------
# 5. Print status
# ------------------------------------------------------------------
if ($IsUp) {
    Write-Host "[ICDEV-autostart] Dashboard is UP at $HealthUrl (after ${Elapsed}s)"
} else {
    Write-Host "[ICDEV-autostart] WARNING: Dashboard did not respond within ${MaxSeconds}s."
    Write-Host "[ICDEV-autostart] Check log: $DashboardLog"
}
