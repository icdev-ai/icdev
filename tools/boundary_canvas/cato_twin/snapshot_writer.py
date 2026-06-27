#!/usr/bin/env python3
# CUI // SP-CTI
"""cATO Twin Snapshot Writer.

Freezes the full cross-framework compliance state into compliance_twin_snapshots
at the end of an assessor run (or on-demand). Each call produces an immutable
snapshot_id that can be replayed, diffed, and queried by the IQE engine.

Schema (migration 027):
  compliance_twin_snapshots  — one row per (snapshot_id, project, framework, control)
  compliance_twin_violations — violation rows for not_satisfied / partially_satisfied
  compliance_twin_runs       — summary row per snapshot execution

Design invariants:
  - snapshot_id is globally unique (UUID4)
  - Violations table is append-only (NIST AU) — no UPDATE/DELETE
  - Runs table records both started_at and completed_at for latency tracking
  - Caller may pass an existing conn (for tests) or let the module open its own
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

_STATUS_TO_SCORE = {
    "satisfied": 1.0,
    "partially_satisfied": 0.5,
    "not_satisfied": 0.0,
    "not_applicable": None,
    "not_assessed": None,
    "risk_accepted": 0.5,
}

# Violation type emitted per status
_STATUS_TO_VIOLATION: Dict[str, Optional[str]] = {
    "not_satisfied": "not_satisfied",
    "partially_satisfied": "partially_satisfied",
}

# Severity derived from control family prefix (coarse heuristic)
_FAMILY_SEVERITY = {
    "AC": "high",
    "AU": "high",
    "CA": "moderate",
    "CM": "high",
    "CP": "moderate",
    "IA": "high",
    "IR": "moderate",
    "MA": "moderate",
    "MP": "moderate",
    "PE": "moderate",
    "PL": "low",
    "PS": "moderate",
    "RA": "moderate",
    "SA": "moderate",
    "SC": "high",
    "SI": "high",
    "SR": "moderate",
}


def _violation_severity(control_id: str) -> str:
    family = control_id.split("-")[0].upper() if "-" in control_id else control_id[:2].upper()
    return _FAMILY_SEVERITY.get(family, "moderate")


def write_snapshot(
    project_id: str,
    framework: str,
    controls: List[Dict[str, Any]],
    *,
    triggered_by: str = "manual",
    assessor: str = "icdev-compliance-engine",
    conn=None,
) -> str:
    """Write a compliance twin snapshot.

    Args:
        project_id:   Project identifier (must exist in projects table).
        framework:    Framework name, e.g. 'FedRAMP Moderate', 'FedRAMP High'.
        controls:     List of dicts with keys:
                        control_id           — e.g. 'AC-2'
                        implementation_status — OSCAL status string
                        evidence_ref          — optional evidence path/ID
                        score                 — optional float 0.0-1.0
                        notes                 — optional string
        triggered_by: Who triggered this snapshot ('genesis_reflex', 'assessor', 'manual').
        assessor:     Identifier of the calling assessor.
        conn:         Optional existing DB connection (for tests). Opens its own if None.

    Returns:
        snapshot_id (str) — UUID4-based unique identifier for this snapshot.
    """
    snapshot_id = f"snap-{uuid.uuid4()}"
    started_at = datetime.now(timezone.utc).isoformat()

    _own_conn = conn is None
    if _own_conn:
        conn = get_connection()

    try:
        # ------------------------------------------------------------------
        # Insert snapshot rows
        # ------------------------------------------------------------------
        counts: Dict[str, int] = {
            "satisfied": 0,
            "partially_satisfied": 0,
            "not_satisfied": 0,
            "not_applicable": 0,
            "not_assessed": 0,
        }
        violation_rows = []

        for ctrl in controls:
            cid = ctrl["control_id"]
            status = ctrl.get("implementation_status", "not_assessed")
            evidence_ref = ctrl.get("evidence_ref")
            score = ctrl.get("score", _STATUS_TO_SCORE.get(status))
            notes = ctrl.get("notes")

            conn.execute(
                """INSERT INTO compliance_twin_snapshots
                   (snapshot_id, project_id, framework, control_id,
                    implementation_status, evidence_ref, score, assessor, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (snapshot_id, project_id, framework, cid, status,
                 evidence_ref, score, assessor, notes),
            )

            bucket = status if status in counts else "not_assessed"
            counts[bucket] = counts.get(bucket, 0) + 1

            # Emit violation for actionable statuses
            vtype = _STATUS_TO_VIOLATION.get(status)
            if vtype:
                violation_rows.append({
                    "snapshot_id": snapshot_id,
                    "project_id": project_id,
                    "framework": framework,
                    "control_id": cid,
                    "violation_type": vtype,
                    "severity": _violation_severity(cid),
                    "details": json.dumps({"evidence_ref": evidence_ref, "score": score}),
                })
            elif status == "not_assessed" or not evidence_ref:
                # Controls with no evidence also get a missing_evidence violation
                if status != "satisfied" and status != "not_applicable":
                    violation_rows.append({
                        "snapshot_id": snapshot_id,
                        "project_id": project_id,
                        "framework": framework,
                        "control_id": cid,
                        "violation_type": "missing_evidence",
                        "severity": _violation_severity(cid),
                        "details": json.dumps({"evidence_ref": None, "status": status}),
                    })

        # ------------------------------------------------------------------
        # Insert violations (append-only)
        # ------------------------------------------------------------------
        for vrow in violation_rows:
            conn.execute(
                """INSERT INTO compliance_twin_violations
                   (snapshot_id, project_id, framework, control_id,
                    violation_type, severity, details)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    vrow["snapshot_id"], vrow["project_id"], vrow["framework"],
                    vrow["control_id"], vrow["violation_type"], vrow["severity"],
                    vrow["details"],
                ),
            )

        # ------------------------------------------------------------------
        # Insert run summary
        # ------------------------------------------------------------------
        completed_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO compliance_twin_runs
               (snapshot_id, framework, project_id, triggered_by,
                started_at, completed_at, total_controls,
                satisfied, partially_satisfied, not_satisfied,
                not_applicable, not_assessed)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                snapshot_id, framework, project_id, triggered_by,
                started_at, completed_at, len(controls),
                counts["satisfied"], counts["partially_satisfied"],
                counts["not_satisfied"], counts["not_applicable"],
                counts["not_assessed"],
            ),
        )

        conn.commit()
        return snapshot_id

    finally:
        if _own_conn:
            conn.close()


def get_latest_snapshot_id(project_id: str, framework: str, conn=None) -> Optional[str]:
    """Return the most recent snapshot_id for a project+framework pair."""
    _own_conn = conn is None
    if _own_conn:
        conn = get_connection()
    try:
        row = conn.execute(
            """SELECT snapshot_id FROM compliance_twin_runs
               WHERE project_id = %s AND framework = %s
               ORDER BY started_at DESC LIMIT 1""",
            (project_id, framework),
        ).fetchone()
        return dict(row)["snapshot_id"] if row else None
    finally:
        if _own_conn:
            conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="cATO Twin Snapshot Writer — freeze compliance state"
    )
    parser.add_argument("--project-id", required=True, dest="project_id")
    parser.add_argument("--framework", required=True, help="e.g. 'FedRAMP Moderate'")
    parser.add_argument(
        "--controls-json", required=True, dest="controls_json",
        help="Path to JSON file with list of control dicts",
    )
    parser.add_argument("--triggered-by", default="manual", dest="triggered_by")
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args()

    controls = json.loads(Path(args.controls_json).read_text(encoding="utf-8"))
    snap_id = write_snapshot(
        project_id=args.project_id,
        framework=args.framework,
        controls=controls,
        triggered_by=args.triggered_by,
    )
    if args.json_out:
        print(json.dumps({"snapshot_id": snap_id, "status": "ok"}))
    else:
        print(f"Snapshot written: {snap_id}")


if __name__ == "__main__":
    main()
