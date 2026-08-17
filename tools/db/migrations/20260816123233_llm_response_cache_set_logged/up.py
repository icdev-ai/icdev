#!/usr/bin/env python3
# CUI // SP-CTI
"""Make llm_response_cache LOGGED — it is the savings ledger, not just a cache.

WHY. tools/cache_savings/savings.py computes every number on the dashboard's LLM
Prompt Cache card — total entries, total hits, hit rate, dollars saved — with
``FROM llm_response_cache`` and nothing else. There is no separate savings table.

The table was created UNLOGGED (tools/llm/response_cache.py::_PG_DDL), and
PostgreSQL TRUNCATES an unlogged table on crash recovery. So any unclean
shutdown silently reset a cumulative business metric to $0.0000 with no record
that it had ever been anything else. Observed 2026-08-16: the card read 0
entries / 0.0% / $0.0000 on a platform that had been routing LLM traffic for
months, and the card's own explanation correctly said the cache was cold.

The optimisation bought nothing to offset that. Writes happen once per cache
MISS — once per real LLM API call, which takes seconds. Benchmarked on this
deployment, 400 inserts of a 2KB body: UNLOGGED 0.482 ms/insert, LOGGED
0.446 ms/insert. LOGGED measured marginally faster; the difference is noise,
which is the point — there is no throughput here to protect.

PostgreSQL only. SQLite has no LOGGED/UNLOGGED concept: its table is already
durable, so this is a no-op there rather than an error. A raw up.sql would have
run on both and failed on SQLite, which is why this is a .py.

Idempotent: SET LOGGED on an already-logged table is accepted by PostgreSQL, and
the persistence is checked first anyway so a re-run reports accurately.
"""
from __future__ import annotations


def _is_postgres(conn) -> bool:
    """True when this connection is PostgreSQL, however it is wrapped."""
    backend = getattr(conn, "backend", None) or getattr(conn, "_backend", None)
    if isinstance(backend, str):
        return backend.lower().startswith("postgres")
    # Fall back to asking the server; SQLite has no such function.
    try:
        conn.execute("SELECT version()").fetchone()
        return True
    except Exception:  # noqa: BLE001 — sqlite3 raises here, which is the answer
        return False


def up(conn) -> dict:
    """Convert llm_response_cache to a LOGGED table on PostgreSQL."""
    if not _is_postgres(conn):
        return {"status": "skipped", "reason": "not postgresql; sqlite tables are durable"}

    row = conn.execute(
        "SELECT relpersistence FROM pg_class WHERE relname = 'llm_response_cache'"
    ).fetchone()
    if row is None:
        # The table is created lazily by response_cache.py on first use, and it
        # now creates a LOGGED one. Nothing to convert.
        return {"status": "skipped", "reason": "llm_response_cache does not exist yet"}

    persistence = (dict(row) if not isinstance(row, (tuple, list)) else {"relpersistence": row[0]})[
        "relpersistence"
    ]
    if persistence == "p":
        return {"status": "skipped", "reason": "already LOGGED"}

    conn.execute("ALTER TABLE llm_response_cache SET LOGGED")
    return {"status": "applied", "was": persistence, "now": "p"}
