-- 175_cpmp_ims.sql
-- CUI // SP-CTI
-- Lightweight Integrated Master Schedule (IMS): milestones + dependency graph
-- linked to cpmp_wbs (schedule traceability) and cpmp_evm_periods (EVM linkage).

CREATE TABLE IF NOT EXISTS cpmp_milestones (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    wbs_id TEXT REFERENCES cpmp_wbs(id),
    title TEXT NOT NULL,
    description TEXT,
    baseline_date TEXT,
    forecast_date TEXT,
    actual_date TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN (
        'pending', 'in_progress', 'complete', 'missed', 'on_hold')),
    evm_period_id TEXT REFERENCES cpmp_evm_periods(id),
    responsible_person TEXT,
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_cpmp_ms_contract ON cpmp_milestones(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_ms_wbs ON cpmp_milestones(wbs_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_ms_status ON cpmp_milestones(status);
CREATE INDEX IF NOT EXISTS idx_cpmp_ms_baseline ON cpmp_milestones(baseline_date);

CREATE TABLE IF NOT EXISTS cpmp_milestone_deps (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    predecessor_id TEXT NOT NULL REFERENCES cpmp_milestones(id),
    successor_id TEXT NOT NULL REFERENCES cpmp_milestones(id),
    lag_days INTEGER DEFAULT 0,
    dep_type TEXT DEFAULT 'finish_to_start' CHECK(dep_type IN (
        'finish_to_start', 'start_to_start', 'finish_to_finish', 'start_to_finish')),
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    UNIQUE(predecessor_id, successor_id)
);
CREATE INDEX IF NOT EXISTS idx_cpmp_msdep_contract ON cpmp_milestone_deps(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_msdep_pred ON cpmp_milestone_deps(predecessor_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_msdep_succ ON cpmp_milestone_deps(successor_id);
