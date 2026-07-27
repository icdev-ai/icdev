-- CUI // SP-CTI
-- 258: Document Modernization Engine (docmod) core schema.
-- Findings and scan runs are APPEND-ONLY (state transitions on findings are
-- expressed as superseding rows via supersedes_id, never UPDATE/DELETE).
-- All tables carry tenant_id/classification — they reference DIC documents.

CREATE TABLE IF NOT EXISTS docmod_scan_runs (
    run_id          TEXT PRIMARY KEY,
    scope_type      TEXT NOT NULL,            -- 'document' | 'collection' | 'sweep'
    scope_id        TEXT,
    pack_ids        TEXT,                     -- JSON array; parse in Python only
    evidence_hash   TEXT,                     -- combined pack evidence_snapshot()
    docs_scanned    INTEGER DEFAULT 0,
    findings_new    INTEGER DEFAULT 0,
    findings_resolved INTEGER DEFAULT 0,
    trigger         TEXT DEFAULT 'manual',    -- 'reflex' | 'manual' | 'api'
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    tenant_id       TEXT,
    classification  TEXT
);

CREATE TABLE IF NOT EXISTS docmod_findings (
    finding_id      TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    doc_id          TEXT NOT NULL,
    version_id      TEXT NOT NULL,
    chunk_link_id   TEXT,
    section_heading TEXT,
    page            INTEGER,
    pack_id         TEXT NOT NULL,
    entity_label    TEXT NOT NULL,
    entity_type     TEXT,
    finding_type    TEXT NOT NULL,            -- constants.FINDING_TYPES
    currency_verdict TEXT NOT NULL,           -- constants.CURRENCY_VERDICTS
    severity        TEXT NOT NULL DEFAULT 'medium',
    rationale       TEXT,
    evidence_json   TEXT,                     -- citation-shaped evidence entries
    recommended_replacement TEXT,
    replacement_evidence_json TEXT,
    confidence      REAL,
    state           TEXT NOT NULL DEFAULT 'open',   -- constants.FINDING_STATES
    supersedes_id   TEXT,                     -- append-only state chain
    redline_suggestion_id TEXT,               -- dic_suggestions row once drafted
    prediction_id   TEXT,                     -- oracle_predictions rollup link
    dedupe_key      TEXT,                     -- sha256(doc|pack|entity|type)
    created_at      TEXT NOT NULL,
    tenant_id       TEXT,
    classification  TEXT
);
CREATE INDEX IF NOT EXISTS idx_docmod_findings_doc    ON docmod_findings(doc_id, state);
CREATE INDEX IF NOT EXISTS idx_docmod_findings_dedupe ON docmod_findings(dedupe_key);

CREATE TABLE IF NOT EXISTS docmod_eol_products (
    id              SERIAL PRIMARY KEY,
    product         TEXT NOT NULL,
    cycle           TEXT NOT NULL,
    eol_date        TEXT,
    eos_date        TEXT,
    latest_version  TEXT,
    lts             INTEGER DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'static_seed',  -- 'endoflife.date' | 'static_seed' | 'import'
    synced_at       TEXT,
    tenant_id       TEXT,
    classification  TEXT,
    UNIQUE(product, cycle)
);

CREATE TABLE IF NOT EXISTS docmod_defacto_standards (
    id              SERIAL PRIMARY KEY,
    domain          TEXT NOT NULL,            -- 'network_hardware' | 'software' | ...
    category        TEXT NOT NULL,            -- device_type / software role
    vendor          TEXT,
    product         TEXT NOT NULL,
    version         TEXT,
    deploy_count    INTEGER DEFAULT 0,
    weighted_score  REAL DEFAULT 0,
    share_pct       REAL DEFAULT 0,
    computed_at     TEXT NOT NULL,
    tenant_id       TEXT,
    classification  TEXT,
    UNIQUE(domain, category, vendor, product, version)
);

CREATE TABLE IF NOT EXISTS docmod_doc_scan_state (
    doc_id          TEXT PRIMARY KEY,
    last_version_id TEXT,
    last_evidence_hash TEXT,
    last_scanned_at TEXT,
    open_findings   INTEGER DEFAULT 0,
    tenant_id       TEXT,
    classification  TEXT
);

-- Central curated standards catalog (Part C). Team-curated, mutable; every
-- mutation appends a docmod_catalog_audit row (append-only).
CREATE TABLE IF NOT EXISTS docmod_catalog_entries (
    entry_id        TEXT PRIMARY KEY,
    domain          TEXT NOT NULL,
    category        TEXT,
    vendor          TEXT,
    product         TEXT NOT NULL,
    model_family    TEXT,
    version         TEXT,
    status          TEXT NOT NULL DEFAULT 'approved',  -- approved | deprecated | retired
    eol_date        TEXT,
    eos_date        TEXT,
    replacement_entry_id TEXT,
    metadata_json   TEXT,
    tags_json       TEXT,
    source          TEXT NOT NULL DEFAULT 'manual',    -- manual | imported | promoted_from_defacto
    is_builtin      INTEGER DEFAULT 0,
    created_by      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT,
    tenant_id       TEXT,
    classification  TEXT,
    UNIQUE(domain, category, vendor, product, version)
);
CREATE INDEX IF NOT EXISTS idx_docmod_catalog_domain ON docmod_catalog_entries(domain, status);

CREATE TABLE IF NOT EXISTS docmod_catalog_audit (
    audit_id        TEXT PRIMARY KEY,
    entry_id        TEXT NOT NULL,
    action          TEXT NOT NULL,            -- create | update | deprecate | retire | import | propose
    actor           TEXT,
    detail_json     TEXT,
    created_at      TEXT NOT NULL,
    tenant_id       TEXT,
    classification  TEXT
);
