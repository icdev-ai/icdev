-- CUI // SP-CTI
-- Cortex canvas tables (ctx-canvas-01).
-- Canonical DDL lives in tools/cortex/db/init_db.py (backend-aware PG/SQLite).
-- This file is the migration-dir marker for the 8-gate completeness check and
-- the reference schema for out-of-band migration runners.

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

-- Append-only governance/facade audit trail (see APPEND_ONLY_TABLES).
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

CREATE INDEX IF NOT EXISTS idx_cortex_sessions_user ON cortex_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_cortex_audit_session ON cortex_audit(session_id);
CREATE INDEX IF NOT EXISTS idx_cortex_search_history_session ON cortex_search_history(session_id);
