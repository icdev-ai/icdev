# ICDEV Modular Installer -- Windows PowerShell
# Usage:
#   .\deploy\install.ps1                    # Interactive installer
#   .\deploy\install.ps1 isv_startup        # Install with named profile
#   .\deploy\install.ps1 -Help              # Show help
#
# Prerequisites (required): Python 3.11+, pip
# Prerequisites (optional): Docker Desktop, kubectl, helm
#Requires -Version 5.1

param(
    [Parameter(Position=0)]
    [string]$Profile = "",

    [switch]$Help
)

$ErrorActionPreference = "Stop"

# ── Helpers ─────────────────────────────────────────────────────────────

function Write-Info  { param($msg) Write-Host "[INFO]  $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "[WARN]  $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "[ERROR] $msg" -ForegroundColor Red }
function Write-Step  { param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }

# ── Help ────────────────────────────────────────────────────────────────

if ($Help) {
    Write-Host "ICDEV Modular Installer"
    Write-Host ""
    Write-Host "Usage:"
    Write-Host "  .\deploy\install.ps1                   Interactive mode (recommended)"
    Write-Host "  .\deploy\install.ps1 <profile>         Install with a named profile"
    Write-Host ""
    Write-Host "Available profiles:"
    Write-Host "  isv_startup       - ISV small team (core, builder, security, dashboard)"
    Write-Host "  isv_enterprise    - ISV with FedRAMP (adds CI/CD, monitoring, infra)"
    Write-Host "  si_consulting     - System Integrator (compliance-focused)"
    Write-Host "  si_enterprise     - Large SI (full lifecycle)"
    Write-Host "  dod_team          - DoD contractor (MOSA, DevSecOps/ZTA, FIPS)"
    Write-Host "  healthcare        - Healthcare (HIPAA, HITRUST, SOC 2)"
    Write-Host "  financial         - Financial services (PCI DSS, SOC 2, ISO 27001)"
    Write-Host "  law_enforcement   - Law enforcement (CJIS, FIPS 199/200)"
    Write-Host "  govcloud_full     - Everything enabled (reference install)"
    Write-Host "  custom            - Minimal core, add modules manually"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -Help             Show this help message"
    Write-Host ""
    Write-Host "Examples:"
    Write-Host "  .\deploy\install.ps1 dod_team"
    Write-Host "  .\deploy\install.ps1 govcloud_full"
    exit 0
}

# ── Banner ──────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  ICDEV Modular Installer" -ForegroundColor Cyan
Write-Host "  Intelligent Coding Development Platform" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

# ── Resolve project root ───────────────────────────────────────────────

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot
Write-Info "Project root: $ProjectRoot"

# ── Step 1: Prerequisites ──────────────────────────────────────────────

Write-Step "Step 1/5: Checking prerequisites"

$PrereqOk = $true
$PythonCmd = $null

# Find Python 3
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($LASTEXITCODE -eq 0 -and $ver -match "Python 3") {
            $PythonCmd = $cmd
            Write-Info "Python: $ver"
            break
        }
    } catch {}
}

if (-not $PythonCmd) {
    Write-Err "Python 3.11+ is required but not found."
    Write-Err "Install from https://www.python.org/downloads/"
    Write-Err "Ensure 'Add Python to PATH' is checked during installation."
    $PrereqOk = $false
}

# Check minimum version (3.9+)
if ($PythonCmd) {
    try {
        $PyMinor = & $PythonCmd -c "import sys; print(sys.version_info.minor)" 2>&1
        if ([int]$PyMinor -lt 9) {
            Write-Err "Python 3.9+ is required. Found: Python 3.$PyMinor"
            $PrereqOk = $false
        }
    } catch {
        Write-Warn "Could not determine Python minor version."
    }
}

# pip
if ($PythonCmd) {
    try {
        & $PythonCmd -c "import pip" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Info "pip available"
        } else {
            Write-Err "pip is required. Run: $PythonCmd -m ensurepip --upgrade"
            $PrereqOk = $false
        }
    } catch {
        Write-Err "pip is required. Run: $PythonCmd -m ensurepip --upgrade"
        $PrereqOk = $false
    }
}

# Optional: Docker
try {
    $dockerVer = docker --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Info "Docker: $dockerVer"
    }
} catch {
    Write-Warn "Docker not found. Required for container-based deployment."
    Write-Warn "  Install: https://docs.docker.com/desktop/install/windows-install/"
}

