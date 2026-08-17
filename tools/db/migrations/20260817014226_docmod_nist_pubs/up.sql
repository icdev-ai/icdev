-- Migration: 20260817014226_docmod_nist_pubs
-- CUI // SP-CTI
--
-- Creates docmod_nist_pubs: the NIST publication revision cache the docmod
-- policy_refs pack reads as evidence (cef-fnd-02).
--
-- WHY A SECOND MIGRATION FOR A TABLE THAT ALREADY HAS DDL. The DDL landed as
-- the flat file tools/db/migrations/282_docmod_nist_pubs.sql, and version '282'
-- was ALREADY recorded applied in schema_migrations by the 2026-07-29 squash
-- baseline ('squashed-282'). MigrationRunner skips any version it has already
-- recorded, so the flat file has never run against the live database and the
-- table is ABSENT — not empty. No writer can populate a table that does not
-- exist, so tools/doc_modernization/nist_pubs_sync.py had no substrate and the
-- policy_refs pack's dynamic NIST-revision half returned 'unknown' forever.
--
-- A timestamped id cannot be shadowed that way, which is why the fix is a new
-- migration rather than an edit to the flat file (the flat file is left in
-- place: it still runs on a database built from zero, and this DDL is
-- CREATE ... IF NOT EXISTS, so the two are idempotent with respect to each
-- other in either order).
--
-- Shape is unchanged from 282 and mirrors the sibling docmod_eol_products
-- (mutable evidence cache, upsert by the natural key, tenant_id/classification
-- for RLS parity; global evidence rows carry NULL tenant and 'CUI').

CREATE TABLE IF NOT EXISTS docmod_nist_pubs (
    id              TEXT PRIMARY KEY,
    pub_id          TEXT NOT NULL,           -- normalized publication id, e.g. 'SP 800-53'
    latest_revision TEXT,                    -- display revision, e.g. 'Rev 5'
    revision_num    INTEGER,                 -- numeric revision for comparison (5)
    title           TEXT,
    url             TEXT,
    published_date  TEXT,                    -- ISO date of the revision announcement
    source          TEXT NOT NULL DEFAULT 'nist.gov'
                        CHECK (source IN ('nist.gov','seed','manual')),
    synced_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    UNIQUE (pub_id)
);

CREATE INDEX IF NOT EXISTS idx_docmod_nist_pubs_pub ON docmod_nist_pubs(pub_id);
