-- Migration 257: Document Modernization Engine (docmod) core schema
-- Plan: review-dic-http-localhost-5050-document-cuddly-kazoo.md — section 'Schema'
-- PG-authored SQL; JSON columns are parsed in Python, never queried with JSON SQL.
-- All tables carry tenant_id/classification (they reference DIC docs) → get_connection().

-- Scan run: APPEND-ONLY — never UPDATE or DELETE. One row per sweep/scan invocation.
CREATE TABLE IF NOT EXISTS docmod_scan_runs (
    run_id          TEXT PRIMARY KEY,
    scope_type      TEXT NOT NULL DEFAULT 'all'
                        CHECK (scope_type IN ('all','collection','doc')),
    scope_id        TEXT,                    -- collection/doc id when scope_type != 'all'
    pack_ids        TEXT DEFAULT '[]',       -- JSON list of enabled pack ids
    evidence_hash   TEXT,                    -- combined hash of evidence inputs (incremental skip)
    docs_scanned    INTEGER NOT NULL DEFAULT 0,
    findings_new    INTEGER NOT NULL DEFAULT 0,
    findings_resolved INTEGER NOT NULL DEFAULT 0,
    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at     TIMESTAMP,
    triggered_by    TEXT NOT NULL DEFAULT 'manual'
                        CHECK (triggered_by IN ('manual','reflex','daemon','api')),
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI'
);

