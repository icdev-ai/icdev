"""
Migration 082 — ORBAT strength columns.

Adds (if not present):
  sg_wargames.conflict_id      TEXT  -- links a wargame to a conflict scenario
  sg_orbat_units.conflict_id   TEXT  -- scopes a unit to a conflict scenario
  sg_orbat_units.side          TEXT  -- 'blue' | 'red' | 'neutral'
  sg_orbat_units.strength_value INTEGER DEFAULT 0  -- numeric strength for aggregation
"""
from tools.db.storage import get_connection


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def up(conn=None) -> None:
    conn = get_connection()
    try:
        alterations = [
            ("sg_wargames",   "conflict_id",    "TEXT"),
            ("sg_orbat_units", "conflict_id",   "TEXT"),
            ("sg_orbat_units", "side",          "TEXT"),
            ("sg_orbat_units", "strength_value", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for table, col, col_def in alterations:
            if not _column_exists(conn, table, col):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")

        for idx_sql in (
            "CREATE INDEX IF NOT EXISTS idx_sg_orbat_conflict ON sg_orbat_units(conflict_id)",
            "CREATE INDEX IF NOT EXISTS idx_sg_orbat_side     ON sg_orbat_units(side)",
        ):
            try:
                conn.execute(idx_sql)
            except Exception:
                pass

        conn.commit()
        print(
            "Migration 082 up: conflict_id/side/strength_value added to "
            "sg_orbat_units; conflict_id added to sg_wargames."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    up()
