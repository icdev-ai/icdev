#!/usr/bin/env bash
# CUI // SP-CTI
# ICDEV™ Alert Pipeline Load Test Runner
#
# Runs the E2E integration test at 500 alerts/sec and validates that
# aggregate end-to-end latency (DB + Network + Serialization) p99 < 5 000 ms.
#
# Usage:
#   bash scripts/load_test.sh              # standard run
#   bash scripts/load_test.sh --json       # emit machine-readable JSON result
#   bash scripts/load_test.sh --help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---- usage ------------------------------------------------------------------
show_help() {
  cat <<EOF
Usage: $0 [OPTIONS]

Options:
  --json    Pass through to e2e_alert_test.py (raw JSON to stdout)
  --help    Show this message

Environment variables honored by the Python test:
  ICDEV_DB_PATH            Override SQLite path (uses temp file by default)
  ICDEV_STORAGE_BACKEND    Set to 'sqlite' (default) or 'postgresql'
  SIEM_ENDPOINT            External SIEM URL (test uses a local mock by default)

Exit codes:
  0   PASS — p99 latency < 5 000 ms
  1   FAIL — p99 latency >= 5 000 ms or other error
EOF
}

for arg in "$@"; do
  if [[ "$arg" == "--help" ]]; then
    show_help
    exit 0
  fi
done

# ---- pre-flight -------------------------------------------------------------
cd "$PROJECT_ROOT"

if ! python --version &>/dev/null && ! python3 --version &>/dev/null; then
  echo "ERROR: python not found on PATH" >&2
  exit 1
fi

PYTHON="${PYTHON:-python}"
if ! "$PYTHON" --version &>/dev/null; then
  PYTHON=python3
fi

if [[ ! -f tests/e2e_alert_test.py ]]; then
  echo "ERROR: tests/e2e_alert_test.py not found (expected at $PROJECT_ROOT/tests/)" >&2
  exit 1
fi

# ---- run --------------------------------------------------------------------
echo "=== ICDEV™ Alert Pipeline Load Test ==="
echo "    Target : 500 alerts/sec"
echo "    P99 SLA: < 5 000 ms (NIST AU-6, SI-4)"
echo "    Root   : $PROJECT_ROOT"
echo ""

"$PYTHON" tests/e2e_alert_test.py "$@"
EXIT_CODE=$?

echo ""
if [[ $EXIT_CODE -eq 0 ]]; then
  echo "============================="
  echo "  LOAD TEST PASSED"
  echo "============================="
else
  echo "============================="
  echo "  LOAD TEST FAILED"
  echo "  p99 latency exceeds 5-second SLA"
  echo "============================="
fi

exit $EXIT_CODE
