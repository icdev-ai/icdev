-- Migration 244: Program-level risk register (prop-pm-02)
--
-- cpmp_risks already ships inline in tools/govcon/init_db.py and the test
-- schema in tests/conftest.py, but had no standalone numbered migration, so
-- a database that is provisioned via the migration runner alone (rather than
-- by re-running govcon/init_db.py) never got the table. This migration adds
-- it via the same runner used for every other CPMP table. Idempotent CREATE
-- statements — safe to run against a DB that already has cpmp_risks.
--
-- Risks link to milestones (prop-pm-01) and negative events for root-cause
-- traceability. exposure = probability (1-5) x impact (1-5), computed and
-- stored by tools/govcon/risk_manager.py on every write.

CREATE TABLE IF NOT EXISTS cpmp_risks (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'other' CHECK(category IN (
        'cost', 'schedule', 'technical', 'cyber',
        'supply_chain', 'compliance', 'staffing', 'other')),
    probability INTEGER NOT NULL DEFAULT 3 CHECK(probability BETWEEN 1 AND 5),
    impact INTEGER NOT NULL DEFAULT 3 CHECK(impact BETWEEN 1 AND 5),
    exposure INTEGER NOT NULL DEFAULT 9,
    mitigation TEXT,
    owner TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN (
        'open', 'mitigating', 'accepted', 'closed', 'transferred')),
    milestone_id TEXT,
    negative_event_id TEXT,
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (contract_id) REFERENCES cpmp_contracts(id),
    FOREIGN KEY (milestone_id) REFERENCES cpmp_milestones(id),
    FOREIGN KEY (negative_event_id) REFERENCES cpmp_negative_events(id)
);

CREATE INDEX IF NOT EXISTS idx_cpmp_risk_contract  ON cpmp_risks(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_risk_status    ON cpmp_risks(status);
CREATE INDEX IF NOT EXISTS idx_cpmp_risk_exposure  ON cpmp_risks(exposure);
CREATE INDEX IF NOT EXISTS idx_cpmp_risk_milestone ON cpmp_risks(milestone_id);
