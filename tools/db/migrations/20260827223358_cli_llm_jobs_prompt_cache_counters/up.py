#!/usr/bin/env python3
# CUI // SP-CTI
"""Carry Claude Code's prompt-cache counters through the CLI bridge (cch-obs-04).

Claude Code's result JSON reports `cache_read_input_tokens` and
`cache_creation_input_tokens`. `cli_bridge/subprocess_backend.py` parsed `usage` and took
only input/output, so `cli_llm_jobs` had nowhere to put them, the LLMResponse never carried
them, and `ai_telemetry.cache_read_input_tokens` recorded 0 for all 626 claude-cli calls.
The cache dashboard then classified the provider `unreported` — "the transport reports no
counters" — which was a statement about THIS PIPELINE, not about the transport.

WHY up.py AND NOT up.sql. `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` is PostgreSQL-only.
`tests/conftest.py` forces `ICDEV_STORAGE_BACKEND=sqlite`, and SQLite rejects that clause
outright — a migration written that way passes on the live board and breaks every test run.

BACKEND DETECTION IS `conn._backend`, NOT THE CONNECTION'S CLASS. A first draft sniffed
`type(conn).__module__` for "psycopg", and that returns FALSE on a real PostgreSQL
connection: `tools/db/storage.py` hands back a WRAPPER, so the module name is storage's.
Measured against the live PG board — it took the SQLite branch and only survived because
`translate_sql` rewrote the query, with a warning. `getattr(conn, "_backend", "sqlite")` is
what `storage.is_pg` itself uses and what the existing column-adding migrations use.

DEFAULT 0, NOT NULL. These are COUNTS from a provider now DECLARED to report them
(args/cache_effectiveness.yaml → `reports_cache_tokens: true`), so a zero is a real
measurement — "no cache hit on this call" — not an absence. Rows written before this
migration backfill to 0 and are indistinguishable from a measured zero; acceptable because
the bridge has been disabled since 2026-07-29 and those rows predate any such claim.
"""

DESCRIPTION = "Persist Claude Code prompt-cache counters on cli_llm_jobs"

COLUMNS = ("cache_read_input_tokens", "cache_creation_input_tokens")
TABLE = "cli_llm_jobs"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_exists(conn, table: str) -> bool:
    """The bridge's job table is created at runtime by its own init path, so a
    migrate-only fresh database legitimately lacks it. Skip rather than abort the
    chain — the same self-skip the fa_*/ttx_* canvas migrations use."""
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
    for column in COLUMNS:
        if _has_column(conn, TABLE, column):
            continue
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {column} INTEGER DEFAULT 0")


def down(conn):
    """Deliberately a no-op.

    SQLite before 3.35 cannot DROP COLUMN at all, and dropping a counter destroys evidence
    that cost a defect to start collecting. The columns default to 0 and nothing reads them
    once the code is rolled back.
    """
    return
