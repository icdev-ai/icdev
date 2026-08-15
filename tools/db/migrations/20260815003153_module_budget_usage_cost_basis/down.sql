-- Migration rollback: 20260815003153_module_budget_usage_cost_basis
-- CUI // SP-CTI
--
-- Drops the basis label. The amounts remain, and become ambiguous again: a 0.00
-- from local inference and a 0.00 from a model with no price look identical.

-- @pg-only
DROP INDEX IF EXISTS idx_mbu_cost_basis;

-- @pg-only
ALTER TABLE module_budget_usage DROP COLUMN IF EXISTS cost_basis;

-- @sqlite-only
SELECT 1;
