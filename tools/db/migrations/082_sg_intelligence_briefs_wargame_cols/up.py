#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 082 — Add wargame export columns to sg_intelligence_briefs.

Adds:
  conflict_id   TEXT — FK reference to the originating wargame (sg_wargames.id)
  classification TEXT NOT NULL DEFAULT 'CUI // SP-CTI' — handling caveat
"""
from tools.db.storage import get_connection, is_pg

MIGRATION_ID = "082"
MIGRATION_NAME = "sg_intelligence_briefs_wargame_cols"
DESCRIPTION = "Add conflict_id and classification columns to sg_intelligence_briefs"

_ALTER_COLS = [
    ("conflict_id",    "TEXT"),
    ("classification", "TEXT NOT NULL DEFAULT 'CUI // SP-CTI'"),
]

_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_sg_ib_conflict_id ON sg_intelligence_briefs(conflict_id)",
]


def _col_exists(conn, col: str) -> bool:
    if is_pg():
        row = conn.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name='sg_intelligence_briefs' AND column_name=%s",
            (col,),
        ).fetchone()
        return (row[0] if row else 0) > 0
    rows = conn.execute("PRAGMA table_info(sg_intelligence_briefs)").fetchall()
    return any((r[1] if isinstance(r, tuple) else r["name"]) == col for r in rows)


def up(conn=None) -> None:
    conn = get_connection()
    try:
        for col, defn in _ALTER_COLS:
            if not _col_exists(conn, col):
                conn.execute(
                    f"ALTER TABLE sg_intelligence_briefs ADD COLUMN {col} {defn}"  # nosec B608
                )
                print(f"Migration 082: added column {col}")
            else:
                print(f"Migration 082: column {col} already exists — skipped")
        for idx in _INDICES:
            try:
                conn.execute(idx)
            except Exception:
                pass
        conn.commit()
        print("Migration 082 up: sg_intelligence_briefs_wargame_cols complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
