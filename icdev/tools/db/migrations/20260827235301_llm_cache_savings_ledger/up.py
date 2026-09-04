#!/usr/bin/env python3
# CUI // SP-CTI
"""A DURABLE record of what the response cache actually saved (cch-obs-07).

THE DEFECT. Every savings number on /cache-savings is derived live `FROM llm_response_cache`
— there is no savings table. So the figure is CUMULATIVE in intent and VOLATILE in fact: a
row's contribution disappears when the row does, and rows disappear routinely.

  * `ttl_seconds: 3600` — an entry is gone an hour after it is written
  * `_evict_lru` deletes `ORDER BY hit_count ASC, created_at ASC` past `max_entries`
  * `invalidate()` deletes by function / model / everything

savings.py's own header identifies exactly this shape for a different cause — the table was
UNLOGGED and PostgreSQL truncates unlogged tables on crash recovery, which "did not merely
drop cached responses (fine, they regenerate), it reset a CUMULATIVE metric to $0.0000 with
no record it had ever been anything else." Migration 20260816123233 made the table LOGGED and
fixed the RESTART half. Expiry and eviction do the same thing on an ordinary day and were
left.

Observed 2026-08-27: the panel reads `$0.0000` and `0 / 15` after previously showing a dollar
figure. The 15 surviving rows have never been re-read, and whatever was re-read before has
aged out — taking its savings with it.

WHAT THIS TABLE IS. One append-only row per cache HIT: the call that did not happen. Summing
it gives a cumulative figure that survives expiry, eviction, invalidation and restart, because
it is no longer reconstructed from a cache — and a cache is by definition allowed to forget.

`usd_saved` IS NULLABLE ON PURPOSE. A local Ollama call has no bill and a Claude subscription
call has no per-token price; a 0.0 there would report a working cache as a failed one, which
is the defect the per-provider split (cch-obs-01) exists to remove. `usd_basis` records WHICH
of those a NULL means. `tokens_saved_*` are always real, whoever served the call.

APPEND-ONLY. Registered in APPEND_ONLY_TABLES; a correction is a new row, never an UPDATE.
"""

DESCRIPTION = "llm_cache_savings_ledger — durable record of avoided LLM calls"

TABLE = "llm_cache_savings_ledger"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_exists(conn, table: str) -> bool:
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=%s",
            (table,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s",
            (table,),
        ).fetchone()
    return row is not None


_PG_DDL = """
CREATE TABLE llm_cache_savings_ledger (
    id                  BIGSERIAL PRIMARY KEY,
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    function            TEXT NOT NULL DEFAULT '',
    model_id            TEXT NOT NULL DEFAULT '',
    provider            TEXT NOT NULL DEFAULT '',
    tokens_saved_input  INTEGER NOT NULL DEFAULT 0,
    tokens_saved_output INTEGER NOT NULL DEFAULT 0,
    usd_saved           DOUBLE PRECISION,
    usd_basis           TEXT NOT NULL DEFAULT 'unpriced',
    classification      TEXT NOT NULL DEFAULT 'CUI // SP-CTI'
)
"""

_SQLITE_DDL = """
CREATE TABLE llm_cache_savings_ledger (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at         TEXT NOT NULL,
    function            TEXT NOT NULL DEFAULT '',
    model_id            TEXT NOT NULL DEFAULT '',
    provider            TEXT NOT NULL DEFAULT '',
    tokens_saved_input  INTEGER NOT NULL DEFAULT 0,
    tokens_saved_output INTEGER NOT NULL DEFAULT 0,
    usd_saved           REAL,
    usd_basis           TEXT NOT NULL DEFAULT 'unpriced',
    classification      TEXT NOT NULL DEFAULT 'CUI // SP-CTI'
)
"""


def up(conn):
    if _table_exists(conn, TABLE):
        return
    conn.execute(_PG_DDL if _is_pg(conn) else _SQLITE_DDL)
    # The cumulative read is a SUM over a time range; the write is an append. Index the one
    # column both care about.
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_occurred ON {TABLE} (occurred_at)"
    )


def down(conn):
    """Deliberately a no-op — this table is the ONLY durable record of what was saved.

    Dropping it recreates the very defect the migration exists to fix, and unlike a cache
    its contents cannot be regenerated: the avoided calls already did not happen.
    """
    return
