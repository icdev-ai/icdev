#!/usr/bin/env python3
# CUI // SP-CTI
"""cATO Twin POA&M Auto-Generator.

Generates POA&M entries directly from compliance_twin_violations for a given
snapshot. Integrates with the existing poam_items table so the generated items
are visible to the existing generate_poam() workflow.

Design:
  - One POAM item per unique (project_id, control_id) violation
  - Weakness ID keyed as 'TWIN-{framework_abbrev}-{control_id}' for dedup
  - Links back to the violation row via poam_id FK update (best-effort)
  - Returns a summary dict with new_items count for Genesis reflex reporting

Usage (CLI):
  python tools/boundary_canvas/cato_twin/poam_auto_generator.py \
      --snapshot-id snap-<uuid> --project-id proj-001

Usage (programmatic):
  from tools.boundary_canvas.cato_twin.poam_auto_generator import generate_from_violations
  result = generate_from_violations(snapshot_id, project_id, conn=conn)
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

from tools.logging.icdev_logger import get_logger
logger = get_logger("icdev.boundary_canvas.poam_auto_generator")


def _row_id(row: Any) -> Any:
    """Extract the ``id`` value from a RETURNING result row.

    Works across backends: psycopg2 DictRow and sqlite3.Row both support
    key access (``row["id"]``); a bare tuple falls back to positional access.
    """
    try:
        return row["id"]
    except (TypeError, KeyError, IndexError):
        return row[0]

_SEVERITY_DAYS = {
    "critical": 15,
    "high": 30,
    "moderate": 90,
    "low": 180,
    "informational": 365,
}

_FRAMEWORK_ABBREV = {
    "fedramp moderate": "FRM",
    "fedramp high": "FRH",
    "nist 800-53": "N53",
    "cmmc": "CMC",
    "hipaa": "HIP",
    "pci dss": "PCI",
    "soc 2": "SOC",
    "iso 27001": "ISO",
}

_CORRECTIVE_TEMPLATES = {
    "not_satisfied": (
        "Implement control {control_id} per {framework} requirements. "
        "Document implementation evidence and upload to the evidence repository."
    ),
    "partially_satisfied": (
        "Complete partial implementation of control {control_id} per {framework}. "
        "Review current evidence and close remaining gaps."
    ),
    "missing_evidence": (
        "Collect and upload evidence demonstrating {control_id} implementation "
        "in the {framework} authorization boundary."
    ),
    "stale_evidence": (
        "Refresh evidence for control {control_id} ({framework}). "
        "Evidence is older than the authorized freshness window."
    ),
    "overdue_assessment": (
        "Schedule and complete a compliance assessment for {control_id} "
        "under {framework}. Assessment is overdue."
    ),
    "cross_framework_drift": (
        "Synchronize {control_id} implementation across all applicable frameworks. "
        "A change in one framework has not been propagated to {framework}."
    ),
}


def _framework_abbrev(framework: str) -> str:
    return _FRAMEWORK_ABBREV.get(framework.lower(), framework[:3].upper())


def _weakness_id(framework: str, control_id: str) -> str:
    abbrev = _framework_abbrev(framework)
    safe_ctrl = control_id.replace(" ", "-").upper()
    return f"TWIN-{abbrev}-{safe_ctrl}"


def _milestone_date(severity: str) -> str:
    days = _SEVERITY_DAYS.get(severity, 90)
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


def _corrective_action(violation_type: str, control_id: str, framework: str) -> str:
    tmpl = _CORRECTIVE_TEMPLATES.get(violation_type, _CORRECTIVE_TEMPLATES["not_satisfied"])
    return tmpl.format(control_id=control_id, framework=framework)


def generate_from_violations(
    snapshot_id: str,
    project_id: str,
    *,
    conn=None,
) -> Dict[str, Any]:
    """Generate POA&M items from twin violations for a snapshot.

    Args:
        snapshot_id: Snapshot to generate POA&M for.
        project_id:  Project identifier.
        conn:        Optional existing DB connection (for tests).

    Returns:
        dict with keys: new_items, skipped_items, snapshot_id, errors
    """
    _own_conn = conn is None
    if _own_conn:
        conn = get_connection()

    try:
        # Load violations for this snapshot
        violations = conn.execute(
            """SELECT * FROM compliance_twin_violations
               WHERE snapshot_id = %s AND project_id = %s""",
            (snapshot_id, project_id),
        ).fetchall()

        if not violations:
            return {
                "new_items": 0,
                "skipped_items": 0,
                "snapshot_id": snapshot_id,
                "errors": [],
            }

        # Load existing POAM weakness IDs for dedup
        existing = {
            r["weakness_id"]
            for r in conn.execute(
                "SELECT weakness_id FROM poam_items WHERE project_id = %s",
                (project_id,),
            ).fetchall()
        }

        new_count = 0
        skipped_count = 0
        errors: list[str] = []

        for viol in violations:
            v = dict(viol)
            framework = v["framework"]
            control_id = v["control_id"]
            violation_type = v["violation_type"]
            severity = v.get("severity", "moderate")

            weakness_id = _weakness_id(framework, control_id)

            if weakness_id in existing:
                skipped_count += 1
                continue

            corrective = _corrective_action(violation_type, control_id, framework)
            milestone = _milestone_date(severity)

            # RETURNING id is PG-native and correct on both backends. cur.lastrowid
            # is UNSAFE on psycopg2 — it returns the table OID (or 0), not the
            # serial PK — which would write a wrong poam_id FK below.
            cur = conn.execute(
                """INSERT INTO poam_items
                   (project_id, weakness_id, weakness_description, severity,
                    source, control_id, status, corrective_action,
                    milestone_date, responsible_party, resources_required)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    project_id,
                    weakness_id,
                    f"{framework} — Control {control_id} violation: {violation_type}",
                    severity if severity in ("critical", "high", "moderate", "low") else "moderate",
                    f"cATO Twin Snapshot {snapshot_id}",
                    control_id,
                    "open",
                    corrective,
                    milestone,
                    "Security Engineering Team",
                    "Staff time, compliance tooling",
                ),
            )
            inserted = cur.fetchone()
            if inserted is None:
                # INSERT ... RETURNING must yield the new PK; a None row means the
                # insert did not land. Surface it — never silently continue.
                msg = (
                    f"POAM insert for weakness_id={weakness_id} returned no id "
                    f"(snapshot_id={snapshot_id}, project_id={project_id})"
                )
                logger.error(msg)
                errors.append(msg)
                continue

            new_poam_id = _row_id(inserted)
            existing.add(weakness_id)
            new_count += 1

            # Link violation → POAM. The violations table is append-only except
            # for this poam_id FK (a non-audit column). A failure here must NOT
            # be swallowed: the POAM item exists but the linkage is missing, so
            # the error is logged and surfaced in the returned result.
            try:
                conn.execute(
                    "UPDATE compliance_twin_violations SET poam_id = %s WHERE id = %s",
                    (new_poam_id, v["id"]),
                )
            except Exception as exc:
                msg = (
                    f"Failed to link violation id={v['id']} to poam_id={new_poam_id} "
                    f"(weakness_id={weakness_id}): {exc}"
                )
                logger.exception(msg)
                errors.append(msg)

        conn.commit()
        return {
            "new_items": new_count,
            "skipped_items": skipped_count,
            "snapshot_id": snapshot_id,
            "errors": errors,
        }

    finally:
        if _own_conn:
            conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Auto-generate POA&M items from cATO Twin violations"
    )
    parser.add_argument("--snapshot-id", required=True, dest="snapshot_id")
    parser.add_argument("--project-id", required=True, dest="project_id")
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args()

    result = generate_from_violations(args.snapshot_id, args.project_id)
    if args.json_out:
        print(json.dumps(result))
    else:
        print(f"New POAM items created: {result['new_items']}")
        print(f"Skipped (existing): {result['skipped_items']}")
        for err in result.get("errors", []):
            print(f"ERROR: {err}")


if __name__ == "__main__":
    main()
