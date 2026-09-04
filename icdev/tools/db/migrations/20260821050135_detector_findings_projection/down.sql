-- Rollback: 20260821050135_detector_findings_projection
-- CUI // SP-CTI

DROP INDEX IF EXISTS idx_detector_findings_task;
DROP INDEX IF EXISTS idx_detector_findings_browse;
DROP TABLE IF EXISTS detector_runs;
DROP TABLE IF EXISTS detector_findings;
