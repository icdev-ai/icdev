-- Migration 223: guarantee UNIQUE(topo_id) on nc_consolidation_analysis (ndc-fix-03)
-- Depends on: nc_consolidation_analysis (init_db.py)
--
-- save_consolidation() in tools/network/migration_phases.py upserts via
--   INSERT ... ON CONFLICT(topo_id) DO UPDATE
-- which requires a UNIQUE constraint/index on topo_id. Fresh installs get it
-- from the column-level UNIQUE in the CREATE TABLE. This migration retrofits
-- pre-existing PostgreSQL databases (the dedicated network_canvas DB) created
-- before that constraint shipped, so the upsert can never silently no-op behind
-- a swallowed exception. A UNIQUE index is a valid ON CONFLICT arbiter; the
-- statement is idempotent.

CREATE UNIQUE INDEX IF NOT EXISTS ux_nc_consolidation_topo_id
    ON nc_consolidation_analysis(topo_id);
