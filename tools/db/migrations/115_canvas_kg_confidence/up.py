#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 115 — canvas_kg_edges confidence column.

Adds to canvas_kg_edges:
  confidence  REAL  — edge confidence score [0.0, 1.0], nullable
"""

MIGRATION_ID = "115"
MIGRATION_NAME = "canvas_kg_confidence"
DESCRIPTION = "Add confidence REAL column to canvas_kg_edges"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _column_exists(conn, table: str, column: str) -> bool:
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = ? AND column_name = ?",
            (table, column),
        ).fetchone()
        return row is not None
    else:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # nosec B608
        return any((r[1] if isinstance(r, tuple) else dict(r).get("name")) == column for r in rows)


def up(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()

    actions = []

    if not _column_exists(conn, "canvas_kg_edges", "confidence"):
        try:
            conn.execute("ALTER TABLE canvas_kg_edges ADD COLUMN confidence REAL")
            actions.append("added_canvas_kg_edges.confidence")
        except Exception as exc:
            actions.append(f"skip_canvas_kg_edges.confidence: {exc}")
    else:
        actions.append("exists_canvas_kg_edges.confidence")

    conn.commit()
    return {"status": "applied", "actions": actions}


def down(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()
    # PostgreSQL does not support DROP COLUMN on older versions but is fine on PG 9+
    try:
        conn.execute("ALTER TABLE canvas_kg_edges DROP COLUMN IF EXISTS confidence")
        conn.commit()
    except Exception:
        pass
    return {"status": "rolled_back"}


if __name__ == "__main__":
    result = up()
    print(result)
