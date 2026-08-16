#!/usr/bin/env python3
# CUI // SP-CTI
"""Drop the prompt-cache token columns from ai_telemetry.

Running this re-opens the defect the migration closes: cache token counts stop
being recorded per call and every caching claim becomes unfalsifiable again.
The already-recorded counts are destroyed, not archived.

Deliberately self-contained rather than importing helpers from ``up.py``:
MigrationRunner loads each of these files with
``importlib.util.spec_from_file_location`` as a STANDALONE module, so there is
no package context and ``from .up import ...`` raises. Three duplicated helpers
beat a relative import that only works when a test imports it as a package.

SQLite gained DROP COLUMN in 3.35 (2021); an older build raises, which is
surfaced rather than swallowed.
"""
from __future__ import annotations

TABLE = "ai_telemetry"
COLUMNS = ("cache_creation_input_tokens", "cache_read_input_tokens")


def _is_postgresql(conn) -> bool:
    """True when *conn* talks to PostgreSQL, however it is wrapped.

    Backend attribute first — see the long note in ``up.py``: a
    ``StorageConnection`` wrapping psycopg2 answers False to the type-name
    sniff alone, which sends the migration down the SQLite branch.
    """
    for attr in ("_backend", "backend"):
        value = getattr(conn, attr, None)
        if isinstance(value, str) and value.lower().startswith("postgres"):
            return True
    return "psycopg2" in type(conn).__module__ or "postgresql" in str(type(conn)).lower()


def _table_exists(conn, table: str) -> bool:
    if _is_postgresql(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
            (table,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    return row is not None


def _column_exists(conn, table: str, column: str) -> bool:
    if _is_postgresql(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            (table, column),
        ).fetchone()
        return row is not None
    for info in conn.execute(f"PRAGMA table_info({table})").fetchall():  # nosec B608
        name = info[1] if isinstance(info, (list, tuple)) else info["name"]
        if name == column:
            return True
    return False


def down(conn) -> dict:
    if not _table_exists(conn, TABLE):
        return {"status": "skipped", "reason": f"{TABLE} does not exist"}

    dropped: list = []
    skipped: list = []
    for column in COLUMNS:
        if not _column_exists(conn, TABLE, column):
            skipped.append(column)
            continue
        conn.execute(f"ALTER TABLE {TABLE} DROP COLUMN {column}")  # nosec B608
        dropped.append(column)

    conn.commit()
    return {"status": "applied", "dropped": dropped, "skipped": skipped}
