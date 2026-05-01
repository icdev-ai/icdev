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
