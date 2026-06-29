-- Migration 220: NQE advisory tables — Forward Networks integration
-- Append-only tables: nc_advisory_assessments, nc_nqe_audit_log
-- Non-append tables: nc_advisories (status updated in-place), nc_nqe_cache (TTL-prunable)

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Advisory registry (mutable status; rows never deleted)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_advisories (
    id                       SERIAL PRIMARY KEY,
    cve_id                   VARCHAR(50),
    vendor                   VARCHAR(100),
    title                    TEXT,
    affected_models_json     JSONB,
    affected_versions_json   JSONB,
    severity                 VARCHAR(20) NOT NULL DEFAULT 'medium'
                                 CHECK(severity IN ('critical','high','medium','low','informational')),
    cvss_score               FLOAT,
    advisory_text            TEXT,
    source_doc_hash          VARCHAR(64),        -- sha256 of uploaded file bytes (audit)
    source_doc_format        VARCHAR(20),        -- pdf|docx|eml|msg|manual
    extraction_confidence    FLOAT,              -- LLM extraction confidence (null = manual entry)
    status                   VARCHAR(30) NOT NULL DEFAULT 'open'
                                 CHECK(status IN ('open','in_progress','mitigated','excepted','closed')),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_advisories_cve ON nc_advisories(cve_id);
CREATE INDEX IF NOT EXISTS idx_nc_advisories_severity ON nc_advisories(severity);
CREATE INDEX IF NOT EXISTS idx_nc_advisories_status ON nc_advisories(status);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Impact assessment results (APPEND-ONLY — never UPDATE/DELETE)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_advisory_assessments (
    id                           SERIAL PRIMARY KEY,
    advisory_id                  INTEGER NOT NULL REFERENCES nc_advisories(id),
    network_id                   VARCHAR(100),
    fwd_snapshot_id              VARCHAR(100),
    data_source                  VARCHAR(20) NOT NULL DEFAULT 'icdev-internal'
                                     CHECK(data_source IN ('fwd-live','icdev-internal')),
    nql_total                    TEXT,               -- NQL for total device count
    nql_impacted                 TEXT,               -- NQL for impacted subset
    nql_ai_generated             TEXT,               -- AI-generated NQL (dual-query validation)
    nql_template_based           TEXT,               -- Template-based NQL (dual-query validation)
    total_devices                INTEGER,
    impacted_count               INTEGER,
    impacted_devices_json        JSONB,              -- [{name, ip, model, osVersion, site}]
    raw_response_total_json      JSONB,              -- Proof: raw FWD/IQE response for total
    raw_response_impacted_json   JSONB,              -- Proof: raw FWD/IQE response for impacted
    raw_response_hash            VARCHAR(64),        -- sha256 of raw response bytes
    ai_confidence                FLOAT,
    cross_validation_delta_pct   FLOAT,              -- % difference between AI and template counts
    cross_validation_warning     BOOLEAN DEFAULT FALSE,
    approved_by                  VARCHAR(200),       -- HITL: null until approved
    approved_at                  TIMESTAMPTZ,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_advisory_assessments_advisory
    ON nc_advisory_assessments(advisory_id);
CREATE INDEX IF NOT EXISTS idx_nc_advisory_assessments_created
    ON nc_advisory_assessments(created_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. NQE query cache (NOT append-only — TTL-expired rows prunable)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_nqe_cache (
    id          SERIAL PRIMARY KEY,
    nql_hash    VARCHAR(64) NOT NULL,   -- sha256(nql + network_id)
    network_id  VARCHAR(100),
    result_json JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_nc_nqe_cache_hash ON nc_nqe_cache(nql_hash);
CREATE INDEX IF NOT EXISTS idx_nc_nqe_cache_expires ON nc_nqe_cache(expires_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Full audit log (APPEND-ONLY — every action traced, NIST AU)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_nqe_audit_log (
    id                   SERIAL PRIMARY KEY,
    session_id           VARCHAR(100),
    user_session         TEXT,
    action               VARCHAR(50) NOT NULL
                             CHECK(action IN ('translate','explain','run','assess','approve','export','upload','simulate')),
    input_text           TEXT,
    nql_generated        TEXT,
    fwd_snapshot_id      VARCHAR(100),
    data_source          VARCHAR(20),
    result_summary       TEXT,
    raw_response_hash    VARCHAR(64),    -- sha256 of raw FWD/IQE response
    confidence           FLOAT,
    advisory_id          INTEGER,
    assessment_id        INTEGER,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_nqe_audit_advisory ON nc_nqe_audit_log(advisory_id)
    WHERE advisory_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_nc_nqe_audit_created ON nc_nqe_audit_log(created_at DESC);
