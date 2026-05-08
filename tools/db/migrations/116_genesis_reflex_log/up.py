#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 116 — genesis_reflex_log table.

Creates:
  genesis_reflex_log(id, reflex_name, ran_at, result_json)
  idx_grl_reflex_name ON genesis_reflex_log(reflex_name)
  idx_grl_ran_at      ON genesis_reflex_log(ran_at)

Append-only NIST AU table. Records each Genesis reflex execution with its
result payload. Used by cyber_feed_refresh and any other reflex that needs
cooldown tracking.

Idempotent: CREATE TABLE IF NOT EXISTS.
"""

MIGRATION_ID = "116"
MIGRATION_NAME = "genesis_reflex_log"
DESCRIPTION = "Create genesis_reflex_log append-only reflex run audit table"

_DDL = """
CREATE TABLE IF NOT EXISTS genesis_reflex_log (
    id           TEXT PRIMARY KEY,
    reflex_name  TEXT NOT NULL,
    ran_at       TEXT NOT NULL,
    result_json  TEXT
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_grl_reflex_name ON genesis_reflex_log (reflex_name)",
    "CREATE INDEX IF NOT EXISTS idx_grl_ran_at       ON genesis_reflex_log (ran_at)",
]


def up(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()

    conn.execute(_DDL)
    for idx in _INDEXES:
        conn.execute(idx)
    conn.commit()
    return {"status": "applied", "actions": ["genesis_reflex_log_table", "indexes_created"]}


def down(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS genesis_reflex_log")
    conn.commit()
    return {"status": "rolled_back"}


if __name__ == "__main__":
    result = up()
    print(result)
