#!/usr/bin/env python3
# CUI // SP-CTI
"""ISSO Gate Simulation — approve workflow steps with audit trail.

Writes to sc_audit (append-only, NIST AU-6 compliant).
Used by Scenario B --simulate to make the ISSO gate feel live.

Usage:
    python tools/sdc/isso_gate.py --step-run demo-run-step-04 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CANVAS_DB = _ROOT / "data" / "security_canvas.db"


def _canvas_conn():
    # PG-primary via the Security Canvas helper (SC_STORAGE_BACKEND); SQLite is a
    # guarded fallback. Returns a StorageConnection so %s placeholders translate.
    from tools.security_canvas.db.init_db import get_connection

    return get_connection()


def approve_demo(
    step_run_id: str,
    approver: str = "isso-demo@agency.gov",
    reason: str = "demo-auto-approve",
) -> dict:
    """Approve a demo workflow step and write an immutable sc_audit record.

    Args:
        step_run_id: ID of the sdc_workflow_step_runs row to approve.
        approver:    Approver identity (email or username).
        reason:      Approval rationale written to audit log.

    Returns:
        dict with ok, step_run_id, approver, approved_at, audit_id.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = _canvas_conn()
    try:
        # Update the step run record
        conn.execute(
            "UPDATE sdc_workflow_step_runs SET approved_by=%s, approved_at=%s, status='completed' "
            "WHERE id=%s",
            (approver, now_iso, step_run_id),
        )

        # Write immutable audit record (sc_audit is APPEND-ONLY per NIST AU-6)
        conn.execute(
            "INSERT INTO sc_audit (action, entity_type, entity_id, details, user_id, classification, ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                "isso_approval",
                "workflow_step",
                step_run_id,
                json.dumps({"reason": reason, "step_run_id": step_run_id}),
                approver,
                "CUI // SP-CTI",
                now_iso,
            ),
        )

        # Read back the audit row id
        audit_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

        return {
            "ok": True,
            "step_run_id": step_run_id,
            "approver": approver,
            "approved_at": now_iso,
            "audit_id": audit_id,
            "reason": reason,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "step_run_id": step_run_id}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="SDC ISSO Gate Simulation")
    parser.add_argument("--step-run", default="demo-run-step-04",
                        help="sdc_workflow_step_runs ID to approve")
    parser.add_argument("--approver", default="isso-demo@agency.gov")
    parser.add_argument("--reason", default="demo-auto-approve")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    sys.path.insert(0, str(_ROOT))
    result = approve_demo(args.step_run, approver=args.approver, reason=args.reason)

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        if result.get("ok"):
            print(f"Approved: {result['step_run_id']} by {result['approver']}")
            print(f"Audit ID: {result['audit_id']}")
        else:
            print(f"Error: {result.get('error')}")
            sys.exit(1)


if __name__ == "__main__":
    main()
