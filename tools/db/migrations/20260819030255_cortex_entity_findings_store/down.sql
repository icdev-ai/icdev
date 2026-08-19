-- Rollback: 20260819030255_cortex_entity_findings_store
-- CUI // SP-CTI

DROP INDEX IF EXISTS idx_cef_findings_entity;
DROP INDEX IF EXISTS idx_cef_findings_browse;
DROP TABLE IF EXISTS cortex_entity_findings;
DROP TABLE IF EXISTS cortex_finding_runs;
