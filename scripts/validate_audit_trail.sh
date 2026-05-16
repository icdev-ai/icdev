#!/usr/bin/env bash
# CUI // SP-CTI
# NIST 800-53 AU-2 / AU-9 Audit Trail Compliance Validator
# Usage: ./scripts/validate_audit_trail.sh
# Reports: Compliant | Non-Compliant with missing-field details

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_FILE="tests/compliance/audit_nist.py"
SCHEMA_FILE="args/schema/audit_events.json"
HOOK_FILE=".claude/hooks/pre_tool_use.py"
REPORT_FILE=".tmp/audit_compliance_report.json"

mkdir -p "$REPO_ROOT/.tmp"

echo "=== ICDEV™ Cross-Agency Transfer Audit Compliance Validator ==="
echo "Classification: CUI // SP-CTI"
echo "Controls: NIST AU-2 (Audit Events) | AU-9 (Protection of Audit Information)"
echo ""

# ---------------------------------------------------------------------------
# 1. Run compliance tests
# ---------------------------------------------------------------------------
TEST_STATUS="PASS"
TEST_OUTPUT=""
if ! command -v pytest &>/dev/null; then
    echo "[WARN] pytest not in PATH; falling back to python -m pytest"
    PYTEST="python -m pytest"
else
    PYTEST="pytest"
fi

if $PYTEST "$REPO_ROOT/$TEST_FILE" -v --tb=short >/dev/null 2>&1; then
    echo "[PASS] Compliance tests ($TEST_FILE)"
else
    TEST_STATUS="FAIL"
    echo "[FAIL] Compliance tests ($TEST_FILE)"
    TEST_OUTPUT="$($PYTEST "$REPO_ROOT/$TEST_FILE" -v --tb=short 2>&1 || true)"
fi

# ---------------------------------------------------------------------------
# 2. Validate JSON schema exists and is well-formed
# ---------------------------------------------------------------------------
SCHEMA_STATUS="PASS"
if [ -f "$REPO_ROOT/$SCHEMA_FILE" ]; then
    if python -c "import json, pathlib; p = pathlib.Path('$SCHEMA_FILE'); json.load(p.open())" 2>/dev/null; then
        echo "[PASS] JSON schema valid ($SCHEMA_FILE)"
    else
        SCHEMA_STATUS="FAIL"
        echo "[FAIL] JSON schema malformed ($SCHEMA_FILE)"
    fi
else
    SCHEMA_STATUS="FAIL"
    echo "[FAIL] JSON schema missing ($SCHEMA_FILE)"
fi

# ---------------------------------------------------------------------------
# 3. Verify cross_agency_transfers is in APPEND_ONLY_TABLES
# ---------------------------------------------------------------------------
APPEND_STATUS="PASS"
if grep -q 'cross_agency_transfers' "$REPO_ROOT/$HOOK_FILE" 2>/dev/null; then
    echo "[PASS] Append-only table registered in hook ($HOOK_FILE)"
else
    APPEND_STATUS="FAIL"
    echo "[FAIL] Append-only table NOT registered in hook ($HOOK_FILE)"
fi

# ---------------------------------------------------------------------------
# 4. Check required fields from audit_config.yaml
# ---------------------------------------------------------------------------
CONFIG_STATUS="PASS"
if grep -q 'required_fields:' "$REPO_ROOT/args/audit_config.yaml" 2>/dev/null; then
    echo "[PASS] Audit config defines required_fields"
else
    CONFIG_STATUS="FAIL"
    echo "[FAIL] Audit config missing required_fields"
fi

