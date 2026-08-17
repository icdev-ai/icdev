-- Migration: 20260817024050_docmod_nist_pubs
-- CUI // SP-CTI
--
-- Give docmod_nist_pubs a migration of its OWN (cef-fnd-02).
--
-- The table's only DDL was tools/db/migrations/282_docmod_nist_pubs.sql, a flat
-- legacy file that SHARES version 282 with 282_insider_risk_uba.sql. MigrationRunner
-- keeps only the FIRST entry per version, and the ordering that currently elects the
-- docmod file is incidental — the sort key is (digit-count, digits, entry.name), so
-- "282_docmod..." wins over "282_insider..." purely because 'd' < 'i'. A rename on
-- either side flips the winner and this table silently stops being created on fresh
-- databases. A 14-digit timestamp cannot collide that way (mvs-alloc-01).
--
-- This migration is deliberately IDEMPOTENT and ADDITIVE: it re-declares the same
-- shape with CREATE ... IF NOT EXISTS, so it is a no-op on every database where 282
-- already ran (the live board, applied 2026-07-29) and the authoritative creator on
-- every database built from here on. The flat 282 file is intentionally LEFT IN
-- PLACE — deleting it would promote 282_insider_risk_uba.sql to the v282 slot, and
-- because '282' is already recorded in schema_migrations that promotion would never
-- replay on an existing database. Retiring 282 is a separate, wider change.
--
-- Mutable evidence cache (upsert by pub_id), modeled on docmod_eol_products
-- (migration 257): one row per NIST publication recording the LATEST revision NIST
-- currently publishes. Written by tools/doc_modernization/nist_pubs_sync.py; read by
-- the policy_refs pack, which flags a document citing an OLDER revision as a
-- superseded standard via a deterministic numeric comparison (TRUST rule 1, no LLM).
-- Carries tenant_id/classification for parity with the sibling docmod tables; global
-- evidence rows use a NULL tenant and 'CUI'.

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
