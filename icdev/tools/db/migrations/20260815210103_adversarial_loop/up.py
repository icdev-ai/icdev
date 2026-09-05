#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 205 — loop_type and adversarial_enabled columns for kanban_tasks.

Adds two columns to support loop engineering patterns:
  loop_type          — 'deterministic' | 'non_deterministic'
  adversarial_enabled — 0 | 1  (enables maker/verifier adversarial review loop)

Safe to re-run: ALTER TABLE with error handling for already-existing columns.
"""

import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent.parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

MIGRATION_ID = "205"
MIGRATION_NAME = "adversarial_loop"
DESCRIPTION = "Add loop_type and adversarial_enabled columns to kanban_tasks"

# (column_name, sql_type, default_value_literal)
_COLUMNS = [
    ("loop_type",           "TEXT",    "'deterministic'"),
    ("adversarial_enabled", "INTEGER", "0"),
]


def up(conn=None) -> None:
    """Apply migration: add loop_type and adversarial_enabled to kanban_tasks."""
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
        _close = True
    else:
        _close = False

    for col_name, col_type, col_default in _COLUMNS:
        # Try PostgreSQL IF NOT EXISTS syntax first; fall back to plain ALTER
        # TABLE which raises OperationalError on SQLite when the column exists.
        for sql in [
            f"ALTER TABLE kanban_tasks ADD COLUMN IF NOT EXISTS "
            f"{col_name} {col_type} DEFAULT {col_default}",
            f"ALTER TABLE kanban_tasks ADD COLUMN "
            f"{col_name} {col_type} DEFAULT {col_default}",
        ]:
            try:
                conn.execute(sql)
                conn.commit()
                break
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

    print(f"[migration {MIGRATION_ID}] {DESCRIPTION} — applied")

    if _close:
        conn.close()


if __name__ == "__main__":
    up()
