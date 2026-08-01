-- Migration 211: Intelligent Documentation Regeneration (IDR) tables
-- Phase 1: core session tracking for the /docgen canvas

-- Session: one per doc regeneration workflow run
CREATE TABLE IF NOT EXISTS idr_sessions (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    domain          TEXT NOT NULL DEFAULT 'network'
                        CHECK (domain IN ('network','security','devops','developer','compliance','standard_guide')),
    doc_type        TEXT NOT NULL DEFAULT 'runbook',
    template_id     TEXT,                    -- WriteGuard wg_style_guides.id
    stage           INTEGER NOT NULL DEFAULT 0 CHECK (stage BETWEEN 0 AND 8),
    status          TEXT NOT NULL DEFAULT 'setup'
                        CHECK (status IN ('setup','ingesting','analyzing','conflicts','synthesizing',
                                          'generating','writeguard','reviewing','publishing','published','failed')),
    dic_collection_id TEXT,                  -- DIC collection backing this session
    ace_instance_id TEXT,                    -- ACE run orchestrating generation
    topology_id     TEXT,                    -- reconciled NDC canvas topology
    wg_result_id    TEXT,                    -- final WriteGuard result ref
    created_by      TEXT,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Upload: one per file uploaded to a session
CREATE TABLE IF NOT EXISTS idr_uploads (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES idr_sessions(id),
    filename        TEXT NOT NULL,
    -- upload_type CHECK derived from tools/docgen/constants.py::UPLOAD_TYPES
    -- ('email' added cnr-doc-04; migration 217 also converges this on PG).
    upload_type     TEXT NOT NULL
                        CHECK (upload_type IN ('diagram','doc','config','iac','supplement','email')),
    file_path       TEXT,
    file_hash       TEXT,
    dic_doc_id      TEXT,                    -- DIC dic_documents.id after ingestion
    extracted_from_doc_id TEXT,              -- if this was embedded in another upload
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','ingested','analyzed','error')),
    error_msg       TEXT,
    tenant_id       TEXT,
    uploaded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Analysis result: one per (upload × analyzer)
CREATE TABLE IF NOT EXISTS idr_analyses (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES idr_sessions(id),
    upload_id       TEXT NOT NULL REFERENCES idr_uploads(id),
    analysis_type   TEXT NOT NULL
                        CHECK (analysis_type IN ('diagram_analysis','config_review',
                                                 'firewall_review','iac_review','api_review')),
    result_ref_id   TEXT NOT NULL,           -- nc_diagram_analyses.id or nc_config_reviews.id
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','done','error')),
    error_msg       TEXT,
    tenant_id       TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Conflict: APPEND-ONLY — never UPDATE or DELETE
-- One row per detected conflict; resolution is recorded in place
CREATE TABLE IF NOT EXISTS idr_conflicts (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES idr_sessions(id),
    node_label      TEXT NOT NULL,           -- normalized label of conflicting node/element
    conflict_type   TEXT NOT NULL
                        CHECK (conflict_type IN ('node_type','property','missing_in_source',
                                                 'topology_discrepancy','boundary_discrepancy')),
    source_a        TEXT NOT NULL,           -- upload filename or analysis_id for side A
    source_a_value  TEXT,                    -- what side A shows (JSON blob)
    source_b        TEXT NOT NULL,           -- upload filename or analysis_id for side B
    source_b_value  TEXT,                    -- what side B shows (JSON blob)
    resolved_by     TEXT,                    -- username who resolved
    resolution      TEXT CHECK (resolution IN ('a','b','manual')),
    resolution_notes TEXT,
    resolved_at     TIMESTAMP,
    tenant_id       TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Generated artifact: one per (session × format) after publish
CREATE TABLE IF NOT EXISTS idr_artifacts (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES idr_sessions(id),
    dic_doc_id      TEXT,                    -- DIC dic_documents.id
    dic_version_id  TEXT,                    -- DIC dic_versions.id
    format          TEXT NOT NULL
                        CHECK (format IN ('html','docx','pdf')),
    file_path       TEXT,
    wg_result_id    TEXT,                    -- WriteGuard analysis that gated this artifact
    published_at    TIMESTAMP,
    tenant_id       TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_idr_uploads_session   ON idr_uploads(session_id);
CREATE INDEX IF NOT EXISTS idx_idr_analyses_session  ON idr_analyses(session_id);
CREATE INDEX IF NOT EXISTS idx_idr_analyses_upload   ON idr_analyses(upload_id);
CREATE INDEX IF NOT EXISTS idx_idr_conflicts_session ON idr_conflicts(session_id);
CREATE INDEX IF NOT EXISTS idx_idr_conflicts_pending ON idr_conflicts(session_id) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_idr_artifacts_session ON idr_artifacts(session_id);
