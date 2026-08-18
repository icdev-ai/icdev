#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 20260817010532: create ``databridge_agent_access_log`` on PostgreSQL.

WHY THIS MIGRATION EXISTS
-------------------------

``tools/databridge/broker.py::_audit`` writes one append-only row per agent
fetch — allowed or denied — into ``databridge_agent_access_log``. On the live
PostgreSQL backend that table DOES NOT EXIST, so every one of those inserts
raised ``UndefinedTable``, and ``_audit`` swallowed the failure into a
``logger.warning``. Every external fetch was therefore UNAUDITABLE while the
call itself reported success. Verified 2026-08-17 against the live backend::

    SELECT COUNT(*) FROM databridge_agent_access_log
    -> UndefinedTable: relation "databridge_agent_access_log" does not exist

The table was never created because its only DDL — ``tools/db/init_icdev_db.py``
— is authored in SQLite syntax (``INTEGER PRIMARY KEY AUTOINCREMENT``,
``datetime('now')``). That path is the SQLite init fallback, not the primary
backend, so on a PG-primary deployment nothing ever ran it.

WHY up.py AND NOT up.sql
------------------------

One statement cannot be both PG-native and SQLite-valid here: ``BIGSERIAL`` and
``NOW()`` are not SQLite, and ``AUTOINCREMENT`` is not PostgreSQL. CLAUDE.md's
rule is that ``translate_sql`` is a thin init-fallback and must never be
load-bearing, so this migration does NOT hand it SQLite DDL and hope — it
branches explicitly on the connection's backend and carries a real DDL for each.

Note the branch reads ``conn._backend``, NOT ``conn._dialect``. A PostgreSQL
``StorageConnection`` has no ``_dialect`` attribute at all, so the
``getattr(conn, "_dialect", "sqlite")`` idiom copied through several older
migrations silently selects the SQLite DDL on a PG database — the same class of
mistake that left this table missing in the first place.

COLUMNS BEYOND THE ORIGINAL DDL
-------------------------------

``tenant_id`` is new. ``get_connection()`` injects ``tenant_id = %s`` into every
SELECT this table serves once a security context is set (see
``tools/security/row_security.py::inject_row_predicate``, which exempts only
system catalogs). Without the column, every read of the audit log would raise
``UndefinedColumn`` — an audit trail nobody can query. ``agent_session_events``
(20260816122036) carries it for exactly this reason.

``classification`` defaults to ``'CUI'``, the LABEL, not to the banner string
``'CUI // SP-CTI'`` the SQLite DDL used. The RLS predicate is
``classification IN (<set dominated by the caller's clearance>)`` and that set is
drawn from the label vocabulary — ``{PUBLIC, UNCLASSIFIED, CUI, SECRET, ECI,
TOP SECRET}``. A row labelled ``'CUI // SP-CTI'`` matches no member of it at any
clearance, so it would have been invisible to every caller: written, retained,
and unreadable. The banner belongs in a document header, not in a predicate
column.

APPEND-ONLY
-----------

Registered in ``APPEND_ONLY_TABLES`` in ``.claude/hooks/pre_tool_use.py``. No row
here is ever UPDATEd or DELETEd; a correction is a new row.
"""
from __future__ import annotations

from tools.db.storage import get_connection

_DDL_PG = """
CREATE TABLE IF NOT EXISTS databridge_agent_access_log (
    id                 BIGSERIAL   PRIMARY KEY,
    agent_id           TEXT        NOT NULL DEFAULT 'unknown',
    connector_name     TEXT        NOT NULL DEFAULT '',
    table_name         TEXT        NOT NULL DEFAULT '',
    decision           TEXT        NOT NULL DEFAULT 'denied'
                                   CHECK (decision IN ('allowed', 'denied')),
    reason             TEXT        NOT NULL DEFAULT '',
    rows_returned      INTEGER     NOT NULL DEFAULT 0,
    redactions_applied INTEGER     NOT NULL DEFAULT 0,
    tenant_id          TEXT        NOT NULL DEFAULT 'default',
    classification     TEXT        NOT NULL DEFAULT 'CUI',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS databridge_agent_access_log (
    id                 INTEGER     PRIMARY KEY AUTOINCREMENT,
    agent_id           TEXT        NOT NULL DEFAULT 'unknown',
    connector_name     TEXT        NOT NULL DEFAULT '',
    table_name         TEXT        NOT NULL DEFAULT '',
    decision           TEXT        NOT NULL DEFAULT 'denied'
                                   CHECK (decision IN ('allowed', 'denied')),
    reason             TEXT        NOT NULL DEFAULT '',
    rows_returned      INTEGER     NOT NULL DEFAULT 0,
    redactions_applied INTEGER     NOT NULL DEFAULT 0,
    tenant_id          TEXT        NOT NULL DEFAULT 'default',
    classification     TEXT        NOT NULL DEFAULT 'CUI',
    created_at         TEXT        NOT NULL DEFAULT (datetime('now'))
)
"""

# "Which connector does this agent keep being refused?" is the question the table
# exists to answer, so the denial-triage read is (agent_id, decision).
# The tenant index leads with tenant_id because RLS injects that predicate into
# every SELECT — the planner never sees the module's own SQL shape.
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_db_agent_access_agent "
    "ON databridge_agent_access_log (agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_db_agent_access_decision "
    "ON databridge_agent_access_log (decision)",
    "CREATE INDEX IF NOT EXISTS idx_db_agent_access_tenant "
    "ON databridge_agent_access_log (tenant_id, created_at)",
]

# The table pre-dates this migration on SQLite deployments (init_icdev_db.py
# created it there), and CREATE TABLE IF NOT EXISTS never alters an existing
# table — so tenant_id would be missing on exactly those databases while the
# INSERT in broker.py, and the RLS predicate, both expect it. Add it explicitly.
_BACKFILL_COLUMNS = [
    ("tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
    ("classification", "TEXT NOT NULL DEFAULT 'CUI'"),
]


def _existing_columns(conn, backend: str) -> set[str]:
    if backend == "postgresql":
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s",
            ("databridge_agent_access_log",),
        ).fetchall()
    else:
        rows = conn.execute("PRAGMA table_info(databridge_agent_access_log)").fetchall()
        return {str(r[1]) for r in rows}
    return {str(r[0]) for r in rows}


def up(conn=None) -> None:
    _own_conn = conn is None
    if _own_conn:
        conn = get_connection()
    try:
        backend = getattr(conn, "_backend", "sqlite")
        conn.execute(_DDL_PG if backend == "postgresql" else _DDL_SQLITE)
        for stmt in _INDEXES:
            conn.execute(stmt)

        present = _existing_columns(conn, backend)
        for column, spec in _BACKFILL_COLUMNS:
            if column not in present:
                conn.execute(
                    f"ALTER TABLE databridge_agent_access_log ADD COLUMN {column} {spec}"
                )

        if _own_conn:
            conn.commit()
        print(
            "[20260817010532_databridge_agent_access_log] up: "
            f"databridge_agent_access_log present on {backend}"
        )
    finally:
        if _own_conn:
            conn.close()


if __name__ == "__main__":
    up()
