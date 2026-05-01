-- Migration: 078_workflow_hitl
-- CUI // SP-CTI
--
-- First 4 tables of the policy-driven HITL workflow layer:
-- wf_templates, wf_teams, wf_team_members, wf_team_assignments.

-- ── Workflow Templates ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wf_templates (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    canvas_type      TEXT,
    stages_json      TEXT NOT NULL,
    roles_json       TEXT NOT NULL,
    approval_policy  TEXT NOT NULL DEFAULT 'any_one',
    kickback_limit   INTEGER NOT NULL DEFAULT 3,
    is_default       INTEGER NOT NULL DEFAULT 0,
    is_system        INTEGER NOT NULL DEFAULT 0,
    created_by       TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Teams ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wf_teams (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    canvas_type TEXT,
    created_by  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Team Members ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wf_team_members (
    id         TEXT PRIMARY KEY,
    team_id    TEXT NOT NULL REFERENCES wf_teams(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    role_label TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(team_id, user_id)
);

-- ── Team Assignments ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wf_team_assignments (
    id          TEXT PRIMARY KEY,
    team_id     TEXT NOT NULL REFERENCES wf_teams(id) ON DELETE CASCADE,
    template_id TEXT REFERENCES wf_templates(id),
    scope_type  TEXT NOT NULL CHECK(scope_type IN ('project','task','task_group')),
    scope_id    TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(team_id, scope_type, scope_id)
);

-- ── Feedback (append-only, NIST AU) ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wf_feedback (
    id               TEXT PRIMARY KEY,
    approval_id      TEXT NOT NULL REFERENCES wf_approvals(id),
    instance_id      TEXT NOT NULL REFERENCES wf_instances(id),
    task_id          TEXT,
    canvas_type      TEXT,
    template_id      TEXT REFERENCES wf_templates(id),
    stage            TEXT NOT NULL,
    decision         TEXT NOT NULL CHECK(decision IN ('approve','kickback','conditional')),
    feedback_types   TEXT,
    rating           INTEGER CHECK(rating BETWEEN 1 AND 5),
    comments         TEXT,
    improvement_tags TEXT,
    kickback_reason  TEXT,
    citations_json   TEXT,
    submitted_by     TEXT NOT NULL,
    submitted_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Feedback Insights (aggregated by Genesis reflex) ──────────────────────────
CREATE TABLE IF NOT EXISTS wf_feedback_insights (
    id            TEXT PRIMARY KEY,
    canvas_type   TEXT,
    template_id   TEXT,
    feedback_type TEXT,
    avg_rating    REAL,
    issue_count   INTEGER,
    top_tags      TEXT,
    kickback_rate REAL,
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── External Steps (email / ticket / wiki) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS wf_external_steps (
    id              TEXT PRIMARY KEY,
    instance_id     TEXT NOT NULL REFERENCES wf_instances(id),
    stage_name      TEXT NOT NULL,
    step_type       TEXT NOT NULL
        CHECK(step_type IN ('manual','external_email','external_ticket','external_wiki')),
    external_system TEXT,
    external_ref    TEXT,
    webhook_token   TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','sent','waiting','completed','failed','timed_out')),
    notified_at     TEXT,
    completed_at    TEXT,
    completed_by    TEXT,
    payload_json    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── kanban_tasks: add hitl_stage tracking column ──────────────────────────────
ALTER TABLE kanban_tasks ADD COLUMN hitl_stage TEXT;

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_wf_ext_instance          ON wf_external_steps(instance_id, status);
CREATE INDEX IF NOT EXISTS idx_wf_ext_token             ON wf_external_steps(webhook_token);
CREATE INDEX IF NOT EXISTS idx_wf_feedback_instance     ON wf_feedback(instance_id);
CREATE INDEX IF NOT EXISTS idx_wf_feedback_canvas       ON wf_feedback(canvas_type, submitted_at);
CREATE INDEX IF NOT EXISTS idx_wf_team_assignments_scope ON wf_team_assignments(scope_type, scope_id);

-- ── Document Templates ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wf_document_templates (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    doc_type          TEXT NOT NULL
        CHECK(doc_type IN ('checklist','form','sop_reference','standard')),
    schema_json       TEXT NOT NULL DEFAULT '{}',
    canvas_type       TEXT,
    stage_scope       TEXT,
    is_ai_reference   INTEGER NOT NULL DEFAULT 0,
    is_human_required INTEGER NOT NULL DEFAULT 0,
    version           TEXT NOT NULL DEFAULT '1.0',
    is_system         INTEGER NOT NULL DEFAULT 0,
    created_by        TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seed: 3 system-level document templates
INSERT OR IGNORE INTO wf_document_templates
    (id, name, doc_type, schema_json, canvas_type, stage_scope, is_ai_reference, is_human_required, version, is_system, created_by)
VALUES
    ('tpl-ndc-naming-standard',
     'NDC Naming Standard',
     'standard',
     '{"fields":[{"key":"artifact_name","label":"Artifact Name","type":"text","required":true},{"key":"prefix","label":"Prefix Code","type":"text","required":true},{"key":"version","label":"Version","type":"text","required":true},{"key":"classification","label":"Classification Marking","type":"text","required":true}]}',
     'NDC', 'design', 1, 0, '1.0', 1, 'system'),
    ('tpl-peer-review-checklist',
     'Peer Review Checklist',
     'checklist',
     '{"fields":[{"key":"requirements_coverage","label":"Requirements Coverage","type":"bool","required":true},{"key":"security_controls","label":"Security Controls Verified","type":"bool","required":true},{"key":"test_coverage","label":"Test Coverage ≥ 80%","type":"bool","required":true},{"key":"comments","label":"Reviewer Comments","type":"textarea","required":false}]}',
     NULL, 'review', 1, 1, '1.0', 1, 'system'),
    ('tpl-security-sign-off-form',
     'Security Sign-Off Form',
     'form',
     '{"fields":[{"key":"control_ids","label":"NIST 800-53 Controls Verified","type":"text","required":true},{"key":"stig_findings","label":"Open STIG Findings","type":"textarea","required":true},{"key":"risk_acceptance","label":"Residual Risk Accepted By","type":"text","required":true},{"key":"ao_signature","label":"AO Signature Block","type":"text","required":true}]}',
     NULL, 'approval', 0, 1, '1.0', 1, 'system');

-- ── Document Submissions (append-only, NIST AU) ───────────────────────────────
CREATE TABLE IF NOT EXISTS wf_document_submissions (
    id              TEXT PRIMARY KEY,
    approval_id     TEXT NOT NULL REFERENCES wf_approvals(id),
    instance_id     TEXT NOT NULL REFERENCES wf_instances(id),
    doc_template_id TEXT NOT NULL REFERENCES wf_document_templates(id),
    stage           TEXT NOT NULL,
    submitted_by    TEXT NOT NULL,
    submission_json TEXT NOT NULL DEFAULT '{}',
    submitted_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Citations ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wf_citations (
    id            TEXT PRIMARY KEY,
    instance_id   TEXT NOT NULL REFERENCES wf_instances(id),
    stage         TEXT,
    source_doc    TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    doc_version   TEXT,
    section       TEXT,
    page_number   TEXT,
    excerpt       TEXT,
    cited_by      TEXT NOT NULL,
    cited_in_type TEXT NOT NULL,
    cited_in_id   TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Additional Indexes ────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_wf_docsub_approval  ON wf_document_submissions(approval_id);
CREATE INDEX IF NOT EXISTS idx_wf_citations_instance ON wf_citations(instance_id);
