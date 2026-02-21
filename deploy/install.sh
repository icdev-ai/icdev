#!/usr/bin/env bash
# ICDEV Modular Installer -- Linux/macOS
# Usage:
#   ./deploy/install.sh                    # Interactive installer
#   ./deploy/install.sh isv_startup        # Install with named profile
#   ./deploy/install.sh --help             # Show help
#
# Prerequisites (required): python3 (3.11+), pip
# Prerequisites (optional): docker, kubectl, helm
set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
step()  { echo -e "\n${CYAN}==> $*${NC}"; }

# ── Help ────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    echo "ICDEV Modular Installer"
    echo ""
    echo "Usage:"
    echo "  $0                          Interactive mode (recommended for first install)"
    echo "  $0 <profile>                Install with a named profile"
    echo ""
    echo "Available profiles:"
    echo "  isv_startup       — ISV small team (core, builder, security, dashboard)"
    echo "  isv_enterprise    — ISV with FedRAMP (adds CI/CD, monitoring, infra)"
    echo "  si_consulting     — System Integrator (compliance-focused)"
    echo "  si_enterprise     — Large SI (full lifecycle)"
    echo "  dod_team          — DoD contractor (MOSA, DevSecOps/ZTA, FIPS)"
    echo "  healthcare        — Healthcare (HIPAA, HITRUST, SOC 2)"
    echo "  financial         — Financial services (PCI DSS, SOC 2, ISO 27001)"
    echo "  law_enforcement   — Law enforcement (CJIS, FIPS 199/200)"
    echo "  govcloud_full     — Everything enabled (reference install)"
    echo "  custom            — Minimal core, add modules manually"
    echo ""
    echo "Options:"
    echo "  --help, -h    Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 dod_team"
    echo "  $0 govcloud_full"
    exit 0
fi

# ── Banner ──────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}================================================================${NC}"
echo -e "${CYAN}  ICDEV Modular Installer${NC}"
echo -e "${CYAN}  Intelligent Coding Development Platform${NC}"
echo -e "${CYAN}================================================================${NC}"
echo ""

# ── Resolve project root ───────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"
info "Project root: $PROJECT_ROOT"

# ── Step 1: Prerequisites ──────────────────────────────────────────────
step "Step 1/5: Checking prerequisites"

PREREQ_OK=true

# Python 3
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    # Check that python is Python 3
    PY_MAJOR=$(python -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo "2")
    if [[ "$PY_MAJOR" == "3" ]]; then
        PYTHON_CMD="python"
    else
        err "python points to Python 2. Install Python 3.11+ and ensure python3 is in PATH."
        PREREQ_OK=false
    fi
else
    err "Python 3 is required but not found."
    err "Install Python 3.11+ from https://www.python.org/downloads/"
    PREREQ_OK=false
fi

if [[ "$PREREQ_OK" == "true" ]]; then
    PYTHON_VERSION=$($PYTHON_CMD --version 2>&1)
    info "Python: $PYTHON_VERSION"

    # Check minimum version (3.9+ required for graphlib)
    PY_MINOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo "0")
    if [[ "$PY_MINOR" -lt 9 ]]; then
        err "Python 3.9+ is required. Found: $PYTHON_VERSION"
        PREREQ_OK=false
    fi
fi

# pip
if [[ "$PREREQ_OK" == "true" ]]; then
    if $PYTHON_CMD -c "import pip" &>/dev/null; then
        info "pip available"
    else
        err "pip is required but not found."
        err "Install with: $PYTHON_CMD -m ensurepip --upgrade"
        PREREQ_OK=false
    fi
fi

# Optional tools
if command -v docker &>/dev/null; then
    info "Docker: $(docker --version 2>/dev/null | head -1)"
else
    warn "Docker not found. Required for container-based deployment."
    warn "  Install: https://docs.docker.com/get-docker/"
fi

