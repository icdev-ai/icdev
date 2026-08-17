-- Rollback: 20260817010533_entity_currency
-- CUI // SP-CTI
--
-- entity_currency is a refreshable cache — every row is re-derivable from its
-- provenance_table/provenance_id origin by re-running the backfill, so dropping
-- it loses no evidence that does not still exist upstream. That is the whole
-- reason provenance is a pointer and not a copy.

DROP INDEX IF EXISTS idx_entity_currency_source;
DROP INDEX IF EXISTS idx_entity_currency_tenant;
DROP INDEX IF EXISTS idx_entity_currency_verdict;
DROP INDEX IF EXISTS idx_entity_currency_entity;
DROP INDEX IF EXISTS idx_entity_currency_identity;
DROP TABLE IF EXISTS entity_currency;
