# CUI // SP-CTI
"""Cortex canvas database schema initialization.

Three canvas-owned tables back the /cortex dashboard:

  cortex_sessions        — a canvas chat/agent session (one row per launch)
  cortex_audit           — append-only governance/facade audit trail
  cortex_search_history  — one row per unified-search / ask invocation

All three carry ``tenant_id`` + ``classification`` and are read through
``get_connection()`` (NOT ``get_canvas_connection()``) so the global RLS
predicate applies — the canvas surfaces user data, so read-down/tenant scoping
must hold from day one (see CLAUDE.md RLS rules + the DIC init pattern).

Idempotent: every statement is ``CREATE TABLE/INDEX IF NOT EXISTS``. Runtime
call sites (blueprint ``before_request``, IQE adapters) tolerate the tables not
existing yet, so a fresh checkout that has not run this init still boots.
"""
from __future__ import annotations

import os

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS cortex_sessions (
    session_id      TEXT        PRIMARY KEY,
    user_id         TEXT        DEFAULT '',
    mode            TEXT        DEFAULT 'ask',
    domain          TEXT        DEFAULT 'general',
    title           TEXT        DEFAULT '',
    status          TEXT        DEFAULT 'active',
    classification  TEXT        DEFAULT 'CUI',
    tenant_id       TEXT        DEFAULT 'default',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cortex_sessions_user ON cortex_sessions(user_id);

CREATE TABLE IF NOT EXISTS cortex_audit (
    audit_id        TEXT        PRIMARY KEY,
    session_id      TEXT        DEFAULT '',
    facade          TEXT        NOT NULL,
    outcome         TEXT        DEFAULT 'pass',
    blocked         BOOLEAN     DEFAULT FALSE,
    detail          TEXT        DEFAULT '',
    classification  TEXT        DEFAULT 'CUI',
    tenant_id       TEXT        DEFAULT 'default',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cortex_audit_session ON cortex_audit(session_id);

CREATE TABLE IF NOT EXISTS cortex_search_history (
    query_id        TEXT        PRIMARY KEY,
    session_id      TEXT        DEFAULT '',
    user_id         TEXT        DEFAULT '',
    mode            TEXT        DEFAULT 'search',
    domain          TEXT        DEFAULT 'general',
    query_text      TEXT        DEFAULT '',
    strategy        TEXT        DEFAULT '',
    result_count    INTEGER     DEFAULT 0,
    grounded        BOOLEAN     DEFAULT FALSE,
    classification  TEXT        DEFAULT 'CUI',
    tenant_id       TEXT        DEFAULT 'default',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cortex_search_history_session ON cortex_search_history(session_id);

CREATE TABLE IF NOT EXISTS cortex_messages (
    message_id      TEXT        PRIMARY KEY,
    session_id      TEXT        DEFAULT '',
    turn_number     INTEGER     DEFAULT 0,
    role            TEXT        DEFAULT 'user',
    content         TEXT        DEFAULT '',
    facade          TEXT        DEFAULT '',
    grounded        BOOLEAN     DEFAULT FALSE,
    confidence      TEXT        DEFAULT '',
    citations       TEXT        DEFAULT '',
    governance      TEXT        DEFAULT '',
    classification  TEXT        DEFAULT 'CUI',
    tenant_id       TEXT        DEFAULT 'default',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cortex_messages_session ON cortex_messages(session_id, turn_number);
"""

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS cortex_sessions (
    session_id      TEXT        PRIMARY KEY,
    user_id         TEXT        DEFAULT '',
    mode            TEXT        DEFAULT 'ask',
    domain          TEXT        DEFAULT 'general',
    title           TEXT        DEFAULT '',
    status          TEXT        DEFAULT 'active',
    classification  TEXT        DEFAULT 'CUI',
    tenant_id       TEXT        DEFAULT 'default',
    created_at      TEXT        DEFAULT (datetime('now')),
    updated_at      TEXT        DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cortex_sessions_user ON cortex_sessions(user_id);

CREATE TABLE IF NOT EXISTS cortex_audit (
    audit_id        TEXT        PRIMARY KEY,
    session_id      TEXT        DEFAULT '',
    facade          TEXT        NOT NULL,
    outcome         TEXT        DEFAULT 'pass',
    blocked         INTEGER     DEFAULT 0,
    detail          TEXT        DEFAULT '',
    classification  TEXT        DEFAULT 'CUI',
    tenant_id       TEXT        DEFAULT 'default',
    created_at      TEXT        DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cortex_audit_session ON cortex_audit(session_id);

CREATE TABLE IF NOT EXISTS cortex_search_history (
    query_id        TEXT        PRIMARY KEY,
    session_id      TEXT        DEFAULT '',
    user_id         TEXT        DEFAULT '',
    mode            TEXT        DEFAULT 'search',
    domain          TEXT        DEFAULT 'general',
    query_text      TEXT        DEFAULT '',
    strategy        TEXT        DEFAULT '',
    result_count    INTEGER     DEFAULT 0,
    grounded        INTEGER     DEFAULT 0,
    classification  TEXT        DEFAULT 'CUI',
    tenant_id       TEXT        DEFAULT 'default',
    created_at      TEXT        DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cortex_search_history_session ON cortex_search_history(session_id);

CREATE TABLE IF NOT EXISTS cortex_messages (
    message_id      TEXT        PRIMARY KEY,
    session_id      TEXT        DEFAULT '',
    turn_number     INTEGER     DEFAULT 0,
    role            TEXT        DEFAULT 'user',
    content         TEXT        DEFAULT '',
    facade          TEXT        DEFAULT '',
    grounded        INTEGER     DEFAULT 0,
    confidence      TEXT        DEFAULT '',
    citations       TEXT        DEFAULT '',
    governance      TEXT        DEFAULT '',
    classification  TEXT        DEFAULT 'CUI',
    tenant_id       TEXT        DEFAULT 'default',
    created_at      TEXT        DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cortex_messages_session ON cortex_messages(session_id, turn_number);
"""

_INIT_DONE = False


def init_db() -> None:
    """Create the three Cortex canvas tables (idempotent, backend-aware)."""
    global _INIT_DONE
    if _INIT_DONE:
        return
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        is_pg = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower() in (
            "postgresql", "postgres", "pg"
        )
        schema = _SCHEMA_PG if is_pg else _SCHEMA_SQLITE
        cur = conn.cursor()
        for stmt in schema.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        conn.commit()
        conn.close()
        _INIT_DONE = True
        logger.info("Cortex canvas schema initialized")
    except Exception as exc:
        logger.warning("Cortex db init error: %s", exc)
