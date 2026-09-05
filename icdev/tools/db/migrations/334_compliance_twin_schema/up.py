#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 027: cATO Twin canonical schema.

Creates three tables implementing the OSCAL-aligned compliance digital twin:

  compliance_twin_snapshots  — control × project × framework × timestamp
                               → implementation_status + evidence_ref
  compliance_twin_violations — new violations detected per snapshot run
  compliance_twin_runs       — one row per snapshot execution (run metadata)

Design rationale (BDC Phase 1, 2026-04-17):
  - Canonical row key: (snapshot_id, project_id, framework, control_id)
  - implementation_status follows OSCAL values used by base_assessor.py
  - Violations are append-only (NIST AU); never UPDATE/DELETE
  - poam_id FK on violations lets POA&M auto-generator link back
  - Idempotent via CREATE TABLE IF NOT EXISTS + column-existence guard
"""

import sqlite3

MIGRATION_ID = "027"
MIGRATION_NAME = "compliance_twin_schema"
DESCRIPTION = "cATO Twin: compliance_twin_snapshots + compliance_twin_violations + compliance_twin_runs"

_STATUS_VALUES = (
    "not_assessed",
    "satisfied",
    "partially_satisfied",
    "not_satisfied",
    "not_applicable",
    "risk_accepted",
)
_STATUS_CHECK = ", ".join(f"'{v}'" for v in _STATUS_VALUES)

_VIOLATION_TYPES = (
    "not_satisfied",
    "partially_satisfied",
    "missing_evidence",
    "stale_evidence",
    "overdue_assessment",
    "cross_framework_drift",
)
_VTYPE_CHECK = ", ".join(f"'{v}'" for v in _VIOLATION_TYPES)

_SEVERITY_VALS = ("critical", "high", "moderate", "low", "informational")
_SEV_CHECK = ", ".join(f"'{v}'" for v in _SEVERITY_VALS)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def up(conn: sqlite3.Connection) -> dict:
    """Apply migration 027 — create cATO Twin tables."""
    actions = []
    cur = conn.cursor()

    # ------------------------------------------------------------------
    # compliance_twin_snapshots
    # ------------------------------------------------------------------
    if not _table_exists(conn, "compliance_twin_snapshots"):
        cur.execute(f"""
            CREATE TABLE compliance_twin_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id     TEXT NOT NULL,
                project_id      TEXT NOT NULL,
                framework       TEXT NOT NULL,
                control_id      TEXT NOT NULL,
                implementation_status TEXT NOT NULL
                    CHECK(implementation_status IN ({_STATUS_CHECK})),
                evidence_ref    TEXT,
                score           REAL DEFAULT 0.0
                    CHECK(score >= 0.0 AND score <= 1.0),
                assessor        TEXT DEFAULT 'icdev-compliance-engine',
                notes           TEXT,
                classification  TEXT DEFAULT 'CUI // SP-CTI',
                created_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(snapshot_id, project_id, framework, control_id)
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_twin_snap_project "
            "ON compliance_twin_snapshots(project_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_twin_snap_framework "
            "ON compliance_twin_snapshots(framework)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_twin_snap_control "
            "ON compliance_twin_snapshots(control_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_twin_snap_status "
            "ON compliance_twin_snapshots(implementation_status)"
        )
        actions.append("created_compliance_twin_snapshots")
    else:
        actions.append("compliance_twin_snapshots_exists")

    # ------------------------------------------------------------------
    # compliance_twin_violations  (append-only per NIST AU)
    # ------------------------------------------------------------------
    if not _table_exists(conn, "compliance_twin_violations"):
        cur.execute(f"""
            CREATE TABLE compliance_twin_violations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id     TEXT NOT NULL,
                project_id      TEXT NOT NULL,
                framework       TEXT NOT NULL,
                control_id      TEXT NOT NULL,
                violation_type  TEXT NOT NULL
                    CHECK(violation_type IN ({_VTYPE_CHECK})),
                severity        TEXT DEFAULT 'moderate'
                    CHECK(severity IN ({_SEV_CHECK})),
                details         TEXT,
                poam_id         INTEGER,
                classification  TEXT DEFAULT 'CUI // SP-CTI',
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_twin_viol_snapshot "
            "ON compliance_twin_violations(snapshot_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_twin_viol_project "
            "ON compliance_twin_violations(project_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_twin_viol_control "
            "ON compliance_twin_violations(control_id)"
        )
        actions.append("created_compliance_twin_violations")
    else:
        actions.append("compliance_twin_violations_exists")

    # ------------------------------------------------------------------
    # compliance_twin_runs  (one row per snapshot execution)
    # ------------------------------------------------------------------
    if not _table_exists(conn, "compliance_twin_runs"):
        cur.execute("""
            CREATE TABLE compliance_twin_runs (
                snapshot_id         TEXT PRIMARY KEY,
                framework           TEXT NOT NULL,
                project_id          TEXT,
                triggered_by        TEXT DEFAULT 'genesis_reflex',
                started_at          TEXT NOT NULL,
                completed_at        TEXT,
                total_controls      INTEGER DEFAULT 0,
                satisfied           INTEGER DEFAULT 0,
                partially_satisfied INTEGER DEFAULT 0,
                not_satisfied       INTEGER DEFAULT 0,
                not_applicable      INTEGER DEFAULT 0,
                not_assessed        INTEGER DEFAULT 0,
                classification      TEXT DEFAULT 'CUI // SP-CTI',
                created_at          TEXT DEFAULT (datetime('now'))
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_twin_run_framework "
            "ON compliance_twin_runs(framework)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_twin_run_project "
            "ON compliance_twin_runs(project_id)"
        )
        actions.append("created_compliance_twin_runs")
    else:
        actions.append("compliance_twin_runs_exists")

    conn.commit()
    return {"status": "applied", "actions": actions}
