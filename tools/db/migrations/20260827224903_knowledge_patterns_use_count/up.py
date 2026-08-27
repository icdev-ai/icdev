#!/usr/bin/env python3
# CUI // SP-CTI
"""Add the `use_count` column `knowledge_server` has always written to (cch-obs-05).

THE DEFECT, and it is one of the four failing Cortex rungs. `tools/mcp/knowledge_server.py`
orders by `use_count`, reads `row["use_count"]`, and increments it after a self-healing event
— but `knowledge_patterns` has never had that column. The canonical DDL in
`tools/db/init_icdev_db.py` declares `occurrence_count` and nothing else countable.

So every `search_knowledge` call raises `column "use_count" does not exist`, and the Cortex
`kb` backend fails on every resolution. Measured on the live board 2026-08-27, on the most
recent `cortex.resolve` carrying backend detail:

    used   : {currency}
    failed : {dic, graph, kb, rag}

CLAUDE.md records the same string twice as a known Cortex defect.

WHY ADD THE COLUMN RATHER THAN RENAME THE CODE TO `occurrence_count`. They are different
facts and the code is right to want both. `occurrence_count` is how often the PROBLEM was
seen; the increment at knowledge_server.py:332 fires after a pattern was USED to heal
something. Pointing the increment at `occurrence_count` would inflate a problem's incidence
every time its fix worked — a metric that rises when things go well.

This does NOT make the `kb` rung return results: `knowledge_patterns` holds 0 rows on this
deployment, so the rung will answer empty. That is the correct outcome and a real improvement
— an empty answer is a measurement, an exception is a broken backend, and only one of the two
can be told apart from "nothing matched".

up.py rather than up.sql because `ADD COLUMN IF NOT EXISTS` is PostgreSQL-only and
tests/conftest.py forces sqlite; backend detection is `conn._backend`, which is what
`storage.is_pg` uses — a connection-class sniff returns False on real PostgreSQL because
storage.py hands back a wrapper.
"""

DESCRIPTION = "knowledge_patterns.use_count — the column knowledge_server has always written"

TABLE = "knowledge_patterns"
COLUMN = "use_count"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_exists(conn, table: str) -> bool:
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=?",
            (table,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    return row is not None


def _has_column(conn, table: str, column: str) -> bool:
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=? AND column_name=?",
            (table, column),
        ).fetchone()
        return row is not None
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any((r[1] if not isinstance(r, dict) else r.get("name")) == column for r in rows)


def up(conn):
    if not _table_exists(conn, TABLE):
        return
    if _has_column(conn, TABLE, COLUMN):
        return
    # DEFAULT 0, not 1: a pattern that has never healed anything has been used zero times.
    # `occurrence_count` defaults to 1 because a pattern is created BY an occurrence; this
    # counter has no such founding event.
    conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} INTEGER DEFAULT 0")


def down(conn):
    """No-op: SQLite before 3.35 cannot DROP COLUMN, and the column is inert without the code."""
    return
