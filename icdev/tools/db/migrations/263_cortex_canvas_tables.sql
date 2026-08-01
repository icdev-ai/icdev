-- Migration 263: ICDEV Cortex canvas — chat sessions, messages, search history.
-- Distinct from the governance cortex_sessions/cortex_audit (migration 262):
-- these are the /cortex chat surface's own conversation store. All carry
-- tenant_id + classification and are read/written through the RLS-aware
-- get_connection() so per-tenant + Bell-LaPadula read-down filtering applies.
-- Idempotent (CREATE ... IF NOT EXISTS); the blueprint/chat_session writes are
-- best-effort and degrade to no-ops until this migration has run.

CREATE TABLE IF NOT EXISTS cortex_chat_sessions (
    session_id      TEXT        PRIMARY KEY,
    user_id         TEXT        DEFAULT '',
    mode            TEXT        DEFAULT 'ask',
    domain          TEXT        DEFAULT 'general',
    title           TEXT        DEFAULT '',
    status          TEXT        DEFAULT 'active',
    classification  TEXT        DEFAULT 'CUI',
    tenant_id       TEXT        DEFAULT 'default',
    created_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cortex_chat_sessions_user ON cortex_chat_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_cortex_chat_sessions_tenant ON cortex_chat_sessions(tenant_id);

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
    created_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cortex_messages_session ON cortex_messages(session_id, turn_number);

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
    created_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cortex_search_history_session ON cortex_search_history(session_id);
