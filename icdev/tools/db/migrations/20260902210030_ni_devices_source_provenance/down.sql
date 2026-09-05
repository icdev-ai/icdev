-- Rollback: 20260902210030_ni_devices_source_provenance
-- CUI // SP-CTI

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only

DROP INDEX IF EXISTS idx_nc_discovery_diffs_scan;
DROP INDEX IF EXISTS idx_nc_discovery_scans_created;
DROP INDEX IF EXISTS idx_ni_devices_source;
ALTER TABLE ni_devices DROP COLUMN IF EXISTS source;

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
--
-- DROP COLUMN needs SQLite 3.35+. The indexes go either way.

DROP INDEX IF EXISTS idx_nc_discovery_diffs_scan;
DROP INDEX IF EXISTS idx_nc_discovery_scans_created;
DROP INDEX IF EXISTS idx_ni_devices_source;
