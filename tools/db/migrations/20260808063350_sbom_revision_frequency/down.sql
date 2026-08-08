-- Rollback: 20260808063350_sbom_revision_frequency
-- CUI // SP-CTI
--
-- Drops the three revision/frequency columns sbx-prc-02 added to sbom_records.
--
-- Indexes are dropped before their columns because SQLite refuses
-- ALTER TABLE ... DROP COLUMN while an index references the column.

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
DROP INDEX IF EXISTS idx_sbom_rec_digest;
DROP INDEX IF EXISTS idx_sbom_rec_srcrev;

ALTER TABLE sbom_records DROP COLUMN content_digest;
ALTER TABLE sbom_records DROP COLUMN source_revision;
ALTER TABLE sbom_records DROP COLUMN revision_reason;

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
DROP INDEX IF EXISTS idx_sbom_rec_digest;
DROP INDEX IF EXISTS idx_sbom_rec_srcrev;

ALTER TABLE sbom_records DROP COLUMN IF EXISTS content_digest;
ALTER TABLE sbom_records DROP COLUMN IF EXISTS source_revision;
ALTER TABLE sbom_records DROP COLUMN IF EXISTS revision_reason;

-- @all
