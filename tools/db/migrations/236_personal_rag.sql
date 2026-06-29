-- Migration 236: personal knowledge base items
CREATE TABLE IF NOT EXISTS user_knowledge_items (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    source_type TEXT NOT NULL CHECK(source_type IN ('url','file','text','github','jira')),
    source_url  TEXT,
    title       TEXT,
    raw_content TEXT,
    summary     TEXT,
    tags        TEXT DEFAULT '[]',
    status      TEXT DEFAULT 'pending' CHECK(status IN ('pending','processing','done','error')),
    error_msg   TEXT,
    created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    indexed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_user_ki ON user_knowledge_items(user_id, tenant_id, status);
