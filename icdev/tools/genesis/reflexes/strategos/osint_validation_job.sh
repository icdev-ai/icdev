#!/usr/bin/env bash
# CUI // SP-CTI
# OSINT validation batch job — verbose logging, heartbeat check, error capture
set -euo pipefail

LOG_DIR="${LOG_DIR:-$(pwd)/.tmp/osint}"
LOG_FILE="${LOG_DIR}/osint_validation_$(date +%Y%m%d_%H%M%S).log"
HEALTH_ENDPOINT="${HEALTH_ENDPOINT:-http://localhost:5050/health}"
TIMEOUT_SECS="${TIMEOUT_SECS:-10}"

mkdir -p "$LOG_DIR"

log() {
    local level="$1"; shift
    printf '[%s] [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "$*" | tee -a "$LOG_FILE"
}

# ── 1. Heartbeat check ─────────────────────────────────────────────────────
log INFO "=== OSINT Validation Job START ==="
log INFO "Log: $LOG_FILE"
log INFO "Heartbeat check → $HEALTH_ENDPOINT"

HTTP_STATUS=$(curl -o /dev/null -s -w "%{http_code}" --max-time "$TIMEOUT_SECS" "$HEALTH_ENDPOINT" 2>>"$LOG_FILE") || {
    log ERROR "Heartbeat curl failed (exit $?). Aborting."
    exit 2
}

if [[ "$HTTP_STATUS" != "200" ]]; then
    log ERROR "Heartbeat returned HTTP $HTTP_STATUS (expected 200). Aborting."
    exit 3
fi
log INFO "Heartbeat OK (HTTP $HTTP_STATUS)"

# ── 2. OSINT harvester validation ──────────────────────────────────────────
TOOL="python tools/genesis/reflexes/strategos/osint_harvester.py"

log INFO "Invoking: $TOOL --validate"
set +e
$TOOL --validate >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
set -e

if [[ $EXIT_CODE -ne 0 ]]; then
    log ERROR "osint_harvester --validate exited with code $EXIT_CODE"
    echo "$EXIT_CODE" > "${LOG_DIR}/last_exit_code.txt"
    exit "$EXIT_CODE"
fi
log INFO "osint_harvester --validate completed OK (exit 0)"

# ── 3. Normalizer validation ───────────────────────────────────────────────
NORMALIZER="python tools/threat_analysis/osint_normalizer.py"
if [[ -f "tools/threat_analysis/osint_normalizer.py" ]]; then
    log INFO "Invoking: $NORMALIZER --validate"
    set +e
    $NORMALIZER --validate >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?
    set -e

    if [[ $EXIT_CODE -ne 0 ]]; then
        log ERROR "osint_normalizer --validate exited with code $EXIT_CODE"
        echo "$EXIT_CODE" > "${LOG_DIR}/last_exit_code.txt"
        exit "$EXIT_CODE"
    fi
    log INFO "osint_normalizer --validate completed OK (exit 0)"
else
    log WARN "osint_normalizer.py not found — skipping normalizer step"
fi

# ── 4. Wrap-up ─────────────────────────────────────────────────────────────
echo "0" > "${LOG_DIR}/last_exit_code.txt"
log INFO "=== OSINT Validation Job COMPLETE ==="