-- Finding: APPEND-ONLY — state transitions are recorded as a NEW row whose
-- supersedes_id points at the previous row; never UPDATE or DELETE.
CREATE TABLE IF NOT EXISTS docmod_findings (
    finding_id      TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES docmod_scan_runs(run_id),
    doc_id          TEXT NOT NULL,           -- dic_documents.id
    version_id      TEXT,                    -- dic_versions.id scanned
    chunk_link_id   TEXT,                    -- RAG chunk linkage for evidence
    section_heading TEXT,
    page            INTEGER,
    pack_id         TEXT NOT NULL,           -- args/docmod/packs/*.yaml key
    entity_label    TEXT NOT NULL,           -- extracted entity (e.g. 'TLS 1.1', 'Catalyst 6500')
    entity_type     TEXT NOT NULL,           -- e.g. protocol, hardware_model, software_product, standard
    finding_type    TEXT NOT NULL,           -- e.g. eol_hardware, deprecated_tech, defacto_divergence, catalog_gap
    currency_verdict TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (currency_verdict IN ('current','deprecated','eol','retired','divergent','unknown')),
    severity        TEXT NOT NULL DEFAULT 'medium'
                        CHECK (severity IN ('critical','high','medium','low','info')),
    rationale       TEXT,
    evidence_json   TEXT DEFAULT '[]',       -- JSON: deterministic evidence refs (catalog/EOL/rulebook/chunk ids)
    recommended_replacement TEXT,            -- must resolve to an existing catalog/learner entry
    replacement_evidence_json TEXT DEFAULT '[]',
    confidence      REAL NOT NULL DEFAULT 0.0,
    state           TEXT NOT NULL DEFAULT 'open'
                        CHECK (state IN ('open','redline_drafted','accepted','rejected','resolved','superseded','stale')),
    redline_suggestion_id TEXT,              -- dic_suggestions.id once a redline is drafted
    prediction_id   TEXT,                    -- oracle/kanban rollup prediction linkage
    dedupe_key      TEXT NOT NULL,           -- stable hash(doc, section, entity, finding_type)
    supersedes_id   TEXT REFERENCES docmod_findings(finding_id),
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- EOL product cache: MUTABLE — synced from endoflife.date or air-gap seed YAML.
CREATE TABLE IF NOT EXISTS docmod_eol_products (
    id              TEXT PRIMARY KEY,
    product         TEXT NOT NULL,           -- endoflife.date product slug
    cycle           TEXT NOT NULL,           -- release cycle (e.g. '3.9', '2019')
    eol_date        TEXT,                    -- ISO date; NULL = not announced
    eos_date        TEXT,                    -- end of support
    latest_version  TEXT,
    lts             INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'endoflife.date'
                        CHECK (source IN ('endoflife.date','seed','manual')),
    synced_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    UNIQUE (product, cycle)
);

-- De facto standards: MUTABLE — recomputed per sweep from deployment inventory.
CREATE TABLE IF NOT EXISTS docmod_defacto_standards (
    id              TEXT PRIMARY KEY,
    domain          TEXT NOT NULL,           -- pack domain (hardware, software, crypto_protocols, policy)
    category        TEXT NOT NULL,
    vendor          TEXT,
    product         TEXT NOT NULL,
    version         TEXT,
    deploy_count    INTEGER NOT NULL DEFAULT 0,
    weighted_score  REAL NOT NULL DEFAULT 0.0,   -- recency-weighted deployment score
    share_pct       REAL NOT NULL DEFAULT 0.0,   -- share of category deployments
    computed_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI'
);

-- Per-doc scan state: MUTABLE — drives incremental skip.
CREATE TABLE IF NOT EXISTS docmod_doc_scan_state (
    doc_id             TEXT PRIMARY KEY,     -- dic_documents.id
    last_version_id    TEXT,
    last_evidence_hash TEXT,
    last_scanned_at    TIMESTAMP,
    open_findings      INTEGER NOT NULL DEFAULT 0,
    tenant_id          TEXT,
    classification     TEXT DEFAULT 'CUI'
);

-- Central standards catalog: MUTABLE, team-curated (Part C).
CREATE TABLE IF NOT EXISTS docmod_catalog_entries (
    entry_id        TEXT PRIMARY KEY,
    domain          TEXT NOT NULL,           -- hardware, software, crypto_protocols, policy (+ future packs)
    category        TEXT NOT NULL,
    vendor          TEXT,
    product         TEXT NOT NULL,
    model_family    TEXT,
    version         TEXT,
    status          TEXT NOT NULL DEFAULT 'approved'
                        CHECK (status IN ('approved','deprecated','retired')),
    eol_date        TEXT,
    eos_date        TEXT,
    replacement_entry_id TEXT REFERENCES docmod_catalog_entries(entry_id),
    metadata_json   TEXT DEFAULT '{}',
    tags_json       TEXT DEFAULT '[]',
    source          TEXT NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual','imported','promoted_from_defacto')),
    is_builtin      INTEGER NOT NULL DEFAULT 0,
    created_by      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    UNIQUE (domain, category, vendor, product, version)
);

-- Catalog curation audit: APPEND-ONLY — never UPDATE or DELETE (NIST AU).
CREATE TABLE IF NOT EXISTS docmod_catalog_audit (
    id              TEXT PRIMARY KEY,
    entry_id        TEXT NOT NULL,           -- docmod_catalog_entries.entry_id
    event_type      TEXT NOT NULL,           -- create / update / status_change / import / promote
    actor           TEXT NOT NULL DEFAULT 'system',
    details         TEXT DEFAULT '{}',       -- JSON: field-level old/new values
    recorded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI'
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_docmod_findings_doc      ON docmod_findings(doc_id, state);
CREATE INDEX IF NOT EXISTS idx_docmod_findings_dedupe   ON docmod_findings(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_docmod_findings_run      ON docmod_findings(run_id);
CREATE INDEX IF NOT EXISTS idx_docmod_eol_product       ON docmod_eol_products(product);
CREATE INDEX IF NOT EXISTS idx_docmod_defacto_domain    ON docmod_defacto_standards(domain, category);
CREATE INDEX IF NOT EXISTS idx_docmod_catalog_domain    ON docmod_catalog_entries(domain, category, status);
CREATE INDEX IF NOT EXISTS idx_docmod_catalog_audit_entry ON docmod_catalog_audit(entry_id);
