#!/usr/bin/env bash
# CUI // SP-CTI
# PGP verification test runner with watchdog abort handler.
#
# Runs the PostgreSQL-primary verification test suite and installs a
# SIGTERM/SIGINT trap that triggers an immediate DB-connection-release
# (idle-in-transaction backend sweep) so cancelled jobs never leave
# kanban_tasks rows in a locked/backlog state.
#
# Usage:
#   ./tools/testing/test_runner.sh          # run full PGP suite
#   ./tools/testing/test_runner.sh --fast   # skip DB-schema checks
#   ./tools/testing/test_runner.sh -k json  # forwarded to pytest
set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

LOG_DIR="${LOG_DIR:-${PROJECT_ROOT}/.logs}"
LOG_FILE="${LOG_DIR}/pgp_test_runner_$(date -u +%Y%m%d_%H%M%S).log"
TEST_ARGS=("$@")
PYTEST_MARKER="pgp or postgres or pg_backend or backend_resolution or translate_sql or migrate_canvas or pg_portability"

mkdir -p "${LOG_DIR}"

# ── Logging helpers ──────────────────────────────────────────────────────────
log() {
    local level="$1"; shift
    local msg
    msg="$(printf '[%s] [%s] %s' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${level}" "$*")"
    printf '%s\n' "${msg}" | tee -a "${LOG_FILE}"
}

# ── Watchdog abort handler ─────────────────────────────────────────────────
# Triggered on SIGTERM or SIGINT.  Performs an immediate sweep of idle-in-
# transaction PostgreSQL backends and closes any open ICDEV connection pools
# so that test-runner cancellation cannot leave kanban_tasks rows locked.
watchdog_abort() {
    local sig="$1"
    log WARN "=== WATCHDOG ABORT triggered by ${sig} ==="
    log WARN "Releasing DB connections and sweeping idle backends…"

    # 1) Best-effort PG idle-in-transaction sweep (same logic as CI + _pg_unblock.py)
    python -c "
import os, sys
sys.path.insert(0, r'${PROJECT_ROOT}')
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(r'${PROJECT_ROOT}', '.env'))
except Exception:
    pass

try:
    import psycopg2
    conn = psycopg2.connect(
        host='127.0.0.1',
        port=int(os.environ.get('ICDEV_PG_PORT', '5432')),
        dbname=os.environ.get('ICDEV_PG_DB', 'icdev'),
        user=os.environ.get('ICDEV_PG_USER', 'icdev'),
        password=os.environ.get('ICDEV_PG_PASSWORD', ''),
        connect_timeout=5,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(\"\"\"
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = current_database()
          AND state IN ('idle in transaction', 'idle in transaction (aborted)')
          AND backend_start < NOW() - INTERVAL '2 minutes'
          AND pid <> pg_backend_pid()
    \"\"\")
    n = cur.rowcount
    cur.close()
    conn.close()
    print(f'PG idle-in-transaction cleanup: terminated {n} backend(s)')
except Exception as e:
    print(f'PG cleanup skipped: {e}')
" 2>&1 | tee -a "${LOG_FILE}"

    # 2) Best-effort close of ICDEV connection pools (SQLite dies with process)
    python -c "
import sys
sys.path.insert(0, r'${PROJECT_ROOT}')
try:
    from tools.db import storage
    pool = getattr(storage, '_pg_pool', None)
    if pool is not None:
        pool.closeall()
        storage._pg_pool = None
        print('ICDEV PG pool closed')
except Exception as e:
    print(f'Pool close skipped: {e}')
" 2>&1 | tee -a "${LOG_FILE}"

    log WARN "=== Watchdog abort complete; exiting with code 130 ==="
    exit 130
}

# Register signal traps
trap 'watchdog_abort SIGTERM' SIGTERM
trap 'watchdog_abort SIGINT'  SIGINT

# ── Main test run ────────────────────────────────────────────────────────────
log INFO "=== PGP Test Runner START ==="
log INFO "Log: ${LOG_FILE}"
log INFO "pytest args: ${TEST_ARGS[*]:-default suite}"

# Discover PGP-related tests.  If the caller passes explicit args, forward them
# verbatim; otherwise run the pre-defined PGP marker suite.
if [[ ${#TEST_ARGS[@]} -gt 0 ]]; then
    pytest \
        "${TEST_ARGS[@]}" \
        --verbose --tb=short \
        2>&1 | tee -a "${LOG_FILE}"
else
    pytest tests/ \
        -m "${PYTEST_MARKER}" \
        --verbose --tb=short \
        2>&1 | tee -a "${LOG_FILE}"
fi

EXIT_CODE=${PIPESTATUS[0]}
log INFO "pytest exited with code ${EXIT_CODE}"

if [[ ${EXIT_CODE} -ne 0 ]]; then
    log ERROR "PGP test suite FAILED (exit ${EXIT_CODE})"
    echo "${EXIT_CODE}" > "${LOG_DIR}/pgp_test_runner_last_exit.txt"
    exit "${EXIT_CODE}"
fi

log INFO "=== PGP Test Runner COMPLETE ==="
echo "0" > "${LOG_DIR}/pgp_test_runner_last_exit.txt"
exit 0
