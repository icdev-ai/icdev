#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 181: EQO — append-only centralized_logs table (eqo-log-01).

Creates the global centralized log sink used by the EQO logging pipeline
(tail-ingest -> centralized_logs, 7d retention, autofix bridge). This is a
GLOBAL table (not a canvas table): it carries tenant_id + classification so the
RLS-aware get_connection() applies the standard row-level predicate. The
migration runner provides that RLS-aware connection on PostgreSQL deployments;
on SQLite it hands us a plain sqlite3 connection.

centralized_logs is APPEND-ONLY (NIST AU): log rows are immutable evidence and
must never be UPDATEd or DELETEd individually. Retention is enforced by bulk
time-window pruning, not row mutation. Registered in APPEND_ONLY_TABLES in
.claude/hooks/pre_tool_use.py.

DDL is SQLite-flavored; the migration runner's SQL translator adapts it for
PostgreSQL. Canonical schema is also mirrored in tools/db/init_icdev_db.py so a
fresh icdev.db carries the table without replaying migrations.
"""

DDL = [
    """
    CREATE TABLE IF NOT EXISTS centralized_logs (
        id              TEXT PRIMARY KEY,
        ts              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        component       TEXT NOT NULL,
        level           TEXT NOT NULL DEFAULT 'INFO',
        message         TEXT NOT NULL DEFAULT '',
        trace_id        TEXT,
        session_id      TEXT,
        classification  TEXT NOT NULL DEFAULT 'CUI',
        tenant_id       TEXT NOT NULL DEFAULT 'default',
        extra_json      TEXT
    )
    """,
]

INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_centralized_logs_component ON centralized_logs(component)",
    "CREATE INDEX IF NOT EXISTS idx_centralized_logs_ts ON centralized_logs(ts)",
    "CREATE INDEX IF NOT EXISTS idx_centralized_logs_level ON centralized_logs(level)",
]


def up(conn):
    """Create centralized_logs + its indexes on the supplied connection."""
    for ddl in DDL:
        conn.execute(ddl)
    for idx in INDEX_DDL:
        conn.execute(idx)
    # The runner commits after up() returns; commit defensively too so the
    # table is durable even when run standalone.
    try:
        conn.commit()
    except Exception:
        pass
    print("[migration 181] centralized_logs table created (append-only, RLS-aware)")


def down(conn):
    """Drop centralized_logs (reverses up)."""
    conn.execute("DROP TABLE IF EXISTS centralized_logs")
    try:
        conn.commit()
    except Exception:
        pass
    print("[migration 181] centralized_logs table dropped")
