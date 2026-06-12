-- Migration: 100_aisg_agent_designs
-- CUI // SP-CTI
-- Table: aisg_agent_designs — stores AI agent design artifacts from the AISG wizard

-- @sqlite-only
CREATE TABLE IF NOT EXISTS aisg_agent_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    canvas_json     TEXT,
    generated_goal_md TEXT,
    trust_tier      TEXT CHECK (trust_tier IN ('observer', 'advisor', 'executor', 'full')),
    created_by      TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- @pg-only
CREATE TABLE IF NOT EXISTS aisg_agent_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    canvas_json     JSONB,
    generated_goal_md TEXT,
    trust_tier      TEXT CHECK (trust_tier IN ('observer', 'advisor', 'executor', 'full')),
    created_by      TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
