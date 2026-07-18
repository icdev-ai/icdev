-- CUI // SP-CTI
-- Migration 275: Converge IDR (DocGen) schema on PostgreSQL (cnr-doc-04).
--
-- bootstrap_pg.py loads tools/db/schema/pg_consolidated.sql (which predates the
-- IDR canvas and therefore contains NO idr_* tables) and then marks every
-- discovered migration — including 211 — as applied. On such squash-bootstrapped
-- databases the idr_* tables are never created, so ALL DocGen operations fail with
-- "relation does not exist". This migration heals those databases (and is a
-- no-op on incrementally-migrated ones) by creating every idr_* table IF NOT
-- EXISTS with the fully-converged column set (211 + 212 + 214/217 + 257).
--
-- The upload_type CHECK includes 'email' and is derived from
-- tools/docgen/constants.py::UPLOAD_TYPES (keep both in lockstep).

CREATE TABLE IF NOT EXISTS idr_sessions (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    domain          TEXT NOT NULL DEFAULT 'network'
                        CHECK (domain IN ('network','security','devops','developer','compliance','standard_guide')),
    doc_type        TEXT NOT NULL DEFAULT 'runbook',
    template_id     TEXT,
    stage           INTEGER NOT NULL DEFAULT 0 CHECK (stage BETWEEN 0 AND 8),
    status          TEXT NOT NULL DEFAULT 'setup'
                        CHECK (status IN ('setup','ingesting','analyzing','conflicts','synthesizing',
                                          'generating','writeguard','reviewing','publishing','published','failed')),
    dic_collection_id TEXT,
    ace_instance_id TEXT,
    topology_id     TEXT,
    wg_result_id    TEXT,
    created_by      TEXT,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    conflicts_resolved BOOLEAN DEFAULT FALSE,
    suggested_classification TEXT,
    suggested_classification_confidence REAL,
    prior_docs_context TEXT,
    last_source_hash TEXT,
    source_hash_checked_at TIMESTAMP,
    final_doc_text  TEXT,
    dic_doc_id      TEXT,
    source_dic_doc_id TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS idr_uploads (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES idr_sessions(id),
    filename        TEXT NOT NULL,
    upload_type     TEXT NOT NULL
                        CHECK (upload_type IN ('diagram','doc','config','iac','supplement','email')),
    file_path       TEXT,
    file_hash       TEXT,
    dic_doc_id      TEXT,
    extracted_from_doc_id TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','ingested','analyzed','error')),
    error_msg       TEXT,
    tenant_id       TEXT,
    uploaded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS idr_analyses (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES idr_sessions(id),
    upload_id       TEXT NOT NULL REFERENCES idr_uploads(id),
    analysis_type   TEXT NOT NULL
                        CHECK (analysis_type IN ('diagram_analysis','config_review',
                                                 'firewall_review','iac_review','api_review')),
    result_ref_id   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','done','error')),
    error_msg       TEXT,
    tenant_id       TEXT,
    result_json     TEXT,
    confidence_score FLOAT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS idr_conflicts (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES idr_sessions(id),
    node_label      TEXT NOT NULL,
    conflict_type   TEXT NOT NULL
                        CHECK (conflict_type IN ('node_type','property','missing_in_source',
                                                 'topology_discrepancy','boundary_discrepancy')),
    source_a        TEXT NOT NULL,
    source_a_value  TEXT,
    source_b        TEXT NOT NULL,
    source_b_value  TEXT,
    resolved_by     TEXT,
    resolution      TEXT CHECK (resolution IN ('a','b','manual')),
    resolution_notes TEXT,
    resolved_at     TIMESTAMP,
    tenant_id       TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS idr_artifacts (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES idr_sessions(id),
    dic_doc_id      TEXT,
    dic_version_id  TEXT,
    format          TEXT NOT NULL
                        CHECK (format IN ('html','docx','pdf')),
    file_path       TEXT,
    wg_result_id    TEXT,
    published_at    TIMESTAMP,
    tenant_id       TEXT,
    flagged_sections TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_idr_uploads_session   ON idr_uploads(session_id);
CREATE INDEX IF NOT EXISTS idx_idr_analyses_session  ON idr_analyses(session_id);
CREATE INDEX IF NOT EXISTS idx_idr_analyses_upload   ON idr_analyses(upload_id);
CREATE INDEX IF NOT EXISTS idx_idr_conflicts_session ON idr_conflicts(session_id);
CREATE INDEX IF NOT EXISTS idx_idr_artifacts_session ON idr_artifacts(session_id);
