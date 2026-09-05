#!/usr/bin/env python3
# CUI // SP-CTI
"""Record prompt-cache tokens on ai_telemetry — the per-call LLM ledger.

WHY. ``LLMResponse`` has carried ``cache_creation_input_tokens`` and
``cache_read_input_tokens`` since D-CACHE-10, and four provider adapters
populate them (Anthropic, Bedrock, Azure OpenAI, OpenAI). Nothing durable ever
recorded them per call. Measured on the live board 2026-08-16:

    ai_telemetry        5,838 rows   input/output/thinking tokens, no cache columns
    module_budget_usage    19 rows   one summed ``tokens`` figure, no cache columns
    llm_gateway_audit       0 rows
    agent_token_usage      14 rows
    llm_response_cache    113 rows   HAS the columns — but only for responses that
                                     were themselves response-cached, a subset of
                                     a subset, and that table is already the
                                     response cache AND the savings ledger (#1725)

So every claim about prompt caching on this platform was unfalsifiable. Azure
served cached tokens and discarded the count for its entire life and nothing
went red.

WHERE. ``ai_telemetry`` is the ONE place that already knows a call happened:
every router path — the main chain, ``invoke_for_role``, and both chain
orchestrator aggregations — funnels through ``LLMRouter._log_telemetry``, which
is the sole writer. Adding two columns here keeps a single writer. A second
independent ledger would drift from this one, which is the defect #1725 had to
work around one table over.

DEFAULT 0, NOT NULL. Absent and zero must not be the same value: a provider
that stops caching has to look different from one that was never asked. Every
row written from here on carries a measured 0 rather than a NULL meaning
"nobody looked". Rows predating this migration back-fill to 0, which is a known
and acknowledged floor — they are distinguishable by ``logged_at``.

Not a raw up.sql: PostgreSQL takes ``ADD COLUMN IF NOT EXISTS`` and SQLite does
not, so one statement cannot serve both. The column-existence probe is done
against the LIVE schema (``information_schema.columns`` / ``PRAGMA
table_info``) rather than assumed from the DDL, because ``CREATE TABLE IF NOT
EXISTS`` never alters an existing table and this table has already drifted from
tools/db/init_icdev_db.py once (``bridge_bypassed``, migration 185).
"""
from __future__ import annotations

MIGRATION_NAME = "ai_telemetry_cache_tokens"

TABLE = "ai_telemetry"
COLUMNS = (
    ("cache_creation_input_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("cache_read_input_tokens", "INTEGER NOT NULL DEFAULT 0"),
)


def _is_postgresql(conn) -> bool:
    """True when *conn* talks to PostgreSQL, however it is wrapped.

    The backend attribute is checked FIRST and deliberately. The type-name
    sniff below is the house pattern, but it answers False for the object the
    migration runner actually hands over: ``tools.db.storage.StorageConnection``
    wrapping psycopg2 has a ``__module__`` of ``tools.db.storage`` and a repr
    containing neither "psycopg2" nor "postgresql". Verified against the live
    PostgreSQL board 2026-08-16 — the sniff alone returned False and sent this
    migration down the SQLite branch.

    That branch happened to succeed only because StorageConnection REWRITES
    ``sqlite_master`` and ``PRAGMA table_info`` into their PostgreSQL
    equivalents; it also logged a "bare ? placeholder" warning on every probe.
    Relying on that is relying on the compatibility shim staying exhaustive,
    and a raw psycopg2 connection would have raised outright. Ask the object
    what it is instead.
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


def up(conn) -> dict:
    if not _table_exists(conn, TABLE):
        # A database whose ai_telemetry has never been created gets the columns
        # from init_icdev_db.py's DDL instead. Not an error.
        return {"status": "skipped", "reason": f"{TABLE} does not exist"}

    added: list[str] = []
    skipped: list[str] = []
    for column, col_type in COLUMNS:
        if _column_exists(conn, TABLE, column):
            skipped.append(column)
            continue
        conn.execute(
            f"ALTER TABLE {TABLE} ADD COLUMN {column} {col_type}"  # nosec B608
        )
        added.append(column)

    conn.commit()
    return {"status": "applied", "added": added, "skipped": skipped}
