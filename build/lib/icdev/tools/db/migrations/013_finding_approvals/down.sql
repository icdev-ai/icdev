-- Rollback: 013_finding_approvals
-- CUI // SP-CTI

DROP INDEX IF EXISTS idx_finding_approvals_severity;
DROP INDEX IF EXISTS idx_finding_approvals_canvas;
DROP INDEX IF EXISTS idx_finding_approvals_decision;
DROP TABLE IF EXISTS finding_approvals;
