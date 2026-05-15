-- CUI // SP-CTI
-- Migration 139: GovLift MAP Assessment Dimensions
-- Adds 7-dimension MAP scoring columns, dependency tracking, and assessment metadata.

-- ── Workload assessment dimensions ────────────────────────────────────────
ALTER TABLE govlift_workloads ADD COLUMN map_strategy TEXT DEFAULT ''
    CHECK (map_strategy IN ('', 'rehost', 'replatform', 'refactor', 'repurchase', 'retain', 'retire'));

ALTER TABLE govlift_workloads ADD COLUMN dim_business INTEGER DEFAULT 0;
ALTER TABLE govlift_workloads ADD COLUMN dim_operational INTEGER DEFAULT 0;
ALTER TABLE govlift_workloads ADD COLUMN dim_platform INTEGER DEFAULT 0;
ALTER TABLE govlift_workloads ADD COLUMN dim_security INTEGER DEFAULT 0;
ALTER TABLE govlift_workloads ADD COLUMN dim_people INTEGER DEFAULT 0;
ALTER TABLE govlift_workloads ADD COLUMN dim_process INTEGER DEFAULT 0;
ALTER TABLE govlift_workloads ADD COLUMN dim_data INTEGER DEFAULT 0;

ALTER TABLE govlift_workloads ADD COLUMN business_value INTEGER DEFAULT 3;
ALTER TABLE govlift_workloads ADD COLUMN technical_debt INTEGER DEFAULT 3;
ALTER TABLE govlift_workloads ADD COLUMN dependency_count INTEGER DEFAULT 0;

ALTER TABLE govlift_workloads ADD COLUMN assessed_at TEXT;
ALTER TABLE govlift_workloads ADD COLUMN auto_assessed INTEGER DEFAULT 0;

-- ── Workload dependency graph ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS govlift_dependencies (
    id                      TEXT PRIMARY KEY,
    workload_id             TEXT NOT NULL REFERENCES govlift_workloads(id),
    dependent_workload_id   TEXT NOT NULL REFERENCES govlift_workloads(id),
    dependency_type         TEXT DEFAULT 'network'
        CHECK (dependency_type IN ('network', 'data', 'service', 'auth', 'scheduling')),
    notes                   TEXT DEFAULT '',
    created_at              TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_govlift_dep_workload ON govlift_dependencies(workload_id);
CREATE INDEX IF NOT EXISTS idx_govlift_dep_dependent ON govlift_dependencies(dependent_workload_id);

-- ── Dimension score index for fast planning queries ─────────────────────
CREATE INDEX IF NOT EXISTS idx_govlift_wl_score ON govlift_workloads(map_score);
CREATE INDEX IF NOT EXISTS idx_govlift_wl_strategy ON govlift_workloads(map_strategy);
