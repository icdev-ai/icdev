#!/usr/bin/env bash
# CUI // SP-CTI
# ICDEV™ Linux/macOS/container production launcher.
# Replaces Windows Task Scheduler for POSIX bare-metal and container PID 1.
#
# Usage:
#   ./scripts/start-production.sh [--no-ollama] [--dry-run]
#
# Container (docker-compose CMD override):
#   command: ["/app/scripts/start-production.sh"]
#
# Systemd (via scripts/icdev.service.template):
#   ExecStart=%h/icdev/scripts/start-production.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PYTHONPATH="${PROJECT_ROOT}"
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONIOENCODING=utf-8
export ICDEV_GENESIS_ENABLED="${ICDEV_GENESIS_ENABLED:-true}"

# Load .env if present and not already set by container runtime
if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "${PROJECT_ROOT}/.env"
    set +a
fi

# Ensure runtime scratch directories exist (also created by Dockerfile, belt-and-suspenders)
mkdir -p "${PROJECT_ROOT}/.tmp/genesis"

cd "${PROJECT_ROOT}"

echo "[icdev] Project root : ${PROJECT_ROOT}"
echo "[icdev] Python       : $(python3 --version 2>&1)"
echo "[icdev] Platform     : $(uname -s)"
echo "[icdev] Starting services..."

exec python3 -B tools/genesis/launch.py "$@"
