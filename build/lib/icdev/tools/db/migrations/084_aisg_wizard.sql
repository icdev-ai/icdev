-- Migration: 084_aisg_wizard
-- CUI // SP-CTI
-- Table: aisg_wizard_sessions — stores AISG wizard session state

-- @sqlite-only
CREATE TABLE IF NOT EXISTS aisg_wizard_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_token       TEXT UNIQUE NOT NULL,
    use_case            TEXT,
    compliance_level    TEXT,
    tech_stack          TEXT,
    ai_maturity         TEXT CHECK (ai_maturity IN ('none', 'pilot', 'scaling')),
    cloud_provider      TEXT,
    generated_args_json TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);

-- @pg-only
CREATE TABLE IF NOT EXISTS aisg_wizard_sessions (
    id                  SERIAL PRIMARY KEY,
    session_token       TEXT UNIQUE NOT NULL,
    use_case            TEXT,
    compliance_level    TEXT,
    tech_stack          TEXT,
    ai_maturity         TEXT CHECK (ai_maturity IN ('none', 'pilot', 'scaling')),
    cloud_provider      TEXT,
    generated_args_json JSONB,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
