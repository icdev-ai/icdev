-- Migration: 085_aadc_versions
-- CUI // SP-CTI
-- Table: aadc_design_versions — version history snapshots for AADC designs

-- @sqlite-only
CREATE TABLE IF NOT EXISTS aadc_design_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id   TEXT NOT NULL REFERENCES aadc_designs(id) ON DELETE CASCADE,
    version_num INTEGER NOT NULL,
    label       TEXT,
    snapshot_json TEXT NOT NULL,
    created_by  TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE (design_id, version_num)
);

CREATE INDEX IF NOT EXISTS idx_aadc_dv_design ON aadc_design_versions (design_id);

-- @pg-only
CREATE TABLE IF NOT EXISTS aadc_design_versions (
    id            SERIAL PRIMARY KEY,
    design_id     TEXT NOT NULL REFERENCES aadc_designs(id) ON DELETE CASCADE,
    version_num   INT NOT NULL,
    label         TEXT,
    snapshot_json JSONB NOT NULL,
    created_by    TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (design_id, version_num)
);

CREATE INDEX IF NOT EXISTS idx_aadc_dv_design ON aadc_design_versions (design_id);
