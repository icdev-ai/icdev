#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 189 — genesis_phase_log table.

Creates:
  genesis_phase_log(id, design_id, phase, status, started_at, completed_at)
  idx_gpl_design_id    ON genesis_phase_log(design_id)
  idx_gpl_started_at   ON genesis_phase_log(started_at)
  idx_gpl_completed_at ON genesis_phase_log(completed_at)

Append-only NIST AU phase-transition log. Records each genesis design phase
change (start / completion). Queried by tools/notification_service/ to surface
phase history in milestone emails, digests, and handler notifications.

Idempotent: CREATE TABLE IF NOT EXISTS.
"""

MIGRATION_ID = "189"
MIGRATION_NAME = "genesis_phase_log"
DESCRIPTION = "Create genesis_phase_log append-only phase-transition event log"

_DDL = """
CREATE TABLE IF NOT EXISTS genesis_phase_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id    TEXT NOT NULL,
    phase        TEXT NOT NULL,
    status       TEXT NOT NULL,
    started_at   TEXT,
    completed_at TEXT
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_gpl_design_id    ON genesis_phase_log (design_id)",
    "CREATE INDEX IF NOT EXISTS idx_gpl_started_at   ON genesis_phase_log (started_at)",
    "CREATE INDEX IF NOT EXISTS idx_gpl_completed_at ON genesis_phase_log (completed_at)",
]


def up(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()

    conn.execute(_DDL)
    for idx in _INDEXES:
        conn.execute(idx)
    conn.commit()
    return {"status": "applied", "actions": ["genesis_phase_log_table", "indexes_created"]}


def down(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS genesis_phase_log")
    conn.commit()
    return {"status": "rolled_back"}


if __name__ == "__main__":
    result = up()
    print(result)
