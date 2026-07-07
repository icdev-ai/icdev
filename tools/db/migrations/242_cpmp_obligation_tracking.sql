-- CUI // SP-CTI
-- Migration 242: Obligation tracking + base/option period fields on cpmp_contracts (prop-ctr-02)
-- Adds obligated_value, remaining_obligation, period_type, option_number.
-- funded_value, pop_start, pop_end already exist on cpmp_contracts.
-- tools/govcon/init_db.py CREATE TABLE IF NOT EXISTS already includes these
-- columns for fresh installs; this ALTER path covers existing databases.

ALTER TABLE cpmp_contracts ADD COLUMN IF NOT EXISTS obligated_value REAL DEFAULT 0.0;
ALTER TABLE cpmp_contracts ADD COLUMN IF NOT EXISTS remaining_obligation REAL DEFAULT 0.0;
ALTER TABLE cpmp_contracts ADD COLUMN IF NOT EXISTS period_type TEXT DEFAULT 'base';
ALTER TABLE cpmp_contracts ADD COLUMN IF NOT EXISTS option_number INTEGER DEFAULT 0;
