#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 079 — workflow_hitl (unified HITL approval layer for all ICDEV canvases)."""

from tools.db.storage import get_connection

MIGRATION_ID = "079"
MIGRATION_NAME = "workflow_hitl"
DESCRIPTION = (
    "Policy-driven HITL workflow: templates, teams, instances, approvals, feedback, "
    "external steps, document conformance, citations."
)

_DDL = [
    # ── Workflow Templates ────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS wf_templates (
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
    )""",

    # ── Teams ─────────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS wf_teams (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT,
        canvas_type TEXT,
        created_by  TEXT,
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )""",

    # ── Team Members ──────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS wf_team_members (
        id         TEXT PRIMARY KEY,
        team_id    TEXT NOT NULL REFERENCES wf_teams(id) ON DELETE CASCADE,
        user_id    TEXT NOT NULL,
        role_label TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(team_id, user_id)
    )""",

    # ── Team Assignments ──────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS wf_team_assignments (
        id          TEXT PRIMARY KEY,
        team_id     TEXT NOT NULL REFERENCES wf_teams(id) ON DELETE CASCADE,
        template_id TEXT REFERENCES wf_templates(id),
        scope_type  TEXT NOT NULL CHECK(scope_type IN ('project','task','task_group')),
        scope_id    TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(team_id, scope_type, scope_id)
    )""",

    # ── Workflow Instances ────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS wf_instances (
        id             TEXT PRIMARY KEY,
        template_id    TEXT NOT NULL REFERENCES wf_templates(id),
        task_id        TEXT,
        project_id     TEXT,
        canvas_type    TEXT,
        current_stage  TEXT NOT NULL DEFAULT 'build',
        kickback_count INTEGER NOT NULL DEFAULT 0,
        status         TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active','waiting_external','approved','rejected','escalated','cancelled')),
        created_at     TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
    )""",

    # ── Approval Gates ────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS wf_approvals (
        id          TEXT PRIMARY KEY,
        instance_id TEXT NOT NULL REFERENCES wf_instances(id),
        stage       TEXT NOT NULL,
        team_id     TEXT REFERENCES wf_teams(id),
        assigned_to TEXT,
        status      TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','approved','kickback','conditional','escalated','skipped')),
        due_at      TEXT,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )""",

    # ── Feedback (append-only, NIST AU) ───────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS wf_feedback (
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
    )""",

    # ── Feedback Insights (aggregated by Genesis reflex) ──────────────────────
    """CREATE TABLE IF NOT EXISTS wf_feedback_insights (
        id           TEXT PRIMARY KEY,
        canvas_type  TEXT,
        template_id  TEXT,
        feedback_type TEXT,
        avg_rating   REAL,
        issue_count  INTEGER,
        top_tags     TEXT,
        kickback_rate REAL,
        period_start TEXT NOT NULL,
        period_end   TEXT NOT NULL,
        created_at   TEXT NOT NULL DEFAULT (datetime('now'))
    )""",

    # ── External Steps (email / ticket / wiki) ────────────────────────────────
    """CREATE TABLE IF NOT EXISTS wf_external_steps (
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
    )""",

    # ── Document Templates (checklist / form / SOP / AI standard) ────────────
    """CREATE TABLE IF NOT EXISTS wf_document_templates (
        id               TEXT PRIMARY KEY,
        name             TEXT NOT NULL,
        doc_type         TEXT NOT NULL
            CHECK(doc_type IN ('checklist','form','sop_reference','standard')),
        schema_json      TEXT NOT NULL,
        canvas_type      TEXT,
        stage_scope      TEXT,
        is_ai_reference  INTEGER NOT NULL DEFAULT 0,
        is_human_required INTEGER NOT NULL DEFAULT 0,
        version          TEXT NOT NULL DEFAULT '1',
        is_system        INTEGER NOT NULL DEFAULT 0,
        created_by       TEXT,
        created_at       TEXT NOT NULL DEFAULT (datetime('now'))
    )""",

    # ── Document Submissions (append-only) ────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS wf_document_submissions (
        id              TEXT PRIMARY KEY,
        approval_id     TEXT NOT NULL REFERENCES wf_approvals(id),
        instance_id     TEXT NOT NULL REFERENCES wf_instances(id),
        doc_template_id TEXT NOT NULL REFERENCES wf_document_templates(id),
        stage           TEXT NOT NULL,
        submitted_by    TEXT NOT NULL,
        submission_json TEXT NOT NULL,
        submitted_at    TEXT NOT NULL DEFAULT (datetime('now'))
    )""",

    # ── Citations ─────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS wf_citations (
        id            TEXT PRIMARY KEY,
        instance_id   TEXT NOT NULL REFERENCES wf_instances(id),
        stage         TEXT NOT NULL,
        source_doc    TEXT NOT NULL,
        source_type   TEXT,
        doc_version   TEXT,
        section       TEXT,
        page_number   INTEGER,
        excerpt       TEXT,
        cited_by      TEXT NOT NULL,
        cited_in_type TEXT NOT NULL,
        cited_in_id   TEXT NOT NULL,
        created_at    TEXT NOT NULL DEFAULT (datetime('now'))
    )""",

    # ── Indexes ───────────────────────────────────────────────────────────────
    "CREATE INDEX IF NOT EXISTS idx_wf_instances_task       ON wf_instances(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_wf_instances_project    ON wf_instances(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_wf_approvals_instance   ON wf_approvals(instance_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_wf_feedback_instance    ON wf_feedback(instance_id)",
    "CREATE INDEX IF NOT EXISTS idx_wf_feedback_canvas      ON wf_feedback(canvas_type, submitted_at)",
    "CREATE INDEX IF NOT EXISTS idx_wf_team_assignments_scope ON wf_team_assignments(scope_type, scope_id)",
    "CREATE INDEX IF NOT EXISTS idx_wf_ext_instance         ON wf_external_steps(instance_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_wf_ext_token            ON wf_external_steps(webhook_token)",
    "CREATE INDEX IF NOT EXISTS idx_wf_docsub_approval      ON wf_document_submissions(approval_id)",
    "CREATE INDEX IF NOT EXISTS idx_wf_citations_instance   ON wf_citations(instance_id, stage)",

    # ── kanban_tasks: add hitl_stage column (no-op if already exists) ─────────
    # Handled separately via ALTER TABLE with error suppression.
]

_ALTER_KANBAN = "ALTER TABLE kanban_tasks ADD COLUMN hitl_stage TEXT"

_SEED_DOC_TEMPLATES = [
    {
        "id": "sys-dt-ndc-naming",
        "name": "NDC Device Naming Convention",
        "doc_type": "standard",
        "schema_json": '{"rules":[{"id":"NR-1","text":"Hostnames: <site>-<type><seq>-<env>, e.g. dc1-sw01-prd"},{"id":"NR-2","text":"Interfaces: GigabitEthernetX/Y or Gi0/0 shorthand"},{"id":"NR-3","text":"VLANs: <site_code><function_code><seq>, e.g. DC1SRV010"}],"applies_to":["NDC"]}',
        "canvas_type": "NDC",
        "stage_scope": None,
        "is_ai_reference": 1,
        "is_human_required": 0,
        "version": "1",
        "is_system": 1,
    },
    {
        "id": "sys-dt-peer-review-checklist",
        "name": "Peer Review Checklist",
        "doc_type": "checklist",
        "schema_json": '[{"item":"Design meets stated requirements","required":true},{"item":"Security controls are addressed","required":true},{"item":"Documentation is complete","required":true},{"item":"Tests cover the happy path","required":true},{"item":"Edge cases are handled","required":false}]',
        "canvas_type": None,
        "stage_scope": "review",
        "is_ai_reference": 0,
        "is_human_required": 1,
        "version": "1",
        "is_system": 1,
    },
    {
        "id": "sys-dt-security-sign-off",
        "name": "Security Sign-Off Form",
        "doc_type": "form",
        "schema_json": '[{"field":"Reviewer Name","type":"text","required":true},{"field":"Review Date","type":"date","required":true},{"field":"Security Findings","type":"textarea","required":false},{"field":"Risk Level","type":"select","options":["Low","Medium","High","Critical"],"required":true},{"field":"Approved","type":"select","options":["Yes","No","Conditional"],"required":true}]',
        "canvas_type": None,
        "stage_scope": "approve",
        "is_ai_reference": 0,
        "is_human_required": 1,
        "version": "1",
        "is_system": 1,
    },
]


def run():
    conn = get_connection()
    try:
        for ddl in _DDL:
            try:
                conn.execute(ddl)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    raise
        # Add hitl_stage to kanban_tasks — ignore if column exists
        try:
            conn.execute(_ALTER_KANBAN)
        except Exception:
            pass
        # Seed system document templates
        for tmpl in _SEED_DOC_TEMPLATES:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO wf_document_templates
                       (id, name, doc_type, schema_json, canvas_type, stage_scope,
                        is_ai_reference, is_human_required, version, is_system)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        tmpl["id"], tmpl["name"], tmpl["doc_type"], tmpl["schema_json"],
                        tmpl["canvas_type"], tmpl["stage_scope"], tmpl["is_ai_reference"],
                        tmpl["is_human_required"], tmpl["version"], tmpl["is_system"],
                    ),
                )
            except Exception:
                pass
        try:
            conn.commit()
        except Exception:
            pass
        print(f"Migration {MIGRATION_ID} ({MIGRATION_NAME}) applied successfully.")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
