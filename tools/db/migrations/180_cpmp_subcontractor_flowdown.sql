-- CUI // SP-CTI
-- Migration 180: add cpmp_subcontractors.flowdown_verified
--
-- Schema drift fix. The canonical schema (tools/db/init_icdev_db.py and
-- tools/govcon/init_db.py) defines `flowdown_verified INTEGER DEFAULT 0`, and
-- tools/govcon/subcontractor_tracker.py INSERTs it, but the column was never
-- added to existing databases. POST /api/cpmp/contracts/<id>/subcontractors
-- therefore raised: column "flowdown_verified" of relation
-- "cpmp_subcontractors" does not exist. Idempotent ADD COLUMN.
ALTER TABLE cpmp_subcontractors ADD COLUMN IF NOT EXISTS flowdown_verified INTEGER DEFAULT 0;
