-- Migration: 013_finding_approvals
-- CUI // SP-CTI
--
-- Canvas-finding approval state. One row per unique finding across all 7 canvas DBs
-- (security_canvas, infra_canvas, observability_canvas, boundary_canvas, data_canvas,
-- network_canvas, pipeline_canvas). Findings live in their source canvas DBs and are
-- regenerated each scan; this table persists the human approval decision keyed by a
-- stable SHA-256 hash of (canvas_source, rule_id, title, affected_entity).
--
-- Mutable: a finding can move pending -> approved -> remediated. Each transition
-- is also written to audit_trail (append-only) for compliance evidence.

CREATE TABLE IF NOT EXISTS finding_approvals (
    finding_hash TEXT PRIMARY KEY,
    canvas_source TEXT NOT NULL,
    rule_id TEXT,
    severity TEXT,
    title TEXT NOT NULL,
    affected_entity TEXT,
    decision TEXT DEFAULT 'pending' CHECK(decision IN ('pending', 'approved', 'declined', 'accepted_risk', 'remediated')),
    decision_by TEXT,
    decision_at TIMESTAMP,
    decision_rationale TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_finding_approvals_decision ON finding_approvals(decision);
CREATE INDEX IF NOT EXISTS idx_finding_approvals_canvas ON finding_approvals(canvas_source);
CREATE INDEX IF NOT EXISTS idx_finding_approvals_severity ON finding_approvals(severity);
