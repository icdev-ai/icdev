#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 014: Add missing columns to AI accountability tables.

Background: tools/compliance/accountability_manager.py writes columns
(`description`, `created_by`, `name`, `role`, `appointment_date`, `status`,
`grievance`, `summary`, `recommendation`, `last_assessed`, etc.) that the
canonical CREATE TABLE statements in tools/db/init_icdev_db.py did not
declare. Coherence checker (D-WF-8) flagged this as schema_code drift.

Resolution: extend the canonical schema to be a strict superset and ALTER
existing databases to add the missing columns. Idempotent — uses try/except
per ALTER so re-running is safe.
"""

import sqlite3

MIGRATION_ID = "014"
MIGRATION_NAME = "ai_accountability_canonical_columns"
DESCRIPTION = "Add missing columns to AI accountability tables (D-WF-8 schema_code fix)"


# (table, column, type_with_default)
_COLUMNS = [
    # ai_oversight_plans — accountability_manager writes description / created_by / updated_at
    ("ai_oversight_plans", "description", "TEXT DEFAULT ''"),
    ("ai_oversight_plans", "created_by", "TEXT DEFAULT ''"),
    ("ai_oversight_plans", "updated_at", "TEXT"),
    # ai_caio_registry — manager uses simple `name` / `role` / `appointment_date` / `status`
    ("ai_caio_registry", "name", "TEXT"),
    ("ai_caio_registry", "role", "TEXT DEFAULT 'CAIO'"),
    ("ai_caio_registry", "appointment_date", "TEXT"),
    ("ai_caio_registry", "status", "TEXT DEFAULT 'active'"),
    # ai_accountability_appeals — manager writes grievance / status / filed_at / resolved_at
    ("ai_accountability_appeals", "grievance", "TEXT DEFAULT ''"),
    ("ai_accountability_appeals", "status", "TEXT DEFAULT 'submitted'"),
    ("ai_accountability_appeals", "filed_at", "TEXT"),
    ("ai_accountability_appeals", "resolved_at", "TEXT"),
    # ai_ethics_reviews — manager writes summary / recommendation / status / submitted_at / reviewed_at
    ("ai_ethics_reviews", "summary", "TEXT DEFAULT ''"),
    ("ai_ethics_reviews", "recommendation", "TEXT DEFAULT ''"),
    ("ai_ethics_reviews", "status", "TEXT DEFAULT 'submitted'"),
    ("ai_ethics_reviews", "submitted_at", "TEXT"),
    ("ai_ethics_reviews", "reviewed_at", "TEXT"),
    # ai_reassessment_schedule — manager uses last_assessed alongside canonical last_completed
    ("ai_reassessment_schedule", "last_assessed", "TEXT"),
]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def up(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    added = 0
    skipped_existing = 0
    skipped_missing_table = 0
    for table, column, col_type in _COLUMNS:
        if not _table_exists(conn, table):
            skipped_missing_table += 1
            continue
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            added += 1
        except sqlite3.OperationalError as e:
            # Column already exists — idempotent
            if "duplicate column name" in str(e).lower():
                skipped_existing += 1
            else:
                raise
    conn.commit()
    return {
        "status": "applied",
        "added": added,
        "skipped_existing": skipped_existing,
        "skipped_missing_table": skipped_missing_table,
        "total": len(_COLUMNS),
    }
