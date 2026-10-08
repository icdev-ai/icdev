# CUI // SP-CTI
"""Migration 20261008003850 rollback — remove aisg_audit's PG immutability.

Rolling this back makes the AISG audit trail mutable again on PostgreSQL
(NIST AU-9). It is a schema operation, not a routine one. Note that
``tools/aisg/db/init_db.py`` reinstalls the triggers the next time the canvas
initialises, so a lasting removal also needs that code changed.

SQLite: no-op (its triggers belong to init_db, not to this migration).
"""
from __future__ import annotations

_TAG = "[20261008003850_aisg_audit_pg_immutability]"

DOWN_STATEMENTS = (
    "DROP TRIGGER IF EXISTS aisg_audit_no_update ON aisg_audit",
    "DROP TRIGGER IF EXISTS aisg_audit_no_delete ON aisg_audit",
    "DROP FUNCTION IF EXISTS aisg_audit_immutable()",
)


def down(conn) -> dict:
    from tools.db.storage import is_pg

    if not is_pg(conn):
        print(f"{_TAG} SQLite: no-op")
        return {"status": "skipped", "reason": "sqlite"}
    row = conn.execute("SELECT to_regclass('aisg_audit') AS reg").fetchone()
    reg = None if row is None else (row["reg"] if hasattr(row, "keys") else row[0])
    # DROP TRIGGER ... ON <missing table> is an error even with IF EXISTS.
    statements = DOWN_STATEMENTS if reg is not None else DOWN_STATEMENTS[-1:]
    for stmt in statements:
        conn.execute(stmt)
    conn.commit()
    print(f"{_TAG} rolled back")
    return {"status": "rolled_back"}
