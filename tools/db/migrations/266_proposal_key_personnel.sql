-- Migration 266: proposal_key_personnel — the BID side's person -> LCAT registry
-- (prem-pstaff-01, consumed by the prem-pstaff-02 intake).
-- CUI // SP-CTI
--
-- Before this table the bid side had NO people->LCAT mapping anywhere:
--   * pg_lcat_allocations is task -> LCAT -> FTE. There are no people in it.
--   * pma_personnel (tools/govcon/personnel_manager.py) is POST-AWARD — it is
--     keyed on contract_id, and a bid has no contract yet.
--   * so program_bridge._gather_key_personnel REGEX-SCRAPED NAMES OUT OF DRAFT
--     PROSE. That guess is what this table replaces.
--
-- Every row carries the evidence that justifies its person -> LCAT mapping.
-- evidence_json is NOT NULL and the CHECK forbids an empty one, because an
-- unevidenced mapping is an assertion nobody can defend — and it ends up in a
-- proposal. The intake refuses those rows rather than storing them with an
-- empty evidence field (tools/cortex/rest_v1.py::api_v1_staffing_matrix); this
-- constraint is the last line of that same contract.
--
-- qualification_verdict mirrors VERDICTS in tools/govcon/key_personnel.py —
-- change both together (tests/cortex/test_rest_staffing_matrix.py pins them).
--
-- RLS columns (tenant_id, classification) exist from the start, never retrofitted.
--
-- PG-first DDL; types chosen so the SQLite init-fallback applies verbatim.
-- Idempotent.

CREATE TABLE IF NOT EXISTS proposal_key_personnel (
    id                     TEXT PRIMARY KEY,
    opportunity_id         TEXT NOT NULL,
    person_ref             TEXT NOT NULL,
    name                   TEXT NOT NULL,
    proposed_lcat          TEXT NOT NULL,
    qualification_verdict  TEXT NOT NULL DEFAULT 'qualified',
    evidence_json          TEXT NOT NULL,
    source                 TEXT,
    tenant_id              TEXT NOT NULL DEFAULT 'default',
    classification         TEXT NOT NULL DEFAULT 'CUI',
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT proposal_key_personnel_verdict_check
        CHECK (qualification_verdict IN ('qualified', 'gap', 'exceeds')),
    CONSTRAINT proposal_key_personnel_evidence_check
        CHECK (evidence_json <> '' AND evidence_json <> '[]'),
    CONSTRAINT proposal_key_personnel_person_unique
        UNIQUE (opportunity_id, person_ref)
);

CREATE INDEX IF NOT EXISTS idx_proposal_key_personnel_opportunity
    ON proposal_key_personnel(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_proposal_key_personnel_tenant
    ON proposal_key_personnel(tenant_id);
