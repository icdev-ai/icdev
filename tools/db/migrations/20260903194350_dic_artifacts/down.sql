-- Migration rollback: 20260903194350_dic_artifacts
-- CUI // SP-CTI
DROP INDEX IF EXISTS idx_dic_artifacts_doc;
DROP INDEX IF EXISTS idx_dic_artifacts_version;
DROP TABLE IF EXISTS dic_artifacts;