# ---------------------------------------------------------------------------
# 5. Simulate cross-agency transfer and inspect for missing fields
# ---------------------------------------------------------------------------
SIM_STATUS="PASS"
SIM_OUTPUT=$(python - <<'PY'
import json, sqlite3, sys, uuid, gc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.audit.cross_agency_transfer_logger import CrossAgencyTransferLogger
from unittest.mock import patch

db_path = Path(".tmp/compliance_sim.db")
conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row
conn.executescript("""
CREATE TABLE IF NOT EXISTS cross_agency_transfers (
    id TEXT PRIMARY KEY, transfer_id TEXT NOT NULL, event_type TEXT NOT NULL,
    source_agency TEXT NOT NULL, target_agency TEXT NOT NULL, data_type TEXT,
    data_classification TEXT NOT NULL DEFAULT 'CUI', actor TEXT NOT NULL DEFAULT '',
    project_id TEXT, bytes_transferred INTEGER, checksum TEXT, duration_ms INTEGER,
    rejection_reason TEXT, error_code TEXT, details TEXT, occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT,
    event_type TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
    details TEXT, affected_files TEXT, classification TEXT DEFAULT 'CUI',
    ip_address TEXT, session_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")
conn.commit()
conn.close()

def _conn():
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c

with patch("tools.audit.cross_agency_transfer_logger.get_connection", side_effect=_conn), \
     patch("tools.audit.audit_logger.get_connection", side_effect=_conn):
    cat = CrossAgencyTransferLogger()
    tid = "sim-" + str(uuid.uuid4())[:8]
    cat.log_initiated(tid, "DoD", "DHS", "intel", "alice", data_classification="SECRET", project_id="p1")
    cat.log_completed(tid, "DoD", "DHS", "alice", bytes_transferred=1024,
                      checksum="a"*64, duration_ms=500, data_classification="SECRET")

gc.collect()

conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM cross_agency_transfers WHERE transfer_id=?", (tid,)).fetchall()
required = ["transfer_id","event_type","source_agency","target_agency","actor","occurred_at","data_classification"]
missing = []
for r in rows:
    for f in required:
        if r[f] is None or str(r[f]) == "":
            missing.append(f"{r['id']}.{f}")
conn.close()

report = {
    "nist_controls": ["AU-2", "AU-9"],
    "status": "Compliant" if not missing else "Non-Compliant",
    "missing_fields": missing,
    "records_examined": len(rows),
    "simulated_transfer_id": tid,
}
print(json.dumps(report))
PY
) || SIM_STATUS="FAIL"

if [ "$SIM_STATUS" = "PASS" ]; then
    SIM_STATUS_JSON=$(echo "$SIM_OUTPUT" | python -c "import sys,json; d=json.load(sys.stdin); print(d['status'])")
    if [ "$SIM_STATUS_JSON" = "Compliant" ]; then
        echo "[PASS] Simulated cross-agency transfer — zero missing fields"
    else
        SIM_STATUS="FAIL"
        echo "[FAIL] Simulated cross-agency transfer — missing fields detected"
        echo "$SIM_OUTPUT"
    fi
else
    echo "[FAIL] Simulation script crashed"
    echo "$SIM_OUTPUT"
fi

# ---------------------------------------------------------------------------
# 6. Overall verdict
# ---------------------------------------------------------------------------
OVERALL="Compliant"
for check in "$TEST_STATUS" "$SCHEMA_STATUS" "$APPEND_STATUS" "$CONFIG_STATUS" "$SIM_STATUS"; do
    if [ "$check" != "PASS" ]; then
        OVERALL="Non-Compliant"
        break
    fi
done

cat > "$REPO_ROOT/$REPORT_FILE" <<EOF
{
  "classification": "CUI // SP-CTI",
  "nist_controls": ["AU-2", "AU-9"],
  "overall_status": "$OVERALL",
  "checks": {
    "compliance_tests": "$TEST_STATUS",
    "json_schema": "$SCHEMA_STATUS",
    "append_only_hook": "$APPEND_STATUS",
    "audit_config": "$CONFIG_STATUS",
    "simulated_transfer": "$SIM_STATUS"
  },
  "simulation": $(echo "$SIM_OUTPUT" || echo '{}'),
  "timestamp": "$(python -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')"
}
EOF

echo ""
echo "========================================"
echo "OVERALL STATUS: $OVERALL"
echo "========================================"
echo "Report written to: $REPORT_FILE"

if [ "$OVERALL" != "Compliant" ]; then
    exit 1
fi
