-- CUI // SP-CTI
-- migrations/002_rag_queries.sql
-- Adds rag_queries and rag_citations tables.
-- rag_queries tracks RAG knowledge search requests and their lifecycle.
-- rag_citations stores source citations attached to each RAG query result.
-- These tables are referenced by tools/notification_service/render_handler_service.py
-- and the schema mirrors tools/db/init_icdev_db.py (added in this migration).

CREATE TABLE IF NOT EXISTS rag_queries (
    id              TEXT    PRIMARY KEY,
    query_text      TEXT    NOT NULL,
    lens            TEXT    DEFAULT 'default',
    status          TEXT    DEFAULT 'pending'
        CHECK(status IN ('pending', 'running', 'done', 'failed')),
    agent_id        TEXT,
    tenant_id       TEXT    DEFAULT '',
    classification  TEXT    DEFAULT 'CUI',
    created_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS rag_citations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id        TEXT    NOT NULL REFERENCES rag_queries(id),
    source_doc      TEXT    NOT NULL,
    citation_text   TEXT,
    confidence      REAL    DEFAULT 0.0,
    tenant_id       TEXT    DEFAULT '',
    classification  TEXT    DEFAULT 'CUI',
    created_at      TEXT    DEFAULT CURRENT_TIMESTAMP
);
