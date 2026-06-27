-- Migration 223: Process-Ify doc regen columns + Process Chain tables

-- Doc regen columns on studio_workflows
ALTER TABLE studio_workflows ADD COLUMN IF NOT EXISTS source_doc_text TEXT;
ALTER TABLE studio_workflows ADD COLUMN IF NOT EXISTS style_fingerprint TEXT;
ALTER TABLE studio_workflows ADD COLUMN IF NOT EXISTS regen_artifact_path TEXT;

-- Process Chain: top-level container for multi-phase workflows
CREATE TABLE IF NOT EXISTS wfc_process_chains (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    industry    TEXT,
    status      TEXT NOT NULL DEFAULT 'draft'
                    CHECK(status IN ('draft','active','completed')),
    created_by  TEXT,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Process Chain Phases: ordered stages within a chain
CREATE TABLE IF NOT EXISTS wfc_chain_phases (
    id                  TEXT PRIMARY KEY,
    chain_id            TEXT NOT NULL REFERENCES wfc_process_chains(id) ON DELETE CASCADE,
    phase_number        INTEGER NOT NULL,
    name                TEXT NOT NULL,
    team_name           TEXT,
    team_role           TEXT,
    workflow_ids        TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending','active','in_progress','complete')),
    unlock_threshold    INTEGER NOT NULL DEFAULT 100,
    handoff_checklist   TEXT NOT NULL DEFAULT '[]',
    style_fingerprint   TEXT,
    regen_artifact_path TEXT,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chain_id, phase_number)
);
