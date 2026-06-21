#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 188 — genesis_outputs table.

Creates:
  genesis_outputs(id, reflex_name, output_type, output_ref, summary, created_at, classification)
  idx_go_reflex_name ON genesis_outputs(reflex_name)
  idx_go_created_at  ON genesis_outputs(created_at)

Append-only NIST AU event log. Records each genesis reflex output artifact
(e.g., weekly PPTX decks emitted by the Slides reflex). Queried by the
dashboard to surface the latest generated decks.

Idempotent: CREATE TABLE IF NOT EXISTS.
"""

MIGRATION_ID = "188"
MIGRATION_NAME = "genesis_outputs"
DESCRIPTION = "Create genesis_outputs append-only reflex output event log"

_DDL = """
CREATE TABLE IF NOT EXISTS genesis_outputs (
    id             TEXT PRIMARY KEY,
    reflex_name    TEXT NOT NULL,
    output_type    TEXT NOT NULL,
    output_ref     TEXT,
    summary        TEXT,
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    classification TEXT DEFAULT 'CUI'
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_go_reflex_name ON genesis_outputs (reflex_name)",
    "CREATE INDEX IF NOT EXISTS idx_go_created_at  ON genesis_outputs (created_at)",
]


def up(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()

    conn.execute(_DDL)
    for idx in _INDEXES:
        conn.execute(idx)
    conn.commit()
    return {"status": "applied", "actions": ["genesis_outputs_table", "indexes_created"]}


def down(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS genesis_outputs")
    conn.commit()
    return {"status": "rolled_back"}


if __name__ == "__main__":
    result = up()
    print(result)
