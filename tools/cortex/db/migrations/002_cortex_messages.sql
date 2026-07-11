-- CUI // SP-CTI
-- Cortex chat turns (ctx-canvas-02).
-- Canonical DDL lives in tools/cortex/db/init_db.py (backend-aware PG/SQLite).
-- One row per conversation turn (user + assistant) on the /cortex chat surface,
-- carrying the TRUST labels that produced each assistant turn so a session can
-- be reloaded with its grounded/confidence badges and governance summary intact.

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
