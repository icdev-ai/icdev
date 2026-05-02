-- Migration: 102_aisg_patterns
-- CUI // SP-CTI
-- Table: aisg_patterns — reusable AI workflow patterns for the AISG Pattern Library

-- @sqlite-only
CREATE TABLE IF NOT EXISTS aisg_patterns (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL CHECK (category IN ('document_qa', 'procurement', 'threat_triage', 'compliance_evidence', 'custom')),
    description     TEXT,
    goal_template   TEXT,
    canvas_type     TEXT,
    complexity      TEXT CHECK (complexity IN ('beginner', 'intermediate', 'advanced')),
    use_case_tags   TEXT,
    deploy_count    INTEGER DEFAULT 0,
    is_builtin      INTEGER DEFAULT 0,
    created_by      TEXT DEFAULT 'system',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- @pg-only
CREATE TABLE IF NOT EXISTS aisg_patterns (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL CHECK (category IN ('document_qa', 'procurement', 'threat_triage', 'compliance_evidence', 'custom')),
    description     TEXT,
    goal_template   TEXT,
    canvas_type     TEXT,
    complexity      TEXT CHECK (complexity IN ('beginner', 'intermediate', 'advanced')),
    use_case_tags   TEXT[],
    deploy_count    INTEGER DEFAULT 0,
    is_builtin      BOOLEAN DEFAULT FALSE,
    created_by      TEXT DEFAULT 'system',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