if command -v kubectl &>/dev/null; then
    KUBECTL_VER=$(kubectl version --client 2>/dev/null | head -1 || echo "unknown")
    info "kubectl: $KUBECTL_VER"
else
    warn "kubectl not found. Required for Kubernetes deployment."
fi

if command -v helm &>/dev/null; then
    info "Helm: $(helm version --short 2>/dev/null || echo 'unknown')"
else
    warn "Helm not found. Required for Helm-based deployment."
fi

if [[ "$PREREQ_OK" != "true" ]]; then
    err "Prerequisites not met. Fix the errors above and re-run."
    exit 1
fi

# ── Step 2: Python Virtual Environment ─────────────────────────────────
step "Step 2/5: Setting up Python virtual environment"

VENV_DIR="$PROJECT_ROOT/.venv"
if [[ -d "$VENV_DIR" ]]; then
    info "Existing venv found at $VENV_DIR"
else
    info "Creating virtual environment at $VENV_DIR"
    $PYTHON_CMD -m venv "$VENV_DIR"
fi

# Activate
if [[ -f "$VENV_DIR/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    info "Activated venv: $(which python)"
else
    err "Could not find venv activation script at $VENV_DIR/bin/activate"
    exit 1
fi

# ── Step 3: Install Python Dependencies ────────────────────────────────
step "Step 3/5: Installing Python dependencies"

if [[ -f "$PROJECT_ROOT/requirements.txt" ]]; then
    pip install --quiet --upgrade pip 2>/dev/null || true
    pip install --quiet -r "$PROJECT_ROOT/requirements.txt"
    info "Dependencies installed from requirements.txt"
elif [[ -f "$PROJECT_ROOT/pyproject.toml" ]]; then
    pip install --quiet --upgrade pip 2>/dev/null || true
    pip install --quiet -e "$PROJECT_ROOT"
    info "Dependencies installed from pyproject.toml"
else
    warn "No requirements.txt or pyproject.toml found. Skipping pip install."
    warn "Some features may not work without dependencies."
fi

# ── Step 4: Initialize Database ────────────────────────────────────────
step "Step 4/5: Initializing ICDEV database"

DATA_DIR="$PROJECT_ROOT/data"
mkdir -p "$DATA_DIR"

if [[ -f "$DATA_DIR/icdev.db" ]]; then
    info "Existing database found at $DATA_DIR/icdev.db"
    info "Running migrations (if any)..."
    python tools/db/init_icdev_db.py 2>/dev/null || true
else
    python tools/db/init_icdev_db.py
    info "Database initialized at $DATA_DIR/icdev.db"
fi

# ── Step 5: Run Modular Installer ──────────────────────────────────────
step "Step 5/5: Running ICDEV modular installer"

PROFILE="${1:-}"

if [[ -n "$PROFILE" ]]; then
    info "Installing with profile: $PROFILE"
    python tools/installer/installer.py --profile "$PROFILE"
else
    info "Starting interactive installer..."
    python tools/installer/installer.py --interactive
fi

# ── Summary ─────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}================================================================${NC}"
echo -e "${GREEN}  ICDEV installation complete!${NC}"
echo -e "${GREEN}================================================================${NC}"
echo ""
echo "  Project root : $PROJECT_ROOT"
echo "  Python venv  : $VENV_DIR"
echo "  Database     : $DATA_DIR/icdev.db"
echo ""
echo "  Next steps:"
echo "    source $VENV_DIR/bin/activate"
echo ""
echo "    # Start the web dashboard"
echo "    python tools/dashboard/app.py"
echo ""
echo "    # Verify your installation"
echo "    python tools/testing/health_check.py"
echo ""
echo "    # Generate deployment artifacts"
echo "    python tools/installer/platform_setup.py --generate docker --modules core,llm,builder,dashboard"
echo ""
echo "    # Run with Docker Compose"
echo "    docker compose up -d"
echo ""