# Optional: kubectl
try {
    $kubectlVer = kubectl version --client 2>&1 | Select-Object -First 1
    if ($LASTEXITCODE -eq 0) {
        Write-Info "kubectl: $kubectlVer"
    }
} catch {
    Write-Warn "kubectl not found. Required for Kubernetes deployment."
}

# Optional: helm
try {
    $helmVer = helm version --short 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Info "Helm: $helmVer"
    }
} catch {
    Write-Warn "Helm not found. Required for Helm-based deployment."
}

if (-not $PrereqOk) {
    Write-Err "Prerequisites not met. Fix the errors above and re-run."
    exit 1
}

# ── Step 2: Python Virtual Environment ─────────────────────────────────

Write-Step "Step 2/5: Setting up Python virtual environment"

$VenvDir = Join-Path $ProjectRoot ".venv"
if (Test-Path $VenvDir) {
    Write-Info "Existing venv found at $VenvDir"
} else {
    Write-Info "Creating virtual environment at $VenvDir"
    & $PythonCmd -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to create virtual environment."
        exit 1
    }
}

# Activate
$ActivateScript = Join-Path $VenvDir "Scripts" "Activate.ps1"
if (Test-Path $ActivateScript) {
    . $ActivateScript
    Write-Info "Activated venv"
} else {
    Write-Err "Could not find venv activation script at $ActivateScript"
    exit 1
}

# ── Step 3: Install Python Dependencies ────────────────────────────────

Write-Step "Step 3/5: Installing Python dependencies"

$ReqFile = Join-Path $ProjectRoot "requirements.txt"
$PyProjectFile = Join-Path $ProjectRoot "pyproject.toml"

if (Test-Path $ReqFile) {
    & pip install --quiet --upgrade pip 2>$null
    & pip install --quiet -r $ReqFile
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Some dependencies may have failed to install."
        Write-Warn "Check the output above for details."
    } else {
        Write-Info "Dependencies installed from requirements.txt"
    }
} elseif (Test-Path $PyProjectFile) {
    & pip install --quiet --upgrade pip 2>$null
    & pip install --quiet -e $ProjectRoot
    Write-Info "Dependencies installed from pyproject.toml"
} else {
    Write-Warn "No requirements.txt or pyproject.toml found. Skipping pip install."
    Write-Warn "Some features may not work without dependencies."
}

# ── Step 4: Initialize Database ────────────────────────────────────────

Write-Step "Step 4/5: Initializing ICDEV database"

$DataDir = Join-Path $ProjectRoot "data"
if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
}

$DbFile = Join-Path $DataDir "icdev.db"
if (Test-Path $DbFile) {
    Write-Info "Existing database found at $DbFile"
    Write-Info "Running migrations (if any)..."
    try {
        & $PythonCmd tools/db/init_icdev_db.py 2>$null
    } catch {}
} else {
    & $PythonCmd tools/db/init_icdev_db.py
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to initialize database."
        exit 1
    }
    Write-Info "Database initialized at $DbFile"
}

# ── Step 5: Run Modular Installer ──────────────────────────────────────

Write-Step "Step 5/5: Running ICDEV modular installer"

if ($Profile) {
    Write-Info "Installing with profile: $Profile"
    & $PythonCmd tools/installer/installer.py --profile $Profile
} else {
    Write-Info "Starting interactive installer..."
    & $PythonCmd tools/installer/installer.py --interactive
}

if ($LASTEXITCODE -ne 0) {
    Write-Err "Installer exited with errors. Check the output above."
    exit 1
}

# ── Summary ─────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  ICDEV installation complete!" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Project root : $ProjectRoot"
Write-Host "  Python venv  : $VenvDir"
Write-Host "  Database     : $DbFile"
Write-Host ""
Write-Host "  Next steps:"
Write-Host "    .venv\Scripts\Activate.ps1"
Write-Host ""
Write-Host "    # Start the web dashboard"
Write-Host "    python tools/dashboard/app.py"
Write-Host ""
Write-Host "    # Verify your installation"
Write-Host "    python tools/testing/health_check.py"
Write-Host ""
Write-Host "    # Generate deployment artifacts"
Write-Host "    python tools/installer/platform_setup.py --generate docker --modules core,llm,builder,dashboard"
Write-Host ""
Write-Host "    # Run with Docker Compose"
Write-Host "    docker compose up -d"
Write-Host ""
