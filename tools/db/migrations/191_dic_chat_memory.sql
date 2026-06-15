-- CUI // SP-CTI
-- Migration 191: Create dic_chat_memory table.
--
-- dic_chat_memory persists chat conversation history for the Document
-- Intelligence Canvas (DIC).  The DIC chat handler (tools/mcp/gap_handlers.py
-- ::handle_dic_chat) performs stateless BM25 retrieval today; this table
-- provides the persistence layer so future iterations can supply multi-turn
-- conversation context and per-session recall.
--
-- Resolves the orphan_db_table gap detected by tools/awareness/gap_detector.py.
--
-- Carries tenant_id + classification so all queries are RLS-aware via
-- tools.db.storage.get_connection().
--
-- Idempotent (IF NOT EXISTS).

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
CREATE TABLE IF NOT EXISTS dic_chat_memory (
    memory_id       TEXT    PRIMARY KEY,
    session_id      TEXT    NOT NULL,
    collection_id   TEXT    NOT NULL DEFAULT 'default',
    user_id         TEXT    NOT NULL DEFAULT '',
    role            TEXT    NOT NULL DEFAULT 'user'
                    CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT    NOT NULL DEFAULT '',
    citations_json  TEXT    NOT NULL DEFAULT '[]',
    token_count     INTEGER NOT NULL DEFAULT 0,
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dic_chat_memory_session    ON dic_chat_memory (session_id);
CREATE INDEX IF NOT EXISTS idx_dic_chat_memory_collection ON dic_chat_memory (collection_id);
CREATE INDEX IF NOT EXISTS idx_dic_chat_memory_tenant     ON dic_chat_memory (tenant_id);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
CREATE TABLE IF NOT EXISTS dic_chat_memory (
    memory_id       TEXT        PRIMARY KEY,
    session_id      TEXT        NOT NULL,
    collection_id   TEXT        NOT NULL DEFAULT 'default',
    user_id         TEXT        NOT NULL DEFAULT '',
    role            TEXT        NOT NULL DEFAULT 'user'
                    CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT        NOT NULL DEFAULT '',
    citations_json  TEXT        NOT NULL DEFAULT '[]',
    token_count     INTEGER     NOT NULL DEFAULT 0,
    tenant_id       TEXT        NOT NULL DEFAULT 'default',
    classification  TEXT        NOT NULL DEFAULT 'CUI',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dic_chat_memory_session    ON dic_chat_memory (session_id);
CREATE INDEX IF NOT EXISTS idx_dic_chat_memory_collection ON dic_chat_memory (collection_id);
CREATE INDEX IF NOT EXISTS idx_dic_chat_memory_tenant     ON dic_chat_memory (tenant_id);

-- @all
