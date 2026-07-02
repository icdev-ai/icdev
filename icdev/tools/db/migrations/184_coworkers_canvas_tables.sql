-- CUI // SP-CTI
-- Migration 184: Coworkers canvas tables (cwk-db-01)
-- Canvas tables: no classification / tenant_id columns.

CREATE TABLE IF NOT EXISTS cwk_coworkers (
    id              TEXT PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    role_type       TEXT DEFAULT 'general',
    capabilities_json TEXT DEFAULT '[]',
    config_json     TEXT DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'inactive', 'deprecated')),
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cwk_coworkers_slug   ON cwk_coworkers(slug);
CREATE INDEX IF NOT EXISTS idx_cwk_coworkers_status ON cwk_coworkers(status);

CREATE TABLE IF NOT EXISTS cwk_sessions (
    id              TEXT PRIMARY KEY,
    coworker_id     TEXT NOT NULL,
    chat_context_id TEXT,
    ace_instance_id TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cwk_sessions_coworker ON cwk_sessions(coworker_id);
CREATE INDEX IF NOT EXISTS idx_cwk_sessions_chat     ON cwk_sessions(chat_context_id);
CREATE INDEX IF NOT EXISTS idx_cwk_sessions_ace      ON cwk_sessions(ace_instance_id);
