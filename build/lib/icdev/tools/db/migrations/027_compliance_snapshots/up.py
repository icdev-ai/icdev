#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 027: CREATE TABLE compliance_snapshots — BDC cATO Twin primitive.

Captures implementation status of a single compliance control for a project
under a specific framework at a point in time. Append-only per NIST AU-2 /
D6 audit-trail policy (NEVER UPDATE OR DELETE rows).

Indexes:
  idx_cs_project_framework  — per-project framework rollup (most common query)
  idx_cs_control_status     — cross-project control-status lookups (IQE)
  idx_cs_taken_at           — time-range slicing for continuous-monitoring trends

Idempotent: skips gracefully if table already exists.
"""

import sqlite3

MIGRATION_ID = "027"
MIGRATION_NAME = "compliance_snapshots"
DESCRIPTION = (
    "CREATE TABLE compliance_snapshots with 3 composite indexes for cATO Twin"
)

_VALID_STATUSES = (
    "satisfied",
    "not_satisfied",
    "partially_satisfied",
    "not_applicable",
    "planned",
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def up(conn: sqlite3.Connection) -> dict:
    actions = []

    if _table_exists(conn, "compliance_snapshots"):
        return {"status": "skipped", "reason": "table already exists"}

    status_check = ", ".join(f"'{s}'" for s in _VALID_STATUSES)
    conn.execute(
        f"""
        CREATE TABLE compliance_snapshots (
            snapshot_id  TEXT NOT NULL,
            project_id   TEXT NOT NULL,
            framework_id TEXT NOT NULL,
            control_id   TEXT NOT NULL,
            status       TEXT NOT NULL CHECK (status IN ({status_check})),
            evidence_ref TEXT,
            taken_at     TEXT NOT NULL,
            PRIMARY KEY (snapshot_id)
        )
        """
    )
    actions.append("table_created")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cs_project_framework "
        "ON compliance_snapshots(project_id, framework_id)"
    )
    actions.append("idx_cs_project_framework_created")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cs_control_status "
        "ON compliance_snapshots(control_id, status)"
    )
    actions.append("idx_cs_control_status_created")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cs_taken_at "
        "ON compliance_snapshots(taken_at)"
    )
    actions.append("idx_cs_taken_at_created")

    conn.commit()
    return {"status": "applied", "actions": actions}
