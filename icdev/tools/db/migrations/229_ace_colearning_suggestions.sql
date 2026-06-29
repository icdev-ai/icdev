-- CUI // SP-CTI
-- Migration 229: co-learning suggestion store for self-improving agent loops
-- Persists rule-based improvement suggestions per (role, category, suggestion)
-- so that recurring issues accumulate visibility and are injected into the
-- next session's system prompt patch via co_learning_store.build_system_prompt_patch().
-- category maps to suggest_improvements() 'field' key (system_prompt / max_iterations /
-- folder_access / reasoning_style).

CREATE TABLE IF NOT EXISTS ace_colearning_suggestions (
    id               SERIAL PRIMARY KEY,
    role             TEXT NOT NULL DEFAULT '',
    category         TEXT NOT NULL DEFAULT 'general',
    suggestion       TEXT NOT NULL DEFAULT '',
    session_id       TEXT NOT NULL DEFAULT '',
    severity         TEXT NOT NULL DEFAULT 'medium',
    applied_count    INTEGER NOT NULL DEFAULT 0,
    dismissed_count  INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT '',
    last_seen_at     TEXT NOT NULL DEFAULT '',
    CONSTRAINT uq_colearn_role_cat_sug UNIQUE (role, category, suggestion)
);

CREATE INDEX IF NOT EXISTS idx_cls_role
    ON ace_colearning_suggestions (role);

CREATE INDEX IF NOT EXISTS idx_cls_role_cat
    ON ace_colearning_suggestions (role, category);

CREATE INDEX IF NOT EXISTS idx_cls_last_seen
    ON ace_colearning_suggestions (last_seen_at DESC);

-- SQLite fallback note: ON CONFLICT (role, category, suggestion) DO UPDATE
-- is handled in co_learning_store.py via psycopg2 for PG; SQLite uses the same
-- UNIQUE constraint and supports INSERT OR REPLACE / ON CONFLICT identically.
