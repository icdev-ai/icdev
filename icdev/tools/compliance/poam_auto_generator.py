#!/usr/bin/env python3
# CUI // SP-CTI
"""POA&M auto-generator from compliance_snapshots violations (dt-bdc-08).

Reads rows from ``compliance_snapshots`` where status is ``not_satisfied`` or
``partially_satisfied`` (OSCAL POA&M trigger condition), builds POA&M item
records, and inserts them into the ``poam_items`` table.

Design constraints:
  - ``compliance_snapshots`` is append-only (cATO audit trail).  This module
    never UPDATE/DELETE that table.
  - ``poam_items`` rows are keyed on ``weakness_id`` (= snapshot_id) per
    project to guarantee idempotency across repeated runs.
  - No LLM dependency.  All logic is deterministic.

NIST 800-53 control mappings: SA-11, CA-7, RA-5.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.compliance.poam_auto_generator")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Violation statuses that require a POA&M item.
_VIOLATION_STATUSES = frozenset({"not_satisfied", "partially_satisfied", "planned"})

# Remediation timelines (days) per severity.
_REMEDIATION_DAYS: dict[str, int] = {
    "critical": 15,
    "high": 30,
    "moderate": 90,
    "low": 180,
}

# Source tag inserted into poam_items.source so queries can filter by origin.
_SOURCE_TAG = "compliance_snapshots"


# ---------------------------------------------------------------------------
# Pure helpers (no DB dependency)
# ---------------------------------------------------------------------------

def severity_for_status(status: str) -> str:
    """Map a compliance snapshot status to a POA&M severity level.

    ``not_satisfied``      → high   (gap must be remediated within 30 days)
    ``partially_satisfied``→ moderate (partial coverage; 90-day window)
    ``planned``            → low    (acknowledged future control; 180 days)
    """
    mapping = {
        "not_satisfied": "high",
        "partially_satisfied": "moderate",
        "planned": "low",
    }
    return mapping.get(status, "moderate")


def milestone_date_for_severity(severity: str) -> str:
    """Return an ISO-8601 date string for the remediation milestone.

    Calculated as ``today + remediation_days[severity]`` in UTC.
    """
    days = _REMEDIATION_DAYS.get(severity, 90)
    target = datetime.now(timezone.utc) + timedelta(days=days)
    return target.strftime("%Y-%m-%d")


def build_poam_item(snapshot_row: dict[str, Any]) -> dict[str, Any]:
    """Build a POA&M item dict from a single compliance_snapshots row.

    The returned dict contains every column needed for a ``poam_items`` INSERT
    plus the extra BDC-context fields (snapshot_id, framework_id) for
    traceability.
    """
    status = snapshot_row["status"]
    severity = severity_for_status(status)
    control_id = snapshot_row["control_id"]
    framework_id = snapshot_row.get("framework_id", "")
    snapshot_id = snapshot_row["snapshot_id"]

    weakness_description = (
        f"{control_id} control not fully satisfied under {framework_id}"
    )
    corrective_action = (
        f"Implement or complete the {control_id} control "
        f"per {framework_id} requirements and document evidence."
    )

    return {
        # BDC traceability fields
        "snapshot_id": snapshot_id,
        "framework_id": framework_id,
        # Standard poam_items columns
        "project_id": snapshot_row["project_id"],
        "control_id": control_id,
        "weakness_id": f"SNAP-{snapshot_id}",
        "weakness_description": weakness_description,
        "severity": severity,
        "source": f"{_SOURCE_TAG}:{framework_id}",
        "status": "open",
        "corrective_action": corrective_action,
        "milestone_date": milestone_date_for_severity(severity),
        "responsible_party": "ISSO",
        "resources_required": "Staff time, testing environment",
        # Pass-through for reference
        "snapshot_status": status,
        "evidence_ref": snapshot_row.get("evidence_ref"),
        "taken_at": snapshot_row.get("taken_at"),
    }


# ---------------------------------------------------------------------------
# DB-layer helpers
# ---------------------------------------------------------------------------

def generate_poam_items(conn: Any) -> list[dict[str, Any]]:
    """Return POA&M item dicts for all violation rows in compliance_snapshots.

    Args:
        conn: A sqlite3 (or compatible) connection with compliance_snapshots.

    Returns:
        List of item dicts, one per violation row.  Empty if no violations.
    """
    # _VIOLATION_STATUSES is a hardcoded frozenset — no user input ever flows here.
    # The IN-list is parameterized (? placeholders); bandit B608 is a false positive.
    statuses = list(_VIOLATION_STATUSES)
    in_clause = ",".join(["%s"] * len(statuses))
    sql = f"SELECT snapshot_id, project_id, framework_id, control_id, status, evidence_ref, taken_at FROM compliance_snapshots WHERE status IN ({in_clause})"  # nosec B608 - placeholders only, no user input
    cursor = conn.execute(sql, statuses)
    cols = [d[0] for d in cursor.description]
    rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
    return [build_poam_item(r) for r in rows]


def _existing_project_controls(conn: Any, project_id: str) -> frozenset[tuple[str, str]]:
    """Return (project_id, control_id) pairs already in poam_items for *project_id*."""
    rows = conn.execute(
        "SELECT project_id, control_id FROM poam_items WHERE project_id = %s",
        (project_id,),
    ).fetchall()
    return frozenset((r[0], r[1]) for r in rows)


def _insert_poam_item(conn: Any, item: dict[str, Any]) -> None:
    """Insert a single item dict into poam_items."""
    conn.execute(
        """INSERT INTO poam_items
           (project_id, weakness_id, weakness_description, severity, source,
            control_id, status, corrective_action, milestone_date,
            responsible_party, resources_required)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            item["project_id"],
            item["weakness_id"],
            item["weakness_description"],
            item["severity"],
            item["source"],
            item["control_id"],
            item["status"],
            item["corrective_action"],
            item["milestone_date"],
            item["responsible_party"],
            item["resources_required"],
        ),
    )


