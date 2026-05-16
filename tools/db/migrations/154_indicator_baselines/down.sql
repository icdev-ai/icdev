-- Rollback: 154_indicator_baselines
-- CUI // SP-CTI

DROP INDEX IF EXISTS idx_indicator_baselines_operator;
DROP INDEX IF EXISTS idx_indicator_baselines_name;
DROP INDEX IF EXISTS idx_indicator_baselines_scope;
DROP TABLE IF EXISTS indicator_baselines;
