#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 319: add kanban_tasks.execution_seconds.

``_detect_execution_anomalies()`` in tools/genesis/reflexes/kanban.py runs
``SELECT execution_seconds, failure_count FROM kanban_tasks`` to derive an
adaptive per-task timeout ceiling from observed runtimes. The column does not
exist on PostgreSQL — the primary backend — so the query raised
``UndefinedColumn`` on every call. The function catches ``Exception`` and
returns ``{}``, which callers read as "not enough data" and silently fall back
to the static constants. The adaptive-timeout feature has therefore never run
in production, and its failure mode was total silence.

tools/kanban/init_db.py's ADD_COLUMNS list (the SQLite/dev path) never carried
this column either, so this is a genuine schema gap rather than PG drift from a
SQLite source of truth.

No backfill: runtimes for already-completed tasks were never recorded anywhere
and cannot be reconstructed. The column starts NULL and populates as tasks
complete; ``_detect_execution_anomalies`` already requires
``execution_seconds > 0`` and returns {} on an empty sample, so the adaptive
path stays dormant until real data exists rather than adapting to noise.
"""
from __future__ import annotations

from tools.db.storage import get_connection

COLUMN = "execution_seconds"
TABLE = "kanban_tasks"


def _has_column(conn, table: str, column: str) -> bool:
    """True when *column* already exists on *table*, on either backend."""
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            (table, column),
        ).fetchone()
        return row is not None
    except Exception:
        # SQLite has no information_schema — fall back to PRAGMA.
        try:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return any(dict(r).get("name") == column for r in rows)
        except Exception:
            return False


def up(conn=None) -> None:
    """Add the column if absent.

    ``conn`` is optional and caller-owned: bootstrap_pg.py and the migration
    runner pass their own connection, and closing it here would break the rest
    of their run. Only a connection opened locally is closed locally.
    """
    owned = conn is None
    conn = conn or get_connection()
    try:
        if _has_column(conn, TABLE, COLUMN):
            print(f"[319] {TABLE}.{COLUMN} already present — no-op")
            return
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} REAL")
        try:
            conn.commit()
        except Exception:
            pass
        print(f"[319] added {TABLE}.{COLUMN}")
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    up()
