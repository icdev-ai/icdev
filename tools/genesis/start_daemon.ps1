# CUI // SP-CTI
# ICDEV(tm) Genesis Daemon -- Startup Wrapper
#
# Launched by Windows Task Scheduler at logon.
# Uses pythonw.exe so no console window appears.
# The daemon writes its own PID to .tmp/genesis/daemon.pid
# and its logs to .tmp/genesis_daemon.log via Python logging.
#
# Usage (manual):
#   powershell -ExecutionPolicy Bypass -File tools\genesis\start_daemon.ps1

$ProjectDir = "C:\Users\schuo\Downloads\ICDev"
$PythonW    = "C:\Python314\pythonw.exe"
$PidFile    = "$ProjectDir\.tmp\genesis\daemon.pid"
$LogFile    = "$ProjectDir\.tmp\genesis_daemon.log"

Set-Location $ProjectDir

# Guard: if daemon is already alive, do nothing
if (Test-Path $PidFile) {
    $storedPid = (Get-Content $PidFile -Raw -ErrorAction SilentlyContinue).Trim()
    if ($storedPid -and (Get-Process -Id ([int]$storedPid) -ErrorAction SilentlyContinue)) {
        "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] start_daemon: already running (PID $storedPid) -- skipping." |
            Add-Content -Path $LogFile
        exit 0
    }
}

# Ensure .tmp/genesis directory exists
New-Item -ItemType Directory -Force -Path "$ProjectDir\.tmp\genesis" | Out-Null

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] start_daemon: launching Genesis daemon..." |
    Add-Content -Path $LogFile

# Start daemon hidden -- daemon.py writes PID file and logs itself
Start-Process `
    -FilePath        $PythonW `
    -ArgumentList    "tools\genesis\daemon.py" `
    -WorkingDirectory $ProjectDir `
    -WindowStyle     Hidden
