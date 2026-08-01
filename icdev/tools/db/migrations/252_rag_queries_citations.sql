-- Migration 252: RAG query + citation stores (rag_queries / rag_citations).
-- Materializes the two tables that notification_service/render_handler_service.py
-- already queries (SELECT ... FROM rag_queries / rag_citations) for databases
-- bootstrapped before tools/db/init_icdev_db.py added them. Without these, the
-- deterministic RAG result-card renderer raises UndefinedTable on PG.
-- Idempotent: safe where init_icdev_db already created them.

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

CREATE INDEX IF NOT EXISTS idx_rag_queries_status ON rag_queries(status);
CREATE INDEX IF NOT EXISTS idx_rag_queries_agent ON rag_queries(agent_id);

CREATE TABLE IF NOT EXISTS rag_citations (
    id              BIGSERIAL PRIMARY KEY,
    query_id        TEXT    NOT NULL REFERENCES rag_queries(id),
    source_doc      TEXT    NOT NULL,
    citation_text   TEXT,
    confidence      REAL    DEFAULT 0.0,
    tenant_id       TEXT    DEFAULT '',
    classification  TEXT    DEFAULT 'CUI',
    created_at      TEXT    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rag_citations_query ON rag_citations(query_id);
