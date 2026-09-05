-- Rollback: 20260905070028_floci_twin_snapshots
-- CUI // SP-CTI

DROP INDEX IF EXISTS idx_floci_twin_snap_created;
DROP INDEX IF EXISTS idx_floci_twin_snap_target;
DROP TABLE IF EXISTS floci_twin_snapshots;
