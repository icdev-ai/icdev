"""Minimal conftest for worktree task-8a35ad18d2 tests."""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure the main repo root is on sys.path so tools/icdev packages resolve.
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Force SQLite backend for tests (override .env PostgreSQL setting — same as main conftest)
os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"
os.environ["NOCC_STORAGE_BACKEND"] = "sqlite"
os.environ["PMC_STORAGE_BACKEND"] = "sqlite"
os.environ["CCC_STORAGE_BACKEND"] = "sqlite"
os.environ["DSOC_STORAGE_BACKEND"] = "sqlite"


MINIMAL_ICDEV_SCHEMA = """
CREATE TABLE IF NOT EXISTS studio_workflows (
    workflow_id   TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT DEFAULT '',
    template_yaml TEXT NOT NULL DEFAULT '',
    category      TEXT DEFAULT 'custom',
    shared        INTEGER DEFAULT 0,
    created_by    TEXT,
    version       INTEGER DEFAULT 1,
    created_at    TEXT,
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS studio_workflow_runs (
    run_id         TEXT PRIMARY KEY,
    workflow_id    TEXT NOT NULL,
    workflow_name  TEXT,
    status         TEXT DEFAULT 'pending',
    started_at     TEXT,
    completed_at   TEXT,
    triggered_by   TEXT,
    project_id     TEXT DEFAULT 'default',
    summary_json   TEXT,
    FOREIGN KEY (workflow_id) REFERENCES studio_workflows(workflow_id)
);
CREATE TABLE IF NOT EXISTS studio_workflow_run_steps (
    step_run_id  TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    step_id      TEXT NOT NULL DEFAULT '',
    step_name    TEXT DEFAULT '',
    tool         TEXT,
    status       TEXT DEFAULT 'pending',
    exit_code    INTEGER,
    stdout       TEXT DEFAULT '',
    stderr       TEXT DEFAULT '',
    duration_ms  INTEGER DEFAULT 0,
    started_at   TEXT,
    completed_at TEXT,
    FOREIGN KEY (run_id) REFERENCES studio_workflow_runs(run_id)
);
CREATE TABLE IF NOT EXISTS kanban_tasks (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    description           TEXT,
    task_type             TEXT DEFAULT 'build',
    priority              TEXT DEFAULT 'high',
    status                TEXT DEFAULT 'backlog',
    scheduled_at          TEXT,
    created_at            TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at            TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at          TEXT,
    executor_type         TEXT DEFAULT 'claude_cli',
    execution_id          TEXT,
    executor_url          TEXT,
    depends_on_task_id    TEXT,
    source_prediction_id  TEXT,
    failure_count         INTEGER DEFAULT 0,
    last_failure_reason   TEXT,
    last_failure_at       TEXT,
    dispatch_source       TEXT DEFAULT 'unknown',
    hitl_stage            TEXT,
    start_date            TEXT,
    target_date           TEXT,
    files_changed         INTEGER DEFAULT 0,
    lines_added           INTEGER DEFAULT 0,
    lines_removed         INTEGER DEFAULT 0,
    completed_via_bypass  INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kanban_task_deps (
    task_id         TEXT NOT NULL,
    depends_on_id   TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (task_id, depends_on_id),
    FOREIGN KEY (task_id)       REFERENCES kanban_tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_id) REFERENCES kanban_tasks(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS kanban_executions (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    status      TEXT DEFAULT 'pending',
    started_at  TEXT,
    finished_at TEXT,
    output      TEXT,
    error       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kanban_verifications (
    id                    TEXT PRIMARY KEY,
    task_id               TEXT NOT NULL,
    verified_at           TEXT NOT NULL,
    result                TEXT NOT NULL,
    reason                TEXT,
    output_length         INTEGER DEFAULT 0,
    fail_markers_found    TEXT,
    claimed_paths         INTEGER DEFAULT 0,
    existing_paths        INTEGER DEFAULT 0,
    phantom_ratio         REAL DEFAULT 0,
    git_commits           INTEGER DEFAULT 0,
    specific_checks       TEXT,
    codelens_passed       INTEGER,
    ruff_issues           INTEGER,
    bandit_issues         INTEGER,
    pytest_passed         INTEGER,
    failed_tests          TEXT,
    coherence_passed      INTEGER,
    coherence_violations  TEXT,
    e2e_ran               INTEGER,
    e2e_passed            INTEGER,
    e2e_errors            TEXT,
    companion_synced      INTEGER,
    created_at            TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kanban_status_transitions (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    actor        TEXT,
    reason       TEXT,
    recorded_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    classification TEXT DEFAULT 'CUI',
    status TEXT DEFAULT 'active',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    status TEXT DEFAULT 'active',
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    added_by TEXT,
    added_at TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id)
);
CREATE TABLE IF NOT EXISTS group_roles (
    group_id TEXT NOT NULL,
    role TEXT NOT NULL,
    canvas_scope TEXT,
    granted_by TEXT,
    granted_at TEXT,
    PRIMARY KEY (group_id, role, canvas_scope)
);
CREATE TABLE IF NOT EXISTS canvas_access_grants (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    principal_type TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    canvas_name TEXT NOT NULL,
    access_level TEXT NOT NULL DEFAULT 'read',
    granted_by TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    UNIQUE (tenant_id, principal_type, principal_id, canvas_name)
);
CREATE TABLE IF NOT EXISTS abac_decisions (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    role TEXT,
    tenant_id TEXT,
    resource TEXT,
    action TEXT,
    policy_matched TEXT,
    decision TEXT,
    reason TEXT,
    evaluated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_mfa (
    user_id TEXT PRIMARY KEY,
    totp_secret TEXT NOT NULL,
    enrolled_at TEXT NOT NULL,
    backup_codes TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS mfa_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 0,
    method TEXT NOT NULL DEFAULT 'totp',
    ip_address TEXT,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    action TEXT NOT NULL,
    resource TEXT,
    details TEXT,
    classification TEXT DEFAULT 'CUI',
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_risk_log (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    risk_score REAL,
    details TEXT,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gateway_rate_limits (
    key TEXT NOT NULL,
    window_start INTEGER NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (key, window_start)
);
CREATE TABLE IF NOT EXISTS zta_drift_alerts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    previous_score REAL NOT NULL,
    current_score REAL NOT NULL,
    drift_pct REAL NOT NULL,
    alert_level TEXT NOT NULL DEFAULT 'warning',
    acknowledged INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canvas_ai_decisions (
    id              TEXT PRIMARY KEY,
    canvas_type     TEXT NOT NULL,
    record_id       TEXT,
    decision_type   TEXT NOT NULL,
    decision        TEXT NOT NULL,
    rationale       TEXT,
    model_used      TEXT,
    confidence      REAL,
    alternatives    TEXT DEFAULT '[]',
    trace_id        TEXT,
    span_id         TEXT,
    actor           TEXT NOT NULL DEFAULT 'icdev-system',
    project_id      TEXT,
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS cpmp_contracts (
    id TEXT PRIMARY KEY,
    contract_number TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    agency TEXT NOT NULL DEFAULT '',
    cor_name TEXT,
    cor_email TEXT,
    cor_phone TEXT,
    contract_type TEXT NOT NULL DEFAULT 'FFP',
    idiq_contract_id TEXT,
    task_order_number TEXT,
    naics_code TEXT,
    total_value REAL DEFAULT 0.0,
    funded_value REAL DEFAULT 0.0,
    ceiling_value REAL,
    billed_value REAL DEFAULT 0.0,
    pop_start TEXT,
    pop_end TEXT,
    status TEXT DEFAULT 'draft',
    health TEXT DEFAULT 'green',
    health_score REAL,
    cpars_rating_current TEXT,
    opportunity_id TEXT,
    customer_delivery_id TEXT,
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    created_by TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS cpmp_clins (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    clin_number TEXT NOT NULL DEFAULT '',
    description TEXT,
    clin_type TEXT NOT NULL DEFAULT 'labor',
    total_value REAL DEFAULT 0.0,
    funded_value REAL DEFAULT 0.0,
    billed_value REAL DEFAULT 0.0,
    pop_start TEXT,
    pop_end TEXT,
    status TEXT DEFAULT 'active',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS cpmp_wbs (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    parent_id TEXT,
    wbs_number TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    description TEXT,
    level INTEGER DEFAULT 1,
    budget_at_completion REAL DEFAULT 0.0,
    pv_cumulative REAL DEFAULT 0.0,
    ev_cumulative REAL DEFAULT 0.0,
    ac_cumulative REAL DEFAULT 0.0,
    percent_complete REAL DEFAULT 0.0,
    planned_start TEXT,
    planned_finish TEXT,
    actual_start TEXT,
    actual_finish TEXT,
    responsible_person TEXT,
    status TEXT DEFAULT 'not_started',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS cpmp_deliverables (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    clin_id TEXT,
    wbs_id TEXT,
    cdrl_number TEXT,
    did_number TEXT,
    title TEXT NOT NULL DEFAULT '',
    description TEXT,
    deliverable_type TEXT NOT NULL DEFAULT 'cdrl',
    frequency TEXT,
    due_date TEXT,
    submitted_date TEXT,
    accepted_date TEXT,
    rejected_date TEXT,
    rejection_reason TEXT,
    status TEXT DEFAULT 'not_started',
    days_overdue INTEGER DEFAULT 0,
    generated_by_tool TEXT,
    output_path TEXT,
    reviewer TEXT,
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS cpmp_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_by TEXT,
    reason TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cpmp_evm_periods (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    wbs_id TEXT,
    period_date TEXT NOT NULL,
    budget_at_completion REAL DEFAULT 0.0,
    bac REAL DEFAULT 0.0,
    planned_value REAL DEFAULT 0.0,
    pv REAL DEFAULT 0.0,
    earned_value REAL DEFAULT 0.0,
    ev REAL DEFAULT 0.0,
    actual_cost REAL DEFAULT 0.0,
    ac REAL DEFAULT 0.0,
    bcws REAL DEFAULT 0.0,
    bcwp REAL DEFAULT 0.0,
    acwp REAL DEFAULT 0.0,
    cpi REAL,
    spi REAL,
    cost_variance REAL,
    cv REAL,
    schedule_variance REAL,
    sv REAL,
    eac REAL,
    etc REAL,
    vac REAL,
    tcpi REAL,
    source TEXT DEFAULT 'manual',
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS cpmp_subcontractors (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    cage_code TEXT,
    uei TEXT,
    business_size TEXT,
    business_type TEXT,
    subcontract_type TEXT,
    subcontract_value REAL DEFAULT 0.0,
    billed_value REAL DEFAULT 0.0,
    performance_rating TEXT,
    flow_down_complete INTEGER DEFAULT 0,
    flowdown_verified INTEGER DEFAULT 0,
    cybersecurity_compliant INTEGER DEFAULT 0,
    cmmc_level INTEGER,
    isr_ssr_current INTEGER DEFAULT 0,
    contact_name TEXT,
    contact_email TEXT,
    status TEXT DEFAULT 'active',
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS cpmp_cpars_assessments (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    quality_rating REAL,
    schedule_rating REAL,
    cost_rating REAL,
    management_rating REAL,
    small_business_rating REAL,
    overall_rating TEXT,
    overall_score REAL,
    predicted_overall TEXT,
    predicted_score REAL,
    narrative TEXT,
    government_narrative TEXT,
    negative_event_count INTEGER DEFAULT 0,
    corrective_actions_completed INTEGER DEFAULT 0,
    status TEXT DEFAULT 'draft',
    submitted_date TEXT,
    finalized_date TEXT,
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS cpmp_negative_events (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence TEXT,
    deliverable_id TEXT,
    subcontractor_id TEXT,
    corrective_action TEXT,
    corrective_action_status TEXT DEFAULT 'open',
    corrective_action_due TEXT,
    cpars_impact REAL DEFAULT 0.0,
    detected_by TEXT,
    reported_by TEXT,
    reported_date TEXT DEFAULT (datetime('now')),
    resolved_date TEXT,
    source_entity_type TEXT,
    source_entity_id TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS cpmp_small_business_plan (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    reporting_period TEXT NOT NULL,
    report_type TEXT NOT NULL DEFAULT 'isr',
    total_subcontract_dollars REAL DEFAULT 0.0,
    sb_goal_pct REAL DEFAULT 0.0,
    sb_actual_pct REAL DEFAULT 0.0,
    sb_actual_dollars REAL DEFAULT 0.0,
    sdb_goal_pct REAL DEFAULT 0.0,
    sdb_actual_pct REAL DEFAULT 0.0,
    sdb_actual_dollars REAL DEFAULT 0.0,
    wosb_goal_pct REAL DEFAULT 0.0,
    wosb_actual_pct REAL DEFAULT 0.0,
    wosb_actual_dollars REAL DEFAULT 0.0,
    hubzone_goal_pct REAL DEFAULT 0.0,
    hubzone_actual_pct REAL DEFAULT 0.0,
    hubzone_actual_dollars REAL DEFAULT 0.0,
    sdvosb_goal_pct REAL DEFAULT 0.0,
    sdvosb_actual_pct REAL DEFAULT 0.0,
    sdvosb_actual_dollars REAL DEFAULT 0.0,
    compliant INTEGER DEFAULT 0,
    submitted_date TEXT,
    status TEXT DEFAULT 'draft',
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS cpmp_cdrl_generations (
    id TEXT PRIMARY KEY,
    deliverable_id TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    cdrl_type TEXT NOT NULL,
    generation_tool TEXT NOT NULL,
    tool_args TEXT DEFAULT '{}',
    output_path TEXT,
    output_hash TEXT,
    file_size_bytes INTEGER,
    status TEXT DEFAULT 'generated',
    error_message TEXT,
    generated_by TEXT,
    reviewed_by TEXT,
    approved_by TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS cpmp_sam_contract_awards (
    id TEXT PRIMARY KEY,
    sam_award_id TEXT NOT NULL UNIQUE,
    piid TEXT,
    referenced_idv_piid TEXT,
    award_type TEXT,
    awardee_name TEXT,
    awardee_uei TEXT,
    awardee_cage TEXT,
    awarding_agency TEXT,
    awarding_sub_agency TEXT,
    funding_agency TEXT,
    naics_code TEXT,
    psc_code TEXT,
    obligation_amount REAL,
    base_exercised_options_value REAL,
    total_dollars_obligated REAL,
    award_date TEXT,
    pop_start TEXT,
    pop_end TEXT,
    place_of_performance TEXT,
    linked_contract_id TEXT,
    content_hash TEXT NOT NULL,
    raw_json TEXT,
    metadata TEXT DEFAULT '{}',
    discovered_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS cpmp_cor_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    user_email TEXT,
    contract_id TEXT NOT NULL,
    action TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cpmp_milestones (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    wbs_id TEXT,
    title TEXT NOT NULL DEFAULT '',
    description TEXT,
    baseline_date TEXT,
    forecast_date TEXT,
    actual_date TEXT,
    status TEXT DEFAULT 'pending',
    evm_period_id TEXT,
    responsible_person TEXT,
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS cpmp_milestone_deps (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    predecessor_id TEXT NOT NULL,
    successor_id TEXT NOT NULL,
    lag_days INTEGER DEFAULT 0,
    dep_type TEXT DEFAULT 'finish_to_start',
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS cpmp_contract_mods (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    mod_number INTEGER NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('admin','funding','scope','pop')),
    description TEXT NOT NULL DEFAULT '',
    value_delta REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested','in_review','approved','rejected','executed')),
    requested_by TEXT,
    requested_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    reviewed_by TEXT,
    reviewed_at TEXT,
    approved_by TEXT,
    approved_at TEXT,
    rejection_reason TEXT,
    effective_date TEXT,
    executed_at TEXT,
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);
CREATE INDEX IF NOT EXISTS idx_cpmp_contract_mods_contract ON cpmp_contract_mods(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_contract_mods_status ON cpmp_contract_mods(status);
CREATE TABLE IF NOT EXISTS cpmp_risks (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'other',
    probability INTEGER NOT NULL DEFAULT 3,
    impact INTEGER NOT NULL DEFAULT 3,
    exposure INTEGER NOT NULL DEFAULT 9,
    mitigation TEXT,
    owner TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    milestone_id TEXT,
    negative_event_id TEXT,
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cpmp_risk_contract ON cpmp_risks(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_risk_status ON cpmp_risks(status);
CREATE TABLE IF NOT EXISTS pg_capture_gate_decisions (
    id TEXT PRIMARY KEY,
    capture_plan_id TEXT NOT NULL,
    opportunity_id TEXT,
    from_phase TEXT NOT NULL,
    to_phase TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT,
    decided_by TEXT,
    gate_criteria_met TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dic_handoff_sessions (
    session_id          TEXT    PRIMARY KEY,
    departing_owner_id  TEXT    NOT NULL,
    successor_owner_id  TEXT    NOT NULL,
    dest_collection_id  TEXT    NOT NULL,
    title               TEXT    DEFAULT '',
    status              TEXT    DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    agenda_count        INTEGER DEFAULT 0,
    answered_count      INTEGER DEFAULT 0,
    generated_count     INTEGER DEFAULT 0,
    orphan_count        INTEGER DEFAULT 0,
    created_by          TEXT    DEFAULT 'system',
    created_at          TEXT    DEFAULT (datetime('now')),
    updated_at          TEXT    DEFAULT (datetime('now')),
    tenant_id           TEXT    DEFAULT 'default',
    classification      TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_handoff_sessions_tenant ON dic_handoff_sessions(tenant_id);
CREATE TABLE IF NOT EXISTS dic_handoff_items (
    item_id         TEXT    PRIMARY KEY,
    session_id      TEXT    NOT NULL,
    item_kind       TEXT    NOT NULL DEFAULT 'interview' CHECK (item_kind IN ('interview', 'generated_doc', 'orphan_flag')),
    finding_id      TEXT    DEFAULT '',
    finding_type    TEXT    DEFAULT '',
    severity        TEXT    DEFAULT '',
    entity_ref      TEXT    DEFAULT '',
    topic           TEXT    DEFAULT '',
    prompt          TEXT    DEFAULT '',
    answer_text     TEXT    DEFAULT '',
    doc_id          TEXT    DEFAULT '',
    version_id      TEXT    DEFAULT '',
    verified        INTEGER DEFAULT 0,
    abstained       INTEGER DEFAULT 0,
    status          TEXT    DEFAULT 'pending' CHECK (status IN ('pending', 'answered', 'generated')),
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now')),
    tenant_id       TEXT    DEFAULT 'default',
    classification  TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_handoff_items_session ON dic_handoff_items(session_id);
CREATE TABLE IF NOT EXISTS dd_mapping_sessions (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT 'Untitled Mapping',
    source_format   TEXT NOT NULL DEFAULT 'json_schema',
    target_format   TEXT NOT NULL DEFAULT 'sql_ddl',
    source_schema_json TEXT DEFAULT '{}',
    target_schema_json TEXT DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending',
    field_count     INTEGER DEFAULT 0,
    confirmed_count INTEGER DEFAULT 0,
    rejected_count  INTEGER DEFAULT 0,
    classification  TEXT NOT NULL DEFAULT 'CUI',
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    created_by      TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dd_field_mappings (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    source_field    TEXT NOT NULL,
    source_type     TEXT DEFAULT '',
    source_path     TEXT DEFAULT '',
    target_field    TEXT NOT NULL,
    target_type     TEXT DEFAULT '',
    target_path     TEXT DEFAULT '',
    confidence      REAL NOT NULL DEFAULT 0.0,
    match_method    TEXT DEFAULT 'name',
    status          TEXT NOT NULL DEFAULT 'pending',
    transform_expr  TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dd_mapping_transforms (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    artifact_type   TEXT NOT NULL DEFAULT 'sql',
    artifact_text   TEXT NOT NULL DEFAULT '',
    field_count     INTEGER DEFAULT 0,
    generated_by    TEXT DEFAULT 'ai',
    model_used      TEXT DEFAULT '',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS zig_pillars (
    slug            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    full_name       TEXT,
    pillar_weight   REAL DEFAULT 0.14,
    icon            TEXT,
    color           TEXT,
    csi_url         TEXT,
    description     TEXT,
    ficam_components TEXT DEFAULT '[]',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS zig_capabilities (
    id              TEXT PRIMARY KEY,
    pillar_slug     TEXT NOT NULL,
    title           TEXT NOT NULL,
    phase           TEXT NOT NULL,
    maturity_level  TEXT NOT NULL,
    description     TEXT,
    nist_controls   TEXT DEFAULT '[]',
    target_fy2027   INTEGER DEFAULT 1,
    implementation_status TEXT DEFAULT 'not_started',
    evidence_note   TEXT,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS zig_activities (
    id              TEXT PRIMARY KEY,
    capability_id   TEXT NOT NULL,
    phase           TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    nist_control_ref TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS zig_activity_completions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id     TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'not_started',
    evidence_note   TEXT,
    completed_by    TEXT,
    completed_at    TEXT,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS zig_maturity_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pillar_slug     TEXT NOT NULL,
    score           REAL NOT NULL DEFAULT 0.0,
    maturity_level  TEXT,
    capability_count INTEGER DEFAULT 0,
    activity_count  INTEGER DEFAULT 0,
    complete_activities INTEGER DEFAULT 0,
    assessment_run_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS slides_decks (
    deck_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    deck_type     TEXT NOT NULL DEFAULT 'executive_overview',
    theme         TEXT NOT NULL DEFAULT 'midnight_executive',
    status        TEXT NOT NULL DEFAULT 'pending',
    source_types  TEXT DEFAULT '[]',
    pptx_path     TEXT,
    slide_count   INTEGER DEFAULT 0,
    error_message TEXT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at  DATETIME
);
CREATE TABLE IF NOT EXISTS slides_slides (
    slide_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id       INTEGER NOT NULL REFERENCES slides_decks(deck_id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    slide_type    TEXT NOT NULL DEFAULT 'content',
    title         TEXT NOT NULL,
    bullets       TEXT DEFAULT '[]',
    speaker_notes TEXT,
    image_path    TEXT,
    image_prompt  TEXT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS slides_audit (
    audit_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id   INTEGER REFERENCES slides_decks(deck_id),
    action    TEXT NOT NULL,
    actor     TEXT DEFAULT 'system',
    details   TEXT,
    ts        DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def icdev_db(tmp_path):
    """Temporary SQLite DB for use-case tests; studio tables pre-created."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def nocc_db(tmp_path, monkeypatch):
    """In-memory SQLite NOCC DB for unit tests."""
    db_path = tmp_path / "noc_canvas.db"
    monkeypatch.setenv("NOCC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("NOCC_DB_PATH", str(db_path))
    from tools.noc_canvas.db.init_db import init_db, get_connection
    init_db()
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture
def ccc_db(tmp_path, monkeypatch):
    """In-memory SQLite CCC DB for unit tests."""
    db_path = tmp_path / "ccc_canvas.db"
    monkeypatch.setenv("CCC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("CCC_DB_PATH", str(db_path))
    from tools.ccc_canvas.db.init_db import init_db, get_connection
    init_db()
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture
def dsoc_db(tmp_path, monkeypatch):
    """In-memory SQLite DSOC DB for unit tests."""
    db_path = tmp_path / "dsoc_canvas.db"
    monkeypatch.setenv("DSOC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("DSOC_DB_PATH", str(db_path))
    from tools.dsoc_canvas.db.init_db import init_db, get_connection
    init_db()
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture
def pmc_db(tmp_path, monkeypatch):
    """In-memory SQLite PMC DB for unit tests."""
    db_path = tmp_path / "pmc_canvas.db"
    monkeypatch.setenv("PMC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("PMC_DB_PATH", str(db_path))
    from tools.pmc_canvas.db.init_db import init_db, get_connection
    init_db()
    conn = get_connection()
    yield conn
    conn.close()
