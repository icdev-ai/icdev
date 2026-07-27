-- Migration: 256_govcon_prior_submissions
-- CUI // SP-CTI
-- Table: govcon_prior_submissions — prior RFIs, proposals, awards and CPARS that
-- predate this system, uploaded so they can serve as PRIMARY evidence.
--
-- Primary evidence is a document we actually submitted or were graded on. It
-- outranks 'derived' evidence (our own previously-approved prose) at retrieval,
-- and a numeric claim may not rest on derived evidence alone.
--
-- Distinct from rfi_session_uploads, which is scoped to a single RFI session, has
-- no dedup key, and carries no tenant_id/classification. This table is the
-- company-wide corpus: file_hash answers "is this already ingested?", and RLS
-- columns are present from the start rather than retrofitted.
--
-- `outcome` lets retrieval prefer winning prose. A lost proposal's text is not
-- persuasive evidence; its lessons feed the capture strategy instead.
--
-- Indexed into RAG (and thereby the KG, via ingestion_manager._kg_enrich_chunks)
-- through the `prior_submissions` SOURCE_REGISTRY entry once status='ingested'.

CREATE TABLE IF NOT EXISTS govcon_prior_submissions (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    doc_type        TEXT NOT NULL DEFAULT 'proposal'
        CHECK(doc_type IN ('rfi', 'proposal', 'award', 'cpars')),
    outcome         TEXT NOT NULL DEFAULT 'unknown'
        CHECK(outcome IN ('won', 'lost', 'no_award', 'cancelled', 'unknown')),
    opportunity_id  TEXT,
    solicitation_number TEXT,
    file_name       TEXT NOT NULL,
    file_path       TEXT NOT NULL DEFAULT '',
    file_hash       TEXT NOT NULL,
    file_size       INTEGER DEFAULT 0,
    extracted_text  TEXT,
    extraction_method TEXT DEFAULT '',
    chunk_count     INTEGER DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'extracting', 'extracted', 'failed', 'ingested')),
    uploaded_by     TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI'
);

-- file_hash answers the user's actual question on drop: "already in the library?"
CREATE UNIQUE INDEX IF NOT EXISTS idx_govcon_prior_sub_hash
    ON govcon_prior_submissions(file_hash);
CREATE INDEX IF NOT EXISTS idx_govcon_prior_sub_status
    ON govcon_prior_submissions(status);
CREATE INDEX IF NOT EXISTS idx_govcon_prior_sub_outcome
    ON govcon_prior_submissions(outcome);
CREATE INDEX IF NOT EXISTS idx_govcon_prior_sub_tenant
    ON govcon_prior_submissions(tenant_id);
