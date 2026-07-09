-- Migration 253: RLS columns for capture/pWin/PTW tables (prop-vv-02 V&V gate).
-- pg_pwin_assessments / pg_competitor_awards / pg_capture_gate_decisions are created
-- by ensure-table helpers (bayesian_bid_scorer.py, rate_benchmarker.py) and older
-- migrations without classification/tenant_id, so the RLS predicate injected by
-- get_connection() raised UndefinedColumn on every read (pWin fetch, weighted
-- pipeline value, PTW analysis, capture-gate history all 500'd on drifted DBs).
--
-- Companion changes (so fresh databases are correct, since bootstrap_pg marks
-- migrations applied and would not re-run this on a fresh DB):
--   * bayesian_bid_scorer._ensure_tables / rate_benchmarker._ensure_tables now
--     create the columns.
--   * pg_consolidated.sql pg_pwin_assessments / pg_competitor_awards carry them.
--   * init_icdev_db.py already carries them on pg_capture_gate_decisions.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS is safe where the columns already exist.
-- (The 248 contract_mod CHECK is already on main via init_icdev_db + migration 177,
--  so it is intentionally not repeated here.)

ALTER TABLE pg_pwin_assessments ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE pg_pwin_assessments ADD COLUMN IF NOT EXISTS classification TEXT DEFAULT 'CUI';

ALTER TABLE pg_competitor_awards ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE pg_competitor_awards ADD COLUMN IF NOT EXISTS classification TEXT DEFAULT 'CUI';

ALTER TABLE pg_capture_gate_decisions ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE pg_capture_gate_decisions ADD COLUMN IF NOT EXISTS classification TEXT DEFAULT 'CUI';
