-- Migration 247: tenant_id backfill for the CPMP INT coverage tables missed by
-- migrations 245/246 (created in tools/govcon/init_db.py, not the consolidated
-- schema). Required so the RLS predicate injector (prop-fix-12) can apply
-- tenant_id filters on /api/cpmp reads without UndefinedColumn.

ALTER TABLE cpmp_int_coverage ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE cpmp_collection_requirements ADD COLUMN IF NOT EXISTS tenant_id TEXT;
