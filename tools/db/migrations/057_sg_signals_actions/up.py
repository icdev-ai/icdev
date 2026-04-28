# CUI // SP-CTI
"""Migration 057 — Add analyst action columns to sg_prioritized_signals.

Adds: is_read, promoted_to_kg, annotation
These columns support the /strategos/signals analyst workflow:
  - Mark-as-read, Promote-to-KG, Annotate, Send to Brief Generator.
"""
from tools.db.storage import get_connection, is_pg

MIGRATION_ID = "057"
MIGRATION_NAME = "sg_signals_actions"
DESCRIPTION = "Add analyst action columns (is_read, promoted_to_kg, annotation) to sg_prioritized_signals"

_ALTER_COLS = [
    ("is_read",        "INTEGER NOT NULL DEFAULT 0"),
    ("promoted_to_kg", "INTEGER NOT NULL DEFAULT 0"),
    ("annotation",     "TEXT"),
]

_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_sg_pri_read  ON sg_prioritized_signals(is_read)",
    "CREATE INDEX IF NOT EXISTS idx_sg_pri_promo ON sg_prioritized_signals(promoted_to_kg)",
]


def _col_exists(conn, col: str) -> bool:
    if is_pg():
        row = conn.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name='sg_prioritized_signals' AND column_name=%s",
            (col,),
        ).fetchone()
        return (row[0] if row else 0) > 0
    else:
        rows = conn.execute("PRAGMA table_info(sg_prioritized_signals)").fetchall()
        return any((r[1] if isinstance(r, tuple) else r["name"]) == col for r in rows)


def up(conn=None) -> None:
    conn = get_connection()
    try:
        for col, defn in _ALTER_COLS:
            if not _col_exists(conn, col):
                conn.execute(
                    f"ALTER TABLE sg_prioritized_signals ADD COLUMN {col} {defn}"
                )
                print(f"Migration 057: added column {col}")
            else:
                print(f"Migration 057: column {col} already exists — skipped")
        for idx in _INDICES:
            try:
                conn.execute(idx)
            except Exception:
                pass
        conn.commit()
        print("Migration 057 up: sg_signals_actions complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