def _write_audit_event(conn: Any, item: dict[str, Any]) -> None:
    """Write an audit_trail row for a generated POA&M item."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (%s, 'poam_generated', 'icdev-compliance-engine',
                       'auto_generate_poam', %s, 'CUI')""",
            (
                item["project_id"],
                json.dumps({
                    "control_id": item["control_id"],
                    "weakness_id": item["weakness_id"],
                    "snapshot_id": item["snapshot_id"],
                    "severity": item["severity"],
                }),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        # audit_trail may not exist in test DBs without full schema
        logger.warning("_write_audit_event: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def run_auto_generator(conn: Any) -> dict[str, Any]:
    """Run the POA&M auto-generation pipeline end-to-end.

    1. Fetch violation rows from compliance_snapshots.
    2. Skip items where (project_id, control_id) already exists in poam_items.
    3. Insert new items and write audit_trail rows.

    Args:
        conn: DB connection (sqlite3 or get_connection() compatible).

    Returns:
        Summary dict: ``{"items_scanned": int, "items_generated": int,
                         "items_skipped": int}``.
    """
    items = generate_poam_items(conn)
    generated = 0
    skipped = 0

    # Build a per-project cache of (project_id, control_id) pairs to avoid N+1 queries.
    existing_by_project: dict[str, frozenset[tuple[str, str]]] = {}

    for item in items:
        pid = item["project_id"]
        if pid not in existing_by_project:
            existing_by_project[pid] = _existing_project_controls(conn, pid)

        key = (pid, item["control_id"])
        if key in existing_by_project[pid]:
            skipped += 1
            continue

        _insert_poam_item(conn, item)
        _write_audit_event(conn, item)
        # Keep the local cache up-to-date within this batch.
        existing_by_project[pid] = existing_by_project[pid] | {key}
        generated += 1

    conn.commit()
    return {
        "items_scanned": len(items),
        "items_generated": generated,
        "items_skipped": skipped,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-generate POA&M items from compliance_snapshots violations"
    )
    parser.add_argument("--db", help="Override database path")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    try:
        from tools.db.storage import get_connection  # noqa: PLC0415

        conn = get_connection(db_path=args.db)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Could not open database: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        result = run_auto_generator(conn)
    finally:
        conn.close()

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print("POA&M auto-generator complete:")
        print(f"  Violations scanned : {result['items_scanned']}")
        print(f"  Items generated    : {result['items_generated']}")
        print(f"  Items skipped      : {result['items_skipped']} (already in DB)")


if __name__ == "__main__":
    main()
