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

# Force SQLite backend for tests (override .env PostgreSQL setting — same as main
# conftest) UNLESS the opt-in ICDEV_PYTEST_PG flag is set. The dedicated CI
# "test-pg" tier (.github/workflows/icdev-ci.yml) sets ICDEV_PYTEST_PG=1 +
# ICDEV_STORAGE_BACKEND=postgresql and runs a CURATED allowlist
# (tests/pg_tier_allowlist.txt) against a live PostgreSQL service so PG-native
# `%s` / portability bugs fail LOUDLY — on SQLite they are silently masked by
# translate_sql. Default (flag absent) preserves the SQLite-forced behaviour for
# the whole normal suite.
_PYTEST_PG = os.environ.get("ICDEV_PYTEST_PG", "").lower() in ("1", "true", "yes")
if _PYTEST_PG:
    os.environ.setdefault("ICDEV_STORAGE_BACKEND", "postgresql")
    # FAIL-CLOSED: without this, get_connection() silently falls back to SQLite
    # when PG is unreachable — the PG tier would go green while testing NOTHING on
    # PG (the exact false-confidence trap this tier exists to prevent). No-fallback
    # makes an unreachable/misconfigured PG raise loudly.
    os.environ["ICDEV_PG_NO_FALLBACK"] = "1"
else:
    os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"
    os.environ["NOCC_STORAGE_BACKEND"] = "sqlite"
    os.environ["PMC_STORAGE_BACKEND"] = "sqlite"
    os.environ["CCC_STORAGE_BACKEND"] = "sqlite"
    os.environ["DSOC_STORAGE_BACKEND"] = "sqlite"

# cnr-plat-01 / cnr-plat-03: keep the two new fail-closed-by-default platform
# gates (CSRF on cookie-authed mutating APIs; canvas access enforcement) OPT-OUT
# during the test suite so the many existing dashboard/canvas tests that use
# logged-in sessions (session_transaction) or unauthenticated test clients keep
# passing; the dedicated cnr-plat tests re-enable them per-test via
# monkeypatch.setenv. An explicit env value still wins.
os.environ.setdefault("ICDEV_CSRF_ENFORCE", "0")
os.environ.setdefault("ICDEV_CANVAS_ACCESS_OPEN", "true")


MINIMAL_ICDEV_SCHEMA = """
CREATE TABLE IF NOT EXISTS web_fetch_provenance (
    id TEXT PRIMARY KEY,
    citation_id TEXT,
    requested_url TEXT NOT NULL,
    final_url TEXT,
    http_status INTEGER,
    content_hash TEXT NOT NULL,
    content_type TEXT,
    content_length INTEGER,
    etag TEXT,
    last_modified TEXT,
    fetched_at TEXT NOT NULL,
    fetcher TEXT,
    classification TEXT DEFAULT 'CUI',
    project_id TEXT,
    tenant_id TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}'
);

-- Dashboard auth: the before_request hook validates session user_id against
-- dashboard_users; route tests set session["user_id"]="test-admin".
CREATE TABLE IF NOT EXISTS dashboard_users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE, display_name TEXT,
    role TEXT DEFAULT 'admin', status TEXT DEFAULT 'active',
    created_by TEXT, created_at TIMESTAMP, updated_at TIMESTAMP
);
INSERT OR IGNORE INTO dashboard_users (id, email, display_name, role)
VALUES ('test-admin', 'admin@test.local', 'Test Admin', 'admin');
CREATE TABLE IF NOT EXISTS dashboard_api_keys (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, key_hash TEXT NOT NULL,
    key_prefix TEXT NOT NULL, label TEXT, status TEXT NOT NULL DEFAULT 'active',
    last_used_at TIMESTAMP, expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, revoked_at TIMESTAMP, revoked_by TEXT
);
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
CREATE TABLE IF NOT EXISTS studio_run_memory (
    run_id     TEXT NOT NULL,
    key        TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, key)
);
CREATE TABLE IF NOT EXISTS studio_event_sources (
    source_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    config_json TEXT,
    enabled     INTEGER DEFAULT 1,
    created_by  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS studio_workflow_triggers (
    trigger_id        TEXT PRIMARY KEY,
    source_id         TEXT NOT NULL,
    workflow_id       TEXT NOT NULL,
    event_type        TEXT,
    filter_json       TEXT,
    input_mapping_json TEXT,
    enabled           INTEGER DEFAULT 1,
    created_at        TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS studio_trigger_events (
    event_id     TEXT PRIMARY KEY,
    source_id    TEXT,
    trigger_id   TEXT,
    event_type   TEXT,
    payload_json TEXT,
    matched      INTEGER DEFAULT 0,
    run_id       TEXT,
    reason       TEXT,
    received_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS studio_mcp_dispatch_audit (
    audit_id       TEXT PRIMARY KEY,
    run_id         TEXT,
    step_id        TEXT,
    tool           TEXT NOT NULL,
    params_sha256  TEXT NOT NULL,
    principal_id   TEXT,
    tenant_id      TEXT,
    caller_il      TEXT,
    caller_roles   TEXT,
    caller_source  TEXT,
    decision       TEXT NOT NULL
                   CHECK(decision IN ('allowed','refused','pending_approval')),
    reason         TEXT NOT NULL,
    detail         TEXT,
    classification TEXT NOT NULL,
    recorded_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kanban_tasks (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    description           TEXT,
    task_type             TEXT DEFAULT 'build',
    priority              TEXT DEFAULT 'high',
    status                TEXT DEFAULT 'backlog',
    project_id            TEXT DEFAULT 'default',
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
    trace_id              TEXT,
    span_id               TEXT,
    hitl_stage            TEXT,
    start_date            TEXT,
    target_date           TEXT,
    files_changed         INTEGER DEFAULT 0,
    lines_added           INTEGER DEFAULT 0,
    lines_removed         INTEGER DEFAULT 0,
    completed_via_bypass  INTEGER DEFAULT 0,
    due_date              TEXT,
    sla_hours             INTEGER
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
CREATE TABLE IF NOT EXISTS proposal_section_drafts (
    id             TEXT PRIMARY KEY,
    opportunity_id TEXT,
    draft_content  TEXT,
    tenant_id      TEXT DEFAULT 'default',
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS proposal_opportunities (
    id                   TEXT PRIMARY KEY,
    solicitation_number  TEXT,
    title                TEXT,
    agency               TEXT,
    naics_code           TEXT,
    status               TEXT DEFAULT 'open',
    created_at           TEXT,
    updated_at           TEXT,
    tenant_id            TEXT DEFAULT 'default',
    classification       TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS pg_cost_volumes (
    id                    TEXT PRIMARY KEY,
    opportunity_id        TEXT NOT NULL,
    contract_type         TEXT,
    pricing_strategy      TEXT,
    total_evaluated_price REAL,
    direct_labor_cost     REAL,
    fringe_rate           REAL,
    overhead_rate         REAL,
    g_and_a_rate          REAL,
    fee_rate              REAL,
    subcontractor_cost    REAL,
    odc_cost              REAL,
    ptw_estimate_low      REAL,
    ptw_estimate_high     REAL,
    calc_benchmark_median REAL,
    status                TEXT,
    created_at            TEXT,
    updated_at            TEXT,
    tenant_id             TEXT DEFAULT 'default',
    classification        TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS pg_lcat_allocations (
    id                TEXT PRIMARY KEY,
    cost_volume_id    TEXT NOT NULL,
    task_description  TEXT NOT NULL,
    labor_category    TEXT NOT NULL,
    bls_soc_code      TEXT,
    fte_count         REAL,
    hourly_rate       REAL,
    annual_cost       REAL,
    basis_of_estimate TEXT,
    created_at        TEXT,
    tenant_id         TEXT DEFAULT 'default',
    classification    TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS proposal_key_personnel (
    id                    TEXT PRIMARY KEY,
    opportunity_id        TEXT NOT NULL,
    person_ref            TEXT NOT NULL,
    name                  TEXT NOT NULL,
    proposed_lcat         TEXT NOT NULL,
    qualification_verdict TEXT NOT NULL CHECK(qualification_verdict IN ('qualified', 'gap', 'exceeds')),
    evidence_json         TEXT NOT NULL CHECK(evidence_json <> '' AND evidence_json <> '[]'),
    source                TEXT CHECK(source IS NULL OR source IN ('compass', 'manual', 'resume_match', 'scraped')),
    key_person            INTEGER NOT NULL DEFAULT 0,
    gaps_json             TEXT NOT NULL DEFAULT '[]',
    tenant_id             TEXT NOT NULL DEFAULT 'default',
    classification        TEXT NOT NULL DEFAULT 'CUI',
    created_at            TIMESTAMP,
    updated_at            TIMESTAMP,
    UNIQUE (opportunity_id, person_ref)
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
    review_passed         INTEGER,
    review_findings       TEXT,
    pytest_ran            INTEGER DEFAULT 0,
    dispatch_source       TEXT DEFAULT 'unknown',
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
-- Continuous Harness eval table (mirrors tools/db/schema/pg_consolidated.sql).
CREATE TABLE IF NOT EXISTS harness_eval (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL DEFAULT '',
    reflex         TEXT NOT NULL,
    decision       TEXT NOT NULL,
    confidence     REAL,
    metadata_json  TEXT DEFAULT '{}',
    actual_outcome TEXT,
    resolved_at    TEXT,
    created_at     TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS tenant_component_overrides (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    component_key TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    updated_by TEXT DEFAULT 'system',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (tenant_id, component_key)
);
CREATE TABLE IF NOT EXISTS component_audit_log (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system',
    tenant_id TEXT,
    component_key TEXT,
    profile_name TEXT,
    details TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI',
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_component_audit_log_event ON component_audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_component_audit_log_component ON component_audit_log(component_key);
CREATE INDEX IF NOT EXISTS idx_component_audit_log_recorded_at ON component_audit_log(recorded_at);
CREATE TABLE IF NOT EXISTS constitutional_audit_log (
    id TEXT PRIMARY KEY,
    artifact_type TEXT DEFAULT '',
    rule_id TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warn',
    verdict TEXT NOT NULL,
    offending_span TEXT DEFAULT '',
    rationale TEXT DEFAULT '',
    revised INTEGER DEFAULT 0,
    vocabulary_version TEXT DEFAULT 'const-1.0',
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_constitutional_audit_rule ON constitutional_audit_log(rule_id, verdict);
CREATE INDEX IF NOT EXISTS idx_constitutional_audit_recorded_at ON constitutional_audit_log(recorded_at);
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
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id          TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL DEFAULT 'default',
    onboarding_state TEXT NOT NULL DEFAULT '{}',
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
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
CREATE TABLE IF NOT EXISTS canvas_kg_nodes (
    id            TEXT PRIMARY KEY,
    canvas        TEXT NOT NULL,
    design_id     TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    node_type     TEXT,
    label         TEXT,
    metadata_json TEXT,
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS canvas_kg_edges (
    id            TEXT PRIMARY KEY,
    canvas        TEXT NOT NULL,
    design_id     TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    edge_type     TEXT,
    metadata_json TEXT,
    updated_at    TEXT
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
    classification TEXT DEFAULT 'CUI',
    compartments TEXT NOT NULL DEFAULT '[]',
    pop_base_end TEXT,
    option_years INTEGER DEFAULT 0,
    tenant_id TEXT
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
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
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
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
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
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
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
    created_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
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
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
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
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
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
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
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
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
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
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
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
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
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
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
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
    created_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
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
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
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
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
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
CREATE TABLE IF NOT EXISTS cpmp_budget_allocations (
    id TEXT PRIMARY KEY,
    initiative_code TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    fiscal_year INTEGER NOT NULL,
    tier TEXT NOT NULL CHECK(tier IN ('tier_1', 'tier_2')),
    allocated_usd REAL NOT NULL DEFAULT 0.0,
    obligated_usd REAL NOT NULL DEFAULT 0.0,
    available_usd REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','depleted','deferred','cancelled')),
    agency TEXT NOT NULL DEFAULT '',
    contract_id TEXT,
    owner TEXT NOT NULL DEFAULT '',
    justification TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE(initiative_code, fiscal_year)
);
CREATE TABLE IF NOT EXISTS cpmp_budget_obligations (
    id TEXT PRIMARY KEY,
    allocation_id TEXT NOT NULL,
    amount_usd REAL NOT NULL DEFAULT 0.0,
    description TEXT NOT NULL DEFAULT '',
    reference_id TEXT,
    recorded_by TEXT,
    recorded_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);
CREATE TABLE IF NOT EXISTS cpmp_budget_tier_history (
    id TEXT PRIMARY KEY,
    allocation_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('allocation_created','tier_transition','status_change','obligation_recorded')),
    from_tier TEXT,
    to_tier TEXT,
    from_status TEXT,
    to_status TEXT,
    amount_usd REAL,
    reason TEXT NOT NULL DEFAULT '',
    actor TEXT,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);
CREATE INDEX IF NOT EXISTS idx_cpmp_budget_allocations_tier ON cpmp_budget_allocations(tier);
CREATE INDEX IF NOT EXISTS idx_cpmp_budget_allocations_fy ON cpmp_budget_allocations(fiscal_year);
CREATE INDEX IF NOT EXISTS idx_cpmp_budget_allocations_status ON cpmp_budget_allocations(status);
CREATE INDEX IF NOT EXISTS idx_cpmp_budget_obligations_alloc ON cpmp_budget_obligations(allocation_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_budget_tier_history_alloc ON cpmp_budget_tier_history(allocation_id);
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
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
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
CREATE TABLE IF NOT EXISTS dic_collections (
    collection_id   TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL DEFAULT '',
    description     TEXT    DEFAULT '',
    owner_id        TEXT    DEFAULT '',
    retention_days  INTEGER DEFAULT 90,
    classification  TEXT    DEFAULT 'CUI',
    tenant_id       TEXT    DEFAULT 'default',
    created_at      TEXT    DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dic_doc_freshness (
    doc_id          TEXT    PRIMARY KEY,
    collection_id   TEXT    NOT NULL DEFAULT 'default',
    state           TEXT    DEFAULT 'unknown',
    reason          TEXT    DEFAULT '',
    source_event    TEXT    DEFAULT '',
    score           REAL    DEFAULT 0.0,
    updated_at      TEXT    DEFAULT (datetime('now')),
    last_notified_at TEXT,
    tenant_id       TEXT    DEFAULT 'default',
    classification  TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_doc_freshness_tenant ON dic_doc_freshness(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dic_doc_freshness_collection ON dic_doc_freshness(collection_id);
CREATE TABLE IF NOT EXISTS dic_cross_references (
    id              TEXT    PRIMARY KEY,
    source_doc_id   TEXT    NOT NULL,
    source_section  TEXT    DEFAULT '',
    target_doc_ref  TEXT    NOT NULL,
    target_doc_id   TEXT,
    target_section  TEXT    DEFAULT '',
    ref_text        TEXT    DEFAULT '',
    tenant_id       TEXT    DEFAULT 'default',
    classification  TEXT    DEFAULT 'CUI',
    extracted_at    TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dic_cross_refs_source ON dic_cross_references(source_doc_id);
CREATE INDEX IF NOT EXISTS idx_dic_cross_refs_target ON dic_cross_references(target_doc_id);
CREATE INDEX IF NOT EXISTS idx_dic_cross_refs_tenant ON dic_cross_references(tenant_id);
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

-- ── Data Canvas: core designs, nodes, edges, snapshots ────────────────────────
CREATE TABLE IF NOT EXISTS data_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    template_id     TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dd_templates (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT,
    description   TEXT,
    graph_json    TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    tags          TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS dd_snippets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT,
    description TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    tags        TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS dd_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    assessment_type TEXT NOT NULL,
    findings_json   TEXT DEFAULT '[]',
    score           REAL DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dd_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id       TEXT,
    user            TEXT,
    action          TEXT NOT NULL,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dd_versions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    version_number  INTEGER NOT NULL,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    change_summary  TEXT DEFAULT '',
    user_id         TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dd_collab_sessions (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    user_name   TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#3498db',
    joined_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active   INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS dd_lineage (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    source_node_id  TEXT NOT NULL,
    target_node_id  TEXT NOT NULL,
    lineage_type    TEXT DEFAULT 'flow',
    column_name     TEXT DEFAULT '',
    transform_desc  TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS data_nodes (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    node_type       TEXT NOT NULL DEFAULT 'table',
    label           TEXT DEFAULT '',
    x               REAL DEFAULT 0,
    y               REAL DEFAULT 0,
    classification  TEXT DEFAULT 'CUI',
    properties_json TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS data_edges (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    source_node_id  TEXT NOT NULL,
    target_node_id  TEXT NOT NULL,
    edge_type       TEXT DEFAULT '',
    label           TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS data_twin_snapshots (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    label           TEXT DEFAULT '',
    table_count     INTEGER DEFAULT 0,
    edge_count      INTEGER DEFAULT 0,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Data Canvas: runbooks & SOPs ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ddc_runbooks (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    category            TEXT DEFAULT 'general',
    severity            TEXT DEFAULT 'medium',
    description         TEXT DEFAULT '',
    trigger_condition   TEXT DEFAULT '',
    steps_json          TEXT DEFAULT '[]',
    classification      TEXT DEFAULT 'CUI // SP-CTI',
    status              TEXT DEFAULT 'active',
    linked_design_id    TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ddc_runbook_executions (
    id              TEXT PRIMARY KEY,
    runbook_id      TEXT,
    triggered_by    TEXT DEFAULT '',
    status          TEXT DEFAULT 'in_progress',
    notes           TEXT DEFAULT '',
    started_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at    TEXT DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS ddc_sops (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    category            TEXT DEFAULT 'general',
    description         TEXT DEFAULT '',
    purpose             TEXT DEFAULT '',
    scope               TEXT DEFAULT '',
    steps_json          TEXT DEFAULT '[]',
    references_json     TEXT DEFAULT '[]',
    version             TEXT DEFAULT '1.0',
    status              TEXT DEFAULT 'draft',
    classification      TEXT DEFAULT 'CUI // SP-CTI',
    linked_design_id    TEXT,
    owner               TEXT DEFAULT '',
    reviewer            TEXT DEFAULT '',
    approver            TEXT DEFAULT '',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ddc_sop_approvals (
    id          TEXT PRIMARY KEY,
    sop_id      TEXT,
    reviewer    TEXT NOT NULL,
    action      TEXT NOT NULL,
    comment     TEXT DEFAULT '',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Data Canvas: explore / query / quality ────────────────────────────────────
CREATE TABLE IF NOT EXISTS dd_explore_sessions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    user            TEXT DEFAULT '',
    db_conn_json    TEXT DEFAULT '{}',
    status          TEXT DEFAULT 'completed',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dd_explore_profiles (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    session_id      TEXT,
    db_conn_json    TEXT DEFAULT '{}',
    profile_json    TEXT DEFAULT '{}',
    table_count     INTEGER DEFAULT 0,
    anomaly_json    TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dd_anomaly_runs (
    id              TEXT PRIMARY KEY,
    profile_id      TEXT,
    findings_json   TEXT,
    overall_risk    TEXT,
    classification  TEXT,
    created_at      TEXT
);
CREATE TABLE IF NOT EXISTS dd_query_history (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    user            TEXT DEFAULT '',
    sql_text        TEXT NOT NULL,
    db_conn_json    TEXT DEFAULT '{}',
    row_count       INTEGER DEFAULT 0,
    exec_ms         INTEGER DEFAULT 0,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dd_quality_rules (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    name            TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    column_name     TEXT DEFAULT '',
    check_type      TEXT NOT NULL,
    threshold       REAL DEFAULT 90.0,
    params_json     TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    enabled         INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dd_quality_runs (
    id              TEXT PRIMARY KEY,
    rule_id         TEXT,
    db_conn_json    TEXT DEFAULT '{}',
    passed          INTEGER DEFAULT 0,
    actual_value    REAL DEFAULT 0.0,
    threshold       REAL DEFAULT 0.0,
    detail          TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dd_freshness_alerts (
    id              TEXT PRIMARY KEY,
    rule_id         TEXT NOT NULL,
    design_id       TEXT,
    db_conn_json    TEXT,
    last_checked    TEXT,
    passed          INTEGER,
    actual_max_value TEXT,
    cutoff_value    TEXT,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT
);

-- ── Data Mesh foundation ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dm_domains (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    owner           TEXT DEFAULT '',
    steward         TEXT DEFAULT '',
    bounded_context TEXT DEFAULT '',
    maturity_level  INTEGER DEFAULT 0,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    status          TEXT DEFAULT 'active',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_data_products (
    id              TEXT PRIMARY KEY,
    domain_id       TEXT,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    owner           TEXT DEFAULT '',
    version         TEXT DEFAULT '1.0.0',
    availability_sla REAL DEFAULT 99.9,
    latency_sla_ms  INTEGER DEFAULT 500,
    status          TEXT DEFAULT 'active',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_contracts (
    id              TEXT PRIMARY KEY,
    product_id      TEXT,
    title           TEXT NOT NULL,
    version         TEXT DEFAULT '1.0.0',
    schema_json     TEXT DEFAULT '{}',
    sla_json        TEXT DEFAULT '{}',
    quality_rules_json TEXT DEFAULT '[]',
    status          TEXT DEFAULT 'draft',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_input_ports (
    id              TEXT PRIMARY KEY,
    product_id      TEXT,
    name            TEXT NOT NULL,
    port_type       TEXT DEFAULT 'cdc',
    schema_json     TEXT DEFAULT '{}',
    source_system   TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_output_ports (
    id              TEXT PRIMARY KEY,
    product_id      TEXT,
    name            TEXT NOT NULL,
    port_type       TEXT DEFAULT 'api',
    schema_json     TEXT DEFAULT '{}',
    endpoint        TEXT DEFAULT '',
    sla_json        TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_ports (
    id              TEXT PRIMARY KEY,
    product_id      TEXT,
    name            TEXT NOT NULL,
    port_type       TEXT NOT NULL DEFAULT 'input',
    transport_type  TEXT DEFAULT 'api',
    schema_json     TEXT DEFAULT '{}',
    endpoint        TEXT DEFAULT '',
    source_system   TEXT DEFAULT '',
    sla_json        TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_domain_maturity (
    id              TEXT PRIMARY KEY,
    domain_id       TEXT,
    maturity_level  INTEGER NOT NULL DEFAULT 0,
    scores_json     TEXT DEFAULT '{}',
    assessed_by     TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_governance_policies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    policy_type     TEXT DEFAULT 'opa',
    rules_json      TEXT DEFAULT '[]',
    applies_to      TEXT DEFAULT 'all',
    status          TEXT DEFAULT 'active',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_catalog_entries (
    id              TEXT PRIMARY KEY,
    product_id      TEXT,
    catalog_name    TEXT NOT NULL,
    tags_json       TEXT DEFAULT '[]',
    metadata_json   TEXT DEFAULT '{}',
    lineage_json    TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id       TEXT,
    product_id      TEXT,
    user            TEXT DEFAULT '',
    action          TEXT NOT NULL,
    detail          TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_opa_policies (
    id              TEXT PRIMARY KEY,
    domain_id       TEXT,
    name            TEXT NOT NULL,
    rego_text       TEXT DEFAULT '',
    policy_path     TEXT DEFAULT 'datamesh/allow',
    enabled         INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_policy_audit_log (
    id              TEXT PRIMARY KEY,
    policy_id       TEXT,
    user            TEXT DEFAULT 'system',
    resource        TEXT DEFAULT '{}',
    decision        INTEGER DEFAULT 0,
    reason          TEXT DEFAULT '',
    method          TEXT DEFAULT 'local',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_csp_sync_log (
    id              TEXT PRIMARY KEY,
    provider        TEXT NOT NULL,
    domain_id       TEXT DEFAULT '',
    product_id      TEXT DEFAULT '',
    operation       TEXT NOT NULL,
    status          TEXT NOT NULL,
    synced_count    INTEGER DEFAULT 0,
    error_detail    TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dm_product_slas (
    id              TEXT PRIMARY KEY,
    product_id      TEXT,
    sla_type        TEXT NOT NULL,
    target_value    REAL NOT NULL,
    unit            TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_product_subscriptions (
    id              TEXT PRIMARY KEY,
    product_id      TEXT,
    subscriber_team TEXT NOT NULL,
    purpose         TEXT DEFAULT '',
    approved        INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_data_contracts (
    id              TEXT PRIMARY KEY,
    domain_id       TEXT DEFAULT '',
    product_id      TEXT DEFAULT '',
    name            TEXT NOT NULL,
    contract_yaml   TEXT DEFAULT '',
    version         TEXT DEFAULT '1.0.0',
    status          TEXT DEFAULT 'draft',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dm_contract_test_runs (
    id              TEXT PRIMARY KEY,
    contract_id     TEXT,
    passed          INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    warnings        INTEGER DEFAULT 0,
    result_json     TEXT DEFAULT '{}',
    method          TEXT DEFAULT 'internal',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Data Canvas: PII scanner ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dd_pii_scans (
    scan_id         TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL DEFAULT '',
    overall_risk    TEXT NOT NULL DEFAULT 'none',
    findings_json   TEXT NOT NULL DEFAULT '[]',
    scanned_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
CREATE TABLE IF NOT EXISTS zig_targets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    system_type     TEXT DEFAULT 'general',
    classification  TEXT DEFAULT 'CUI',
    status          TEXT DEFAULT 'active',
    pillar_focus    TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS slides_decks (
    deck_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title                TEXT NOT NULL,
    deck_type            TEXT NOT NULL DEFAULT 'executive_overview',
    theme                TEXT NOT NULL DEFAULT 'midnight_executive',
    tone                 TEXT DEFAULT 'professional',
    occasion             TEXT,
    target_audience      TEXT,
    citation_style       TEXT DEFAULT 'inline_links',
    output_formats       TEXT DEFAULT '["pptx"]',
    status               TEXT NOT NULL DEFAULT 'pending',
    source_types         TEXT DEFAULT '[]',
    pptx_path            TEXT,
    pdf_path             TEXT,
    html_path            TEXT,
    slide_count          INTEGER DEFAULT 0,
    error_message        TEXT,
    enable_rich_diagrams INTEGER DEFAULT 0,
    audience_mode        TEXT,
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at         DATETIME
);
CREATE TABLE IF NOT EXISTS slides_slides (
    slide_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id              INTEGER NOT NULL REFERENCES slides_decks(deck_id) ON DELETE CASCADE,
    position             INTEGER NOT NULL,
    slide_type           TEXT NOT NULL DEFAULT 'content',
    title                TEXT NOT NULL,
    bullets              TEXT DEFAULT '[]',
    speaker_notes        TEXT,
    citations            TEXT DEFAULT '[]',
    image_path           TEXT,
    image_prompt         TEXT,
    mermaid_code         TEXT,
    three_scene_config   TEXT,
    excalidraw_elements  TEXT,
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS slides_audit (
    audit_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id   INTEGER REFERENCES slides_decks(deck_id),
    action    TEXT NOT NULL,
    actor     TEXT DEFAULT 'system',
    details   TEXT,
    ts        DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ── Procurement Quote vs IGCE Comparison (task-plan-7fe75cb8f440) ──
CREATE TABLE IF NOT EXISTS proc_procurements (
    id              TEXT PRIMARY KEY,
    solicitation    TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    agency          TEXT NOT NULL DEFAULT '',
    contract_type   TEXT NOT NULL DEFAULT 'ffp',
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'open',
    metadata        TEXT DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    classification  TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS proc_igce_line_items (
    id              TEXT PRIMARY KEY,
    procurement_id  TEXT NOT NULL,
    clin            TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    unit            TEXT NOT NULL DEFAULT 'each',
    quantity        REAL NOT NULL DEFAULT 1.0,
    unit_cost       REAL NOT NULL DEFAULT 0.0,
    extended_cost   REAL NOT NULL DEFAULT 0.0,
    basis           TEXT NOT NULL DEFAULT '',
    notes           TEXT,
    metadata        TEXT DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    classification  TEXT DEFAULT 'CUI',
    UNIQUE (procurement_id, clin)
);
CREATE TABLE IF NOT EXISTS proc_vendor_quotes (
    id              TEXT PRIMARY KEY,
    procurement_id  TEXT NOT NULL,
    vendor_name     TEXT NOT NULL,
    quote_ref       TEXT NOT NULL DEFAULT '',
    clin            TEXT NOT NULL DEFAULT '',
    unit_price      REAL NOT NULL DEFAULT 0.0,
    quantity        REAL,
    total_price     REAL NOT NULL DEFAULT 0.0,
    quote_date      TEXT,
    valid_until     TEXT,
    status          TEXT NOT NULL DEFAULT 'submitted',
    notes           TEXT,
    metadata        TEXT DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    classification  TEXT DEFAULT 'CUI',
    UNIQUE (procurement_id, vendor_name, quote_ref, clin)
);
CREATE INDEX IF NOT EXISTS idx_igce_proc ON proc_igce_line_items(procurement_id);
CREATE INDEX IF NOT EXISTS idx_quote_proc ON proc_vendor_quotes(procurement_id);
CREATE INDEX IF NOT EXISTS idx_quote_vendor ON proc_vendor_quotes(vendor_name);
CREATE INDEX IF NOT EXISTS idx_quote_clin ON proc_vendor_quotes(clin);

-- ── IGCE Estimator (task-plan-ddef9424ab46) ───────────────────────────
-- Pre-bid Independent Government Cost Estimate generator that produces
-- estimates within 10% of vendor actuals, validated against GSA
-- Schedule pricing or market data.
CREATE TABLE IF NOT EXISTS gsa_schedule_rates (
    id                  TEXT PRIMARY KEY,
    labor_category      TEXT NOT NULL,
    bls_soc_code        TEXT,
    sin                 TEXT NOT NULL DEFAULT '',
    schedule_contractor TEXT NOT NULL DEFAULT '',
    hourly_rate         REAL NOT NULL,
    year                INTEGER NOT NULL,
    region              TEXT,
    education_level     TEXT,
    min_years_experience INTEGER,
    source              TEXT NOT NULL DEFAULT 'gsa_schedule',
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gsa_rate_lc ON gsa_schedule_rates(labor_category);
CREATE INDEX IF NOT EXISTS idx_gsa_rate_soc ON gsa_schedule_rates(bls_soc_code);
CREATE INDEX IF NOT EXISTS idx_gsa_rate_year ON gsa_schedule_rates(year);

CREATE TABLE IF NOT EXISTS gsa_market_rates (
    id              TEXT PRIMARY KEY,
    labor_category  TEXT NOT NULL,
    bls_soc_code    TEXT,
    source          TEXT NOT NULL,
    p25_hourly      REAL,
    median_hourly   REAL NOT NULL,
    p75_hourly      REAL,
    sample_size     INTEGER DEFAULT 0,
    year            INTEGER NOT NULL,
    region          TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_rate_lc ON gsa_market_rates(labor_category);
CREATE INDEX IF NOT EXISTS idx_market_rate_source ON gsa_market_rates(source);

CREATE TABLE IF NOT EXISTS igce_estimates (
    id                      TEXT PRIMARY KEY,
    procurement_id          TEXT,
    opportunity_id          TEXT,
    solicitation            TEXT NOT NULL DEFAULT '',
    agency                  TEXT NOT NULL DEFAULT '',
    title                   TEXT NOT NULL DEFAULT '',
    period_of_performance   TEXT,
    estimation_method       TEXT NOT NULL DEFAULT 'deterministic',
    status                  TEXT NOT NULL DEFAULT 'draft',
    total_estimated_cost    REAL NOT NULL DEFAULT 0.0,
    total_low_estimate      REAL NOT NULL DEFAULT 0.0,
    total_high_estimate     REAL NOT NULL DEFAULT 0.0,
    within_10pct_confidence REAL,
    benchmark_source        TEXT,
    benchmark_sample_size   INTEGER DEFAULT 0,
    notes                   TEXT,
    created_by              TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_igce_est_proc ON igce_estimates(procurement_id);
CREATE INDEX IF NOT EXISTS idx_igce_est_opp ON igce_estimates(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_igce_est_status ON igce_estimates(status);

CREATE TABLE IF NOT EXISTS igce_estimate_line_items (
    id                      TEXT PRIMARY KEY,
    igce_estimate_id        TEXT NOT NULL,
    clin                    TEXT NOT NULL DEFAULT '',
    description             TEXT NOT NULL,
    unit                    TEXT NOT NULL DEFAULT 'each',
    quantity                REAL NOT NULL DEFAULT 1.0,
    unit_cost_estimate      REAL NOT NULL DEFAULT 0.0,
    unit_cost_low           REAL,
    unit_cost_high          REAL,
    extended_cost           REAL NOT NULL DEFAULT 0.0,
    bls_soc_code            TEXT,
    labor_category          TEXT,
    benchmark_source        TEXT,
    benchmark_rate          REAL,
    benchmark_year          INTEGER,
    benchmark_n             INTEGER DEFAULT 0,
    confidence              REAL,
    rationale               TEXT,
    created_at              TEXT NOT NULL,
    FOREIGN KEY (igce_estimate_id) REFERENCES igce_estimates(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_igce_line_est ON igce_estimate_line_items(igce_estimate_id);

CREATE TABLE IF NOT EXISTS igce_calibration_log (
    id                  TEXT PRIMARY KEY,
    igce_estimate_id    TEXT NOT NULL,
    procurement_id      TEXT,
    clin                TEXT NOT NULL DEFAULT '',
    estimated_unit_cost REAL NOT NULL,
    actual_unit_cost    REAL NOT NULL,
    actual_vendor       TEXT,
    variance_pct        REAL NOT NULL,
    within_10pct        INTEGER NOT NULL,
    benchmark_source    TEXT,
    confidence_predicted REAL,
    captured_at         TEXT NOT NULL,
    FOREIGN KEY (igce_estimate_id) REFERENCES igce_estimates(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_igce_cal_est ON igce_calibration_log(igce_estimate_id);
CREATE INDEX IF NOT EXISTS idx_igce_cal_proc ON igce_calibration_log(procurement_id);

-- SIPA — Software Integrity & Provenance Assessor (sipa-db-03)
-- Mirrors tools/db/init_icdev_db.py; CHECK values from tools/integrity/constants.py.
CREATE TABLE IF NOT EXISTS integrity_assessments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT NOT NULL CHECK(source_type IN ('local', 'git', 'unc', 'uri')),
    source_ref      TEXT NOT NULL,
    mode            TEXT NOT NULL CHECK(mode IN ('provenance_aware', 'provenance_blind', 'auto')),
    project_id      TEXT,
    session_id      TEXT,
    dir_digest      TEXT,
    status          TEXT NOT NULL DEFAULT 'quarantine'
                        CHECK(status IN ('quarantine', 'assessed', 'approved', 'rejected')),
    verdict         TEXT CHECK(verdict IN ('allow', 'review', 'quarantine') OR verdict IS NULL),
    risk_score      REAL DEFAULT 0,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_by      TEXT NOT NULL DEFAULT 'system',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS integrity_capabilities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id   INTEGER NOT NULL REFERENCES integrity_assessments(id) ON DELETE CASCADE,
    file_path       TEXT NOT NULL,
    function_name   TEXT,
    capability_type TEXT NOT NULL CHECK(capability_type IN (
                        'network_egress', 'filesystem', 'process_exec', 'dynamic_code',
                        'crypto', 'env_secret', 'serialization', 'obfuscation')),
    evidence        TEXT,
    line_start      INTEGER,
    line_end        INTEGER,
    risk_weight     REAL DEFAULT 0,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS integrity_findings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id   INTEGER NOT NULL REFERENCES integrity_assessments(id) ON DELETE CASCADE,
    source_scanner  TEXT NOT NULL CHECK(source_scanner IN (
                        'sast', 'secrets', 'deps', 'formal', 'container', 'semgrep',
                        'capability', 'reconciliation', 'tamper')),
    finding_type    TEXT NOT NULL CHECK(finding_type IN (
                        'dangerous_api', 'secret', 'vuln_dependency', 'unauthorized_capability',
                        'undisclosed_capability', 'tamper_mismatch', 'known_bad_signature')),
    severity        TEXT NOT NULL CHECK(severity IN ('critical', 'high', 'medium', 'low', 'info')),
    file_path       TEXT,
    line            INTEGER,
    detail          TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS integrity_verdicts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id   INTEGER NOT NULL REFERENCES integrity_assessments(id) ON DELETE CASCADE,
    verdict         TEXT NOT NULL CHECK(verdict IN ('allow', 'review', 'quarantine')),
    risk_score      REAL DEFAULT 0,
    rationale       TEXT,
    decided_by      TEXT NOT NULL DEFAULT 'system',
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS integrity_authorizations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id   INTEGER NOT NULL REFERENCES integrity_assessments(id) ON DELETE CASCADE,
    capability_id   INTEGER REFERENCES integrity_capabilities(id) ON DELETE CASCADE,
    requirement_id  TEXT,
    claim_ref       TEXT,
    authorized      INTEGER NOT NULL DEFAULT 0,
    reason          TEXT,
    reviewed_by     TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS centralized_logs (
    id              TEXT PRIMARY KEY,
    ts              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    component       TEXT NOT NULL,
    level           TEXT NOT NULL DEFAULT 'INFO',
    message         TEXT NOT NULL DEFAULT '',
    trace_id        TEXT,
    session_id      TEXT,
    classification  TEXT NOT NULL DEFAULT 'CUI',
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    extra_json      TEXT
);
CREATE INDEX IF NOT EXISTS idx_centralized_logs_component ON centralized_logs(component);
CREATE INDEX IF NOT EXISTS idx_centralized_logs_ts        ON centralized_logs(ts);
CREATE INDEX IF NOT EXISTS idx_centralized_logs_level     ON centralized_logs(level);
CREATE TABLE IF NOT EXISTS cli_llm_jobs (
    id             TEXT PRIMARY KEY,
    function       TEXT NOT NULL DEFAULT '',
    prompt         TEXT NOT NULL DEFAULT '',
    system_prompt  TEXT DEFAULT '',
    model_id       TEXT,
    backend        TEXT DEFAULT 'auto',
    status         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'running', 'done', 'error')),
    result         TEXT,
    error          TEXT,
    context_id     TEXT,
    input_tokens   INTEGER DEFAULT 0,
    output_tokens  INTEGER DEFAULT 0,
    tenant_id      TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at     TEXT,
    updated_at     TEXT,
    claimed_at     TEXT,
    completed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_cli_llm_jobs_claim   ON cli_llm_jobs (status, backend, created_at);
CREATE INDEX IF NOT EXISTS idx_cli_llm_jobs_context ON cli_llm_jobs (context_id);

-- ACF — Autonomous Capability Foundry (acf-db) — 6 platform findings tables.
CREATE TABLE IF NOT EXISTS foundry_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    harvested         INTEGER NOT NULL DEFAULT 0,
    concepts_proposed INTEGER NOT NULL DEFAULT 0,
    concepts_approved INTEGER NOT NULL DEFAULT 0,
    tasks_emitted     INTEGER NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL DEFAULT 'running',
    detail            TEXT    DEFAULT '{}',
    tenant_id         TEXT    NOT NULL DEFAULT 'default',
    classification    TEXT    NOT NULL DEFAULT 'CUI',
    created_at        TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS foundry_signals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT    NOT NULL,
    source_engine     TEXT    NOT NULL,
    source_ref        TEXT    NOT NULL,
    theme             TEXT,
    raw_score         REAL    DEFAULT 0.0,
    keywords          TEXT    DEFAULT '[]',
    content_hash      TEXT,
    tenant_id         TEXT    NOT NULL DEFAULT 'default',
    classification    TEXT    NOT NULL DEFAULT 'CUI',
    created_at        TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_foundry_signals_hash ON foundry_signals(content_hash);
CREATE TABLE IF NOT EXISTS foundry_concepts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT    NOT NULL,
    name                TEXT    NOT NULL,
    slug                TEXT    NOT NULL UNIQUE,
    problem_statement   TEXT,
    proposed_capability TEXT,
    target_users        TEXT,
    cluster_signal_ids  TEXT    DEFAULT '[]',
    novelty_score       REAL    DEFAULT 0.0,
    market_score        REAL    DEFAULT 0.0,
    fit_score           REAL    DEFAULT 0.0,
    effort_estimate     REAL    DEFAULT 0.0,
    compliance_risk     REAL    DEFAULT 0.0,
    composite_score     REAL    DEFAULT 0.0,
    status              TEXT    NOT NULL DEFAULT 'proposed',
    reject_reason       TEXT,
    tenant_id           TEXT    NOT NULL DEFAULT 'default',
    classification      TEXT    NOT NULL DEFAULT 'CUI',
    created_by          TEXT    NOT NULL DEFAULT 'system',
    created_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS foundry_specs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id      INTEGER NOT NULL,
    spec_md         TEXT    NOT NULL,
    canvas_contract TEXT    DEFAULT '{}',
    task_count      INTEGER NOT NULL DEFAULT 0,
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS foundry_tasks_emitted (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id     INTEGER NOT NULL,
    kanban_task_id TEXT    NOT NULL,
    epic           TEXT,
    seq            INTEGER NOT NULL DEFAULT 0,
    tenant_id      TEXT    NOT NULL DEFAULT 'default',
    classification TEXT    NOT NULL DEFAULT 'CUI',
    created_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS foundry_outcomes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id     INTEGER NOT NULL,
    outcome        TEXT    NOT NULL,
    metric         REAL,
    detail         TEXT    DEFAULT '{}',
    tenant_id      TEXT    NOT NULL DEFAULT 'default',
    classification TEXT    NOT NULL DEFAULT 'CUI',
    created_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- MCIP DAT — Diplomatic Activity Tracker (issue-18)
CREATE TABLE IF NOT EXISTS mcip_dat_events (
    id              TEXT PRIMARY KEY,
    source_type     TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    sender          TEXT NOT NULL DEFAULT 'unknown',
    recipient       TEXT NOT NULL DEFAULT 'unknown',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    tension_signal  REAL NOT NULL DEFAULT 0.0,
    ingested_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcip_dat_events_source ON mcip_dat_events(source_type);
CREATE INDEX IF NOT EXISTS idx_mcip_dat_events_at     ON mcip_dat_events(ingested_at);

CREATE TABLE IF NOT EXISTS mcip_dti_scores (
    id              TEXT PRIMARY KEY,
    score           REAL NOT NULL,
    cable_sub       REAL NOT NULL DEFAULT 0.0,
    unsc_sub        REAL NOT NULL DEFAULT 0.0,
    backchannel_sub REAL NOT NULL DEFAULT 0.0,
    event_count     INTEGER NOT NULL DEFAULT 0,
    computed_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcip_dti_scores_at ON mcip_dti_scores(computed_at);

-- RAG provenance ledger — append-only AIA chain-of-custody (D-AIDP, NIST AU-3)
CREATE TABLE IF NOT EXISTS rag_provenance_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_uuid TEXT NOT NULL,
    parent_doc_uuid TEXT,
    sha256_hash TEXT,
    token_count INTEGER DEFAULT 0,
    classification_label TEXT,
    version_tree_ref TEXT,
    model_id TEXT,
    hyperparams_json TEXT DEFAULT '{}',
    prompt_sha256 TEXT,
    signature TEXT,
    event_type TEXT NOT NULL DEFAULT 'ingest'
        CHECK(event_type IN ('ingest', 'chain_of_custody')),
    ingest_timestamp TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rag_prov_chunk ON rag_provenance_ledger(chunk_uuid);
CREATE INDEX IF NOT EXISTS idx_rag_prov_event_type ON rag_provenance_ledger(event_type);

-- SBOM component registry and supply chain risk tables (migration 209)
CREATE TABLE IF NOT EXISTS sbom_components (
    id              TEXT    PRIMARY KEY,
    component_name  TEXT    NOT NULL,
    version         TEXT,
    vendor          TEXT,
    component_type  TEXT,
    purl            TEXT,
    license         TEXT,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT    DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS supply_chain_vulnerabilities (
    id                TEXT    PRIMARY KEY,
    sbom_id           TEXT    NOT NULL REFERENCES sbom_components(id),
    cve_id            TEXT    NOT NULL,
    cvss_score        REAL    NOT NULL DEFAULT 0.0,
    severity          TEXT,
    affected_versions TEXT,
    fixed_version     TEXT,
    classification    TEXT    NOT NULL DEFAULT 'CUI',
    created_at        TEXT    DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS supply_chain_risk_scores (
    id              TEXT    PRIMARY KEY,
    sbom_id         TEXT    NOT NULL REFERENCES sbom_components(id),
    risk_level      TEXT,
    exploitability  TEXT,
    patch_available INTEGER NOT NULL DEFAULT 0,
    last_assessed   TEXT    NOT NULL,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS rag_queries (
    id              TEXT    PRIMARY KEY,
    query_text      TEXT    NOT NULL,
    lens            TEXT    DEFAULT 'default',
    status          TEXT    DEFAULT 'pending'
        CHECK(status IN ('pending', 'running', 'done', 'failed')),
    agent_id        TEXT,
    tenant_id       TEXT    DEFAULT '',
    classification  TEXT    DEFAULT 'CUI',
    created_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
    completed_at    TEXT
);
CREATE TABLE IF NOT EXISTS rag_citations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id        TEXT    NOT NULL REFERENCES rag_queries(id),
    source_doc      TEXT    NOT NULL,
    citation_text   TEXT,
    confidence      REAL    DEFAULT 0.0,
    tenant_id       TEXT    DEFAULT '',
    classification  TEXT    DEFAULT 'CUI',
    created_at      TEXT    DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS showcase_apps (
    id          TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT '',
    description TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'draft',
    slug        TEXT,
    metadata    TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT    DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sso_providers (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    protocol TEXT NOT NULL CHECK(protocol IN ('saml','oidc')),
    entity_id TEXT,
    metadata_url TEXT,
    client_id TEXT,
    client_secret_enc TEXT,
    attr_mapping TEXT,
    claims_mapping TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sso_providers_tenant ON sso_providers(tenant_id);
CREATE TABLE IF NOT EXISTS sso_sessions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    user_id TEXT,
    name_id TEXT,
    session_index TEXT,
    id_token TEXT,
    access_token_enc TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sso_sessions_tenant ON sso_sessions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_sso_sessions_provider ON sso_sessions(provider_id);
CREATE TABLE IF NOT EXISTS evidence_items (
    id              TEXT PRIMARY KEY,
    control_id      TEXT NOT NULL,
    framework       TEXT NOT NULL DEFAULT 'soc2',
    tenant_id       TEXT NOT NULL,
    evidence_type   TEXT CHECK(evidence_type IN ('log','config','test_result','screenshot','policy')),
    source_table    TEXT,
    source_row_id   TEXT,
    summary         TEXT,
    collected_at    TEXT NOT NULL DEFAULT (datetime('now')),
    collector       TEXT NOT NULL DEFAULT 'auto',
    classification  TEXT NOT NULL DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_evidence_items_control ON evidence_items(tenant_id, control_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_items_source
    ON evidence_items(source_table, source_row_id, control_id)
    WHERE source_table IS NOT NULL AND source_row_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS data_residency_zones (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    region      TEXT NOT NULL,
    pg_dsn_env  TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tenant_zone_assignments (
    tenant_id   TEXT PRIMARY KEY,
    zone_id     TEXT NOT NULL REFERENCES data_residency_zones(id),
    assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
    assigned_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_tenant_zone_assignments_zone
    ON tenant_zone_assignments(zone_id);
CREATE TABLE IF NOT EXISTS erasure_audit (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    requested_by    TEXT NOT NULL,
    scope           TEXT NOT NULL DEFAULT 'pii',
    tables_affected TEXT,
    completed_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    name         TEXT NOT NULL,
    key_prefix   TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    scopes       TEXT NOT NULL DEFAULT 'read',
    last_used_at TEXT,
    expires_at   TEXT,
    revoked_at   TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash   ON api_keys(key_hash);

CREATE TABLE IF NOT EXISTS idr_sessions (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    domain          TEXT NOT NULL DEFAULT 'network',
    doc_type        TEXT NOT NULL DEFAULT 'runbook',
    template_id     TEXT,
    stage           INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'setup',
    dic_collection_id TEXT,
    ace_instance_id TEXT,
    topology_id     TEXT,
    wg_result_id    TEXT,
    conflicts_resolved INTEGER DEFAULT 0,
    created_by      TEXT,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    suggested_classification TEXT,
    suggested_classification_confidence REAL,
    prior_docs_context TEXT,
    last_source_hash TEXT,
    source_hash_checked_at TEXT,
    final_doc_text  TEXT,
    dic_doc_id      TEXT,
    source_dic_doc_id TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS idr_uploads (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    filename        TEXT NOT NULL,
    upload_type     TEXT NOT NULL DEFAULT 'doc',
    file_path       TEXT,
    file_hash       TEXT,
    dic_doc_id      TEXT,
    extracted_from_doc_id TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    error_msg       TEXT,
    tenant_id       TEXT,
    uploaded_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS idr_analyses (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    upload_id       TEXT NOT NULL,
    analysis_type   TEXT NOT NULL,
    result_ref_id   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'done',
    error_msg       TEXT,
    tenant_id       TEXT,
    result_json     TEXT,
    confidence_score REAL,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS idr_conflicts (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    node_label      TEXT NOT NULL,
    conflict_type   TEXT NOT NULL,
    source_a        TEXT NOT NULL,
    source_a_value  TEXT,
    source_b        TEXT NOT NULL,
    source_b_value  TEXT,
    resolved_by     TEXT,
    resolution      TEXT,
    resolution_notes TEXT,
    resolved_at     TEXT,
    tenant_id       TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS idr_artifacts (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    dic_doc_id      TEXT,
    dic_version_id  TEXT,
    format          TEXT NOT NULL,
    file_path       TEXT,
    wg_result_id    TEXT,
    published_at    TEXT,
    tenant_id       TEXT,
    flagged_sections TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS idr_publish_audit (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    gate        TEXT NOT NULL,
    reviewer    TEXT,
    findings    TEXT,
    tenant_id   TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- PNA: Predictive Network Analytics (migration 222)
CREATE TABLE IF NOT EXISTS nc_eol_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name TEXT NOT NULL,
    vendor TEXT, model TEXT, os_version TEXT,
    eos_date TEXT, eol_date TEXT, days_remaining INTEGER,
    has_active_cves INTEGER NOT NULL DEFAULT 0,
    active_cve_count INTEGER NOT NULL DEFAULT 0,
    risk_score REAL NOT NULL DEFAULT 0.0,
    risk_tier TEXT NOT NULL DEFAULT 'medium',
    nqe_source TEXT NOT NULL DEFAULT 'local_mapping',
    model_version TEXT NOT NULL DEFAULT '1.0',
    predicted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS nc_bgp_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    device_name TEXT NOT NULL,
    peer_ip TEXT NOT NULL,
    peer_asn INTEGER,
    event_type TEXT NOT NULL DEFAULT 'flap',
    event_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS nc_bgp_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    device_name TEXT NOT NULL,
    peer_ip TEXT NOT NULL,
    peer_asn INTEGER,
    stability_score REAL NOT NULL DEFAULT 1.0,
    flap_count_24h INTEGER NOT NULL DEFAULT 0,
    flap_count_7d INTEGER NOT NULL DEFAULT 0,
    flap_risk TEXT NOT NULL DEFAULT 'low',
    route_count INTEGER,
    session_state TEXT,
    predicted_outage_hrs REAL,
    confidence REAL NOT NULL DEFAULT 0.4,
    model_version TEXT NOT NULL DEFAULT '1.0',
    predicted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS nc_compliance_drift (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name TEXT NOT NULL,
    framework TEXT NOT NULL DEFAULT 'DISA_STIG',
    last_compliant_score REAL,
    current_score REAL NOT NULL DEFAULT 0.0,
    drift_delta REAL NOT NULL DEFAULT 0.0,
    drift_rate_per_day REAL,
    failing_controls INTEGER NOT NULL DEFAULT 0,
    critical_controls_failing INTEGER NOT NULL DEFAULT 0,
    predicted_fail_date TEXT,
    days_to_failure INTEGER,
    risk_score REAL NOT NULL DEFAULT 0.0,
    risk_tier TEXT NOT NULL DEFAULT 'medium',
    assessed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS nc_capacity_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name TEXT NOT NULL,
    interface_name TEXT NOT NULL,
    interface_id TEXT,
    current_util_pct REAL NOT NULL DEFAULT 0.0,
    peak_util_pct REAL,
    avg_util_pct_7d REAL,
    trend_slope REAL NOT NULL DEFAULT 0.0,
    days_to_saturation INTEGER,
    saturation_date TEXT,
    confidence REAL NOT NULL DEFAULT 0.4,
    risk_score REAL NOT NULL DEFAULT 0.0,
    risk_tier TEXT NOT NULL DEFAULT 'low',
    nqe_source TEXT NOT NULL DEFAULT 'local_mapping',
    model_version TEXT NOT NULL DEFAULT '1.0',
    predicted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS nc_change_risk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_request_id TEXT NOT NULL DEFAULT 'auto',
    device_name TEXT NOT NULL,
    action_type TEXT,
    failure_probability REAL NOT NULL DEFAULT 0.0,
    blast_radius_size INTEGER NOT NULL DEFAULT 0,
    concurrent_change_count INTEGER NOT NULL DEFAULT 0,
    maintenance_window_compliant INTEGER NOT NULL DEFAULT 1,
    device_criticality INTEGER NOT NULL DEFAULT 3,
    risk_factors_json TEXT,
    risk_tier TEXT NOT NULL DEFAULT 'low',
    simulation_verdict TEXT,
    model_version TEXT NOT NULL DEFAULT '1.0',
    predicted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS nc_supply_chain_risk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor TEXT NOT NULL,
    device_count INTEGER NOT NULL DEFAULT 0,
    model_count INTEGER NOT NULL DEFAULT 0,
    cve_count INTEGER NOT NULL DEFAULT 0,
    kev_count INTEGER NOT NULL DEFAULT 0,
    critical_cve_count INTEGER NOT NULL DEFAULT 0,
    high_cve_count INTEGER NOT NULL DEFAULT 0,
    risk_score REAL NOT NULL DEFAULT 0.0,
    vendor_risk_rating TEXT NOT NULL DEFAULT 'low',
    top_cves_json TEXT,
    nqe_device_sample_json TEXT,
    model_version TEXT NOT NULL DEFAULT '1.0',
    assessed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS studio_forms (
    form_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    schema_json TEXT NOT NULL,
    created_by  TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now')),
    version     INTEGER DEFAULT 1,
    status      TEXT DEFAULT 'draft'
);
CREATE TABLE IF NOT EXISTS studio_form_submissions (
    submission_id TEXT PRIMARY KEY,
    form_id       TEXT NOT NULL REFERENCES studio_forms(form_id),
    data_json     TEXT NOT NULL,
    submitted_by  TEXT,
    submitted_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wfc_branding (
    id               TEXT PRIMARY KEY,
    entity_type      TEXT NOT NULL,
    entity_id        TEXT NOT NULL,
    org_name         TEXT,
    logo_data        TEXT,
    primary_color    TEXT DEFAULT '#1a365d',
    secondary_color  TEXT DEFAULT '#c8a951',
    header_html      TEXT,
    footer_html      TEXT,
    show_classification INTEGER DEFAULT 1,
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wfc_workflow_form_nodes (
    id                   TEXT PRIMARY KEY,
    workflow_id          TEXT NOT NULL,
    node_key             TEXT NOT NULL,
    form_id              TEXT NOT NULL,
    node_label           TEXT,
    required_before_next INTEGER DEFAULT 1,
    created_at           TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS forecast_jobs (
    id             TEXT PRIMARY KEY,
    source         TEXT NOT NULL DEFAULT 'manual',
    context        TEXT DEFAULT '',
    input_rows     INTEGER NOT NULL,
    input_summary  TEXT DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT 'pending',
    prediction     TEXT DEFAULT '{}',
    model_id       TEXT DEFAULT 'timesfm-2.5-200m',
    error_message  TEXT DEFAULT '',
    created_at     TEXT DEFAULT (datetime('now')),
    updated_at     TEXT DEFAULT (datetime('now')),
    completed_at   TEXT,
    classification TEXT DEFAULT 'CUI',
    tenant_id      TEXT
);
CREATE TABLE IF NOT EXISTS forecast_audit (
    id             TEXT PRIMARY KEY,
    job_id         TEXT NOT NULL REFERENCES forecast_jobs(id) ON DELETE CASCADE,
    event_type     TEXT NOT NULL,
    actor          TEXT DEFAULT 'system',
    details        TEXT DEFAULT '{}',
    created_at     TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS bi_dashboards (
    id              TEXT    PRIMARY KEY,
    title           TEXT    NOT NULL,
    owner_id        TEXT    DEFAULT '',
    tiles_json      TEXT    DEFAULT '[]',
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now')),
    tenant_id       TEXT    DEFAULT 'default',
    classification  TEXT    DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS bi_data_sources (
    id               TEXT    PRIMARY KEY,
    name             TEXT    NOT NULL,
    source_type      TEXT    DEFAULT 'upload',
    columns_json     TEXT    DEFAULT '[]',
    dimensions_json  TEXT    DEFAULT '[]',
    measures_json    TEXT    DEFAULT '[]',
    rows_json        TEXT    DEFAULT '[]',
    row_count        INTEGER DEFAULT 0,
    created_at       TEXT    DEFAULT (datetime('now')),
    tenant_id        TEXT    DEFAULT 'default',
    classification   TEXT    DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS bi_generation_log (
    id              TEXT    PRIMARY KEY,
    dashboard_id    TEXT    DEFAULT '',
    prompt          TEXT    NOT NULL,
    structure_json  TEXT    DEFAULT '{}',
    method          TEXT    DEFAULT 'heuristic',
    accepted        INTEGER DEFAULT 1,
    created_at      TEXT    DEFAULT (datetime('now')),
    tenant_id       TEXT    DEFAULT 'default',
    classification  TEXT    DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS aggregation_events (
    id                      TEXT PRIMARY KEY,
    occurred_at             TEXT NOT NULL DEFAULT (datetime('now')),
    user_id                 TEXT,
    tenant_id               TEXT,
    surface                 TEXT,
    rule_name               TEXT,
    derived_classification  TEXT NOT NULL,
    surface_ceiling         TEXT,
    action                  TEXT NOT NULL DEFAULT 'derive' CHECK (action IN ('derive', 'warn', 'block')),
    element_summary         TEXT,
    classification          TEXT NOT NULL DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS document_aggregation_findings (
    id                      TEXT PRIMARY KEY,
    surface                 TEXT NOT NULL,
    document_id             TEXT NOT NULL,
    rule_id                 TEXT NOT NULL,
    derived_classification  TEXT NOT NULL,
    matched_elements        TEXT,
    content_signature       TEXT NOT NULL,
    resolution              TEXT CHECK (resolution IN ('override')),
    resolved_by             TEXT,
    resolved_at             TEXT,
    resolution_comment      TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS docmod_scan_runs (
    run_id          TEXT PRIMARY KEY,
    scope_type      TEXT NOT NULL DEFAULT 'all'
                        CHECK (scope_type IN ('all','collection','doc')),
    scope_id        TEXT,
    pack_ids        TEXT DEFAULT '[]',
    evidence_hash   TEXT,
    docs_scanned    INTEGER NOT NULL DEFAULT 0,
    findings_new    INTEGER NOT NULL DEFAULT 0,
    findings_resolved INTEGER NOT NULL DEFAULT 0,
    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at     TIMESTAMP,
    triggered_by    TEXT NOT NULL DEFAULT 'manual'
                        CHECK (triggered_by IN ('manual','reflex','daemon','api')),
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS docmod_findings (
    finding_id      TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES docmod_scan_runs(run_id),
    doc_id          TEXT NOT NULL,
    version_id      TEXT,
    chunk_link_id   TEXT,
    section_heading TEXT,
    page            INTEGER,
    pack_id         TEXT NOT NULL,
    entity_label    TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    finding_type    TEXT NOT NULL,
    currency_verdict TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (currency_verdict IN ('current','deprecated','eol','retired','divergent','unknown')),
    severity        TEXT NOT NULL DEFAULT 'medium'
                        CHECK (severity IN ('critical','high','medium','low','info')),
    rationale       TEXT,
    evidence_json   TEXT DEFAULT '[]',
    recommended_replacement TEXT,
    replacement_evidence_json TEXT DEFAULT '[]',
    confidence      REAL NOT NULL DEFAULT 0.0,
    state           TEXT NOT NULL DEFAULT 'open'
                        CHECK (state IN ('open','redline_drafted','accepted','rejected','resolved','superseded','stale')),
    redline_suggestion_id TEXT,
    prediction_id   TEXT,
    dedupe_key      TEXT NOT NULL,
    supersedes_id   TEXT REFERENCES docmod_findings(finding_id),
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_docmod_findings_doc    ON docmod_findings(doc_id, state);
CREATE INDEX IF NOT EXISTS idx_docmod_findings_dedupe ON docmod_findings(dedupe_key);
CREATE TABLE IF NOT EXISTS docmod_eol_products (
    id              TEXT PRIMARY KEY,
    product         TEXT NOT NULL,
    cycle           TEXT NOT NULL,
    eol_date        TEXT,
    eos_date        TEXT,
    latest_version  TEXT,
    lts             INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'endoflife.date'
                        CHECK (source IN ('endoflife.date','seed','manual')),
    synced_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    UNIQUE (product, cycle)
);
CREATE TABLE IF NOT EXISTS docmod_nist_pubs (
    id              TEXT PRIMARY KEY,
    pub_id          TEXT NOT NULL,
    latest_revision TEXT,
    revision_num    INTEGER,
    title           TEXT,
    url             TEXT,
    published_date  TEXT,
    source          TEXT NOT NULL DEFAULT 'nist.gov'
                        CHECK (source IN ('nist.gov','seed','manual')),
    synced_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    UNIQUE (pub_id)
);
CREATE TABLE IF NOT EXISTS docmod_defacto_standards (
    id              TEXT PRIMARY KEY,
    domain          TEXT NOT NULL,
    category        TEXT NOT NULL,
    vendor          TEXT,
    product         TEXT NOT NULL,
    version         TEXT,
    deploy_count    INTEGER NOT NULL DEFAULT 0,
    weighted_score  REAL NOT NULL DEFAULT 0.0,
    share_pct       REAL NOT NULL DEFAULT 0.0,
    computed_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS docmod_doc_scan_state (
    doc_id             TEXT PRIMARY KEY,
    last_version_id    TEXT,
    last_evidence_hash TEXT,
    last_scanned_at    TIMESTAMP,
    open_findings      INTEGER NOT NULL DEFAULT 0,
    tenant_id          TEXT,
    classification     TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS docmod_catalog_entries (
    entry_id        TEXT PRIMARY KEY,
    domain          TEXT NOT NULL,
    category        TEXT NOT NULL,
    vendor          TEXT,
    product         TEXT NOT NULL,
    model_family    TEXT,
    version         TEXT,
    status          TEXT NOT NULL DEFAULT 'approved'
                        CHECK (status IN ('approved','deprecated','retired')),
    eol_date        TEXT,
    eos_date        TEXT,
    replacement_entry_id TEXT REFERENCES docmod_catalog_entries(entry_id),
    metadata_json   TEXT DEFAULT '{}',
    tags_json       TEXT DEFAULT '[]',
    source          TEXT NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual','imported','promoted_from_defacto')),
    is_builtin      INTEGER NOT NULL DEFAULT 0,
    created_by      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    UNIQUE (domain, category, vendor, product, version)
);
CREATE TABLE IF NOT EXISTS docmod_catalog_audit (
    id              TEXT PRIMARY KEY,
    entry_id        TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    actor           TEXT NOT NULL DEFAULT 'system',
    details         TEXT DEFAULT '{}',
    recorded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS dic_claims (
    claim_id        TEXT PRIMARY KEY,
    doc_id          TEXT NOT NULL,
    version_id      TEXT NOT NULL,
    section         TEXT,
    chunk_link_id   TEXT,
    page            INTEGER,
    claim_text      TEXT NOT NULL,
    anchor_start    INTEGER NOT NULL,
    anchor_end      INTEGER NOT NULL,
    subject_label   TEXT NOT NULL,
    subject_type    TEXT,
    predicate       TEXT NOT NULL,
    object_label    TEXT,
    object_type     TEXT,
    pack_domain     TEXT,
    linked_evidence_ids TEXT,
    status          TEXT NOT NULL DEFAULT 'pending_review'
                    CHECK (status IN ('pending_review','active','invalidated','superseded')),
    supersedes_id   TEXT,
    dedupe_key      TEXT,
    prov_model      TEXT,
    prov_prompt_version TEXT,
    extracted_at    TEXT NOT NULL,
    confidence      REAL DEFAULT 1.0,
    tenant_id       TEXT DEFAULT 'default',
    classification  TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_claims_tenant   ON dic_claims(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dic_claims_doc      ON dic_claims(doc_id, version_id);
CREATE INDEX IF NOT EXISTS idx_dic_claims_subject  ON dic_claims(subject_label);
CREATE INDEX IF NOT EXISTS idx_dic_claims_dedupe   ON dic_claims(dedupe_key);
CREATE TABLE IF NOT EXISTS cortex_sessions (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    user_id         TEXT,
    domain          TEXT,
    air_gap         INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active',
    metadata_json   TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cortex_sessions_tenant ON cortex_sessions(tenant_id);
CREATE TABLE IF NOT EXISTS cortex_audit (
    id              TEXT PRIMARY KEY,
    session_id      TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    function        TEXT NOT NULL DEFAULT 'cortex',
    agent_id        TEXT,
    user_id         TEXT,
    gates_json      TEXT,
    outcome         TEXT NOT NULL DEFAULT 'pass'
        CHECK (outcome IN ('pass', 'warn', 'fail', 'blocked')),
    blocked         INTEGER NOT NULL DEFAULT 0,
    provenance_id   TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cortex_audit_session ON cortex_audit(session_id);
CREATE INDEX IF NOT EXISTS idx_cortex_audit_tenant ON cortex_audit(tenant_id);

-- Cortex service keys (migration 265) — external-caller auth for the Cortex
-- REST/MCP surface. No classification column on purpose (verified pre-RLS).
CREATE TABLE IF NOT EXISTS cortex_service_keys (
    id                       TEXT PRIMARY KEY,
    label                    TEXT NOT NULL,
    key_hash                 TEXT NOT NULL UNIQUE,
    key_prefix               TEXT,
    tenant_id                TEXT NOT NULL DEFAULT 'default',
    classification_ceiling   TEXT NOT NULL DEFAULT 'CUI',
    scopes                   TEXT,
    status                   TEXT NOT NULL DEFAULT 'active',
    created_by               TEXT,
    created_at               TEXT DEFAULT CURRENT_TIMESTAMP,
    last_used_at             TEXT,
    revoked_at               TEXT,
    revoked_by               TEXT
);
CREATE INDEX IF NOT EXISTS idx_cortex_service_keys_tenant ON cortex_service_keys(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cortex_service_keys_status ON cortex_service_keys(status);

-- Cortex canvas (chat) tables — distinct from the governance cortex_sessions/cortex_audit.
CREATE TABLE IF NOT EXISTS cortex_chat_sessions (
    session_id      TEXT PRIMARY KEY,
    user_id         TEXT DEFAULT '',
    mode            TEXT DEFAULT 'ask',
    domain          TEXT DEFAULT 'general',
    title           TEXT DEFAULT '',
    status          TEXT DEFAULT 'active',
    classification  TEXT DEFAULT 'CUI',
    tenant_id       TEXT DEFAULT 'default',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cortex_messages (
    message_id      TEXT PRIMARY KEY,
    session_id      TEXT DEFAULT '',
    turn_number     INTEGER DEFAULT 0,
    role            TEXT DEFAULT 'user',
    content         TEXT DEFAULT '',
    facade          TEXT DEFAULT '',
    grounded        INTEGER DEFAULT 0,
    confidence      TEXT DEFAULT '',
    citations       TEXT DEFAULT '',
    governance      TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI',
    tenant_id       TEXT DEFAULT 'default',
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cortex_search_history (
    query_id        TEXT PRIMARY KEY,
    session_id      TEXT DEFAULT '',
    user_id         TEXT DEFAULT '',
    mode            TEXT DEFAULT 'search',
    domain          TEXT DEFAULT 'general',
    query_text      TEXT DEFAULT '',
    strategy        TEXT DEFAULT '',
    result_count    INTEGER DEFAULT 0,
    grounded        INTEGER DEFAULT 0,
    classification  TEXT DEFAULT 'CUI',
    tenant_id       TEXT DEFAULT 'default',
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ace_step_audit_log (
    id          TEXT    PRIMARY KEY,
    step_id     TEXT    NOT NULL,
    tool        TEXT    NOT NULL,
    trust_tier  TEXT    NOT NULL,
    success     INTEGER NOT NULL,
    skipped     INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    duration_ms REAL,
    created_at  TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS ace_webhook_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id       TEXT NOT NULL DEFAULT '',
    url               TEXT NOT NULL DEFAULT '',
    status_code       INTEGER,
    response          TEXT,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    last_attempted_at TEXT,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Observability tables (mirror tools/db/init_icdev_db.py). These carry a
-- classification column but intentionally NO tenant_id column — the traces API
-- must bypass RLS predicate injection when querying them (see obx-trc-03).
CREATE TABLE IF NOT EXISTS otel_spans (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT,
    name TEXT NOT NULL,
    kind TEXT DEFAULT 'INTERNAL',
    start_time TEXT NOT NULL,
    end_time TEXT,
    duration_ms INTEGER DEFAULT 0,
    status_code TEXT DEFAULT 'UNSET',
    status_message TEXT,
    attributes TEXT,
    events TEXT,
    agent_id TEXT,
    project_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS shap_attributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    shapley_value REAL NOT NULL,
    coalition_size INTEGER,
    confidence_low REAL,
    confidence_high REAL,
    outcome_metric TEXT DEFAULT 'success',
    outcome_value REAL,
    analysis_params TEXT,
    agent_id TEXT,
    project_id TEXT,
    classification TEXT DEFAULT 'CUI',
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Observability Design Canvas (ODC) — twin snapshot round-trip + projections.
CREATE TABLE IF NOT EXISTS observability_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    template_id     TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS od_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    assessment_type TEXT NOT NULL,
    findings_json   TEXT DEFAULT '[]',
    score           REAL DEFAULT 0,
    grade           TEXT DEFAULT 'F',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS odc_twin_snapshots (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    label           TEXT NOT NULL DEFAULT '',
    service_count   INTEGER NOT NULL DEFAULT 0,
    coverage_score  REAL NOT NULL DEFAULT 0.0,
    coverage_basis  TEXT NOT NULL DEFAULT 'no_assessment',
    payload_json    TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
-- QDC + AADC digital twins (twx-cov-01). Snapshots use PDC dedup/retention
-- (NOT append-only); simulations persist the pass/warn/fail verdict.
CREATE TABLE IF NOT EXISTS qdc_twin_snapshots (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL,
    label       TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    node_count  INTEGER DEFAULT 0,
    edge_count  INTEGER DEFAULT 0,
    created_by  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS qdc_simulations (
    id                TEXT PRIMARY KEY,
    design_id         TEXT NOT NULL,
    baseline_snap_id  TEXT,
    delta_graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    verdict           TEXT NOT NULL DEFAULT 'unknown',
    findings_json     TEXT DEFAULT '[]',
    diff_json         TEXT DEFAULT '{}',
    created_by        TEXT,
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS aadc_twin_snapshots (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL,
    label       TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    node_count  INTEGER DEFAULT 0,
    edge_count  INTEGER DEFAULT 0,
    created_by  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS aadc_simulations (
    id                TEXT PRIMARY KEY,
    design_id         TEXT NOT NULL,
    baseline_snap_id  TEXT,
    delta_graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    verdict           TEXT NOT NULL DEFAULT 'unknown',
    findings_json     TEXT DEFAULT '[]',
    diff_json         TEXT DEFAULT '{}',
    created_by        TEXT,
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);
-- AIML digital twin (twx-cov-02 wave-2).
CREATE TABLE IF NOT EXISTS aiml_twin_snapshots (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL,
    label       TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    node_count  INTEGER DEFAULT 0,
    edge_count  INTEGER DEFAULT 0,
    created_by  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS aiml_simulations (
    id                TEXT PRIMARY KEY,
    design_id         TEXT NOT NULL,
    baseline_snap_id  TEXT,
    delta_graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    verdict           TEXT NOT NULL DEFAULT 'unknown',
    findings_json     TEXT DEFAULT '[]',
    diff_json         TEXT DEFAULT '{}',
    created_by        TEXT,
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Observability trace / provenance / XAI tables (obx-trc-05 retention target).
-- Columns mirror tools/db/schema/pg_consolidated.sql. All five are append-only
-- by design/NIST AU; the observability_retention reflex archives-then-prunes.
CREATE TABLE IF NOT EXISTS otel_spans (
    id              TEXT PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    parent_span_id  TEXT,
    name            TEXT NOT NULL,
    kind            TEXT DEFAULT 'INTERNAL',
    start_time      TEXT NOT NULL,
    end_time        TEXT,
    duration_ms     INTEGER DEFAULT 0,
    status_code     TEXT DEFAULT 'UNSET',
    status_message  TEXT,
    attributes      TEXT,
    events          TEXT,
    agent_id        TEXT,
    project_id      TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS prov_entities (
    id              TEXT PRIMARY KEY,
    entity_type     TEXT NOT NULL,
    label           TEXT,
    content_hash    TEXT,
    content         TEXT,
    attributes      TEXT,
    trace_id        TEXT,
    span_id         TEXT,
    agent_id        TEXT,
    project_id      TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS prov_activities (
    id              TEXT PRIMARY KEY,
    activity_type   TEXT NOT NULL,
    label           TEXT,
    start_time      TEXT,
    end_time        TEXT,
    attributes      TEXT,
    trace_id        TEXT,
    span_id         TEXT,
    agent_id        TEXT,
    project_id      TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS prov_relations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    relation_type   TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    object_id       TEXT NOT NULL,
    attributes      TEXT,
    trace_id        TEXT,
    project_id      TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS shap_attributions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    shapley_value   REAL NOT NULL,
    coalition_size  INTEGER,
    confidence_low  REAL,
    confidence_high REAL,
    outcome_metric  TEXT DEFAULT 'success',
    outcome_value   REAL,
    analysis_params TEXT,
    agent_id        TEXT,
    project_id      TEXT,
    classification  TEXT DEFAULT 'CUI',
    analyzed_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
-- AI GameDay League (gd_ai_*) tables — mirrors tools/db/schema/pg_consolidated.sql
CREATE TABLE IF NOT EXISTS gd_ai_tournaments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    scenario_pack TEXT NOT NULL DEFAULT 'cyber_adversarial',
    status TEXT NOT NULL DEFAULT 'pending',
    round_count INTEGER NOT NULL DEFAULT 5,
    round_duration_minutes INTEGER NOT NULL DEFAULT 60,
    current_round INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    game_key TEXT NOT NULL DEFAULT 'gameday',
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS gd_ai_teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL,
    team_key TEXT NOT NULL,
    name TEXT NOT NULL,
    domain TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#6c6c80',
    total_score INTEGER NOT NULL DEFAULT 0,
    rounds_won INTEGER NOT NULL DEFAULT 0,
    artifacts_suggested INTEGER NOT NULL DEFAULT 0,
    training_pairs_contributed INTEGER NOT NULL DEFAULT 0,
    game_key TEXT NOT NULL DEFAULT 'gameday',
    classification TEXT DEFAULT 'CUI',
    UNIQUE (tournament_id, team_key)
);
CREATE TABLE IF NOT EXISTS gd_ai_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL,
    round_num INTEGER NOT NULL,
    scenario_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    game_key TEXT NOT NULL DEFAULT 'gameday',
    classification TEXT DEFAULT 'CUI',
    UNIQUE (tournament_id, round_num)
);
CREATE TABLE IF NOT EXISTS gd_ai_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    team_key TEXT NOT NULL,
    member_role TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    content TEXT NOT NULL,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    model_used TEXT NOT NULL DEFAULT '',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    game_key TEXT NOT NULL DEFAULT 'gameday',
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS gd_ai_judge_evals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    team_key TEXT NOT NULL,
    quality_score REAL NOT NULL DEFAULT 0.0,
    innovation_score REAL NOT NULL DEFAULT 0.0,
    ethics_score REAL NOT NULL DEFAULT 1.0,
    adversarial_score REAL NOT NULL DEFAULT 0.0,
    compliance_score REAL NOT NULL DEFAULT 1.0,
    total_score INTEGER NOT NULL DEFAULT 0,
    routed_to_suggested INTEGER NOT NULL DEFAULT 0,
    training_pairs_extracted INTEGER NOT NULL DEFAULT 0,
    ethics_blocked INTEGER NOT NULL DEFAULT 0,
    judge_notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    game_key TEXT NOT NULL DEFAULT 'gameday',
    classification TEXT DEFAULT 'CUI',
    UNIQUE (round_id, team_id)
);
CREATE TABLE IF NOT EXISTS gd_ai_llmops_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL,
    round_id INTEGER,
    team_key TEXT NOT NULL,
    member_role TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    game_key TEXT NOT NULL DEFAULT 'gameday',
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS gd_ai_training_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL,
    team_key TEXT NOT NULL,
    member_role TEXT NOT NULL,
    prompt TEXT NOT NULL,
    completion TEXT NOT NULL,
    quality_score REAL NOT NULL DEFAULT 0.0,
    ft_dataset_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    game_key TEXT NOT NULL DEFAULT 'gameday',
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS gd_ai_leaderboard (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    team_key TEXT NOT NULL,
    rank INTEGER,
    total_score INTEGER NOT NULL DEFAULT 0,
    rounds_won INTEGER NOT NULL DEFAULT 0,
    artifacts_suggested INTEGER NOT NULL DEFAULT 0,
    training_pairs_contributed INTEGER NOT NULL DEFAULT 0,
    avg_ethics_score REAL NOT NULL DEFAULT 1.0,
    avg_innovation_score REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    game_key TEXT NOT NULL DEFAULT 'gameday',
    classification TEXT DEFAULT 'CUI',
    UNIQUE (tournament_id, team_id)
);
-- AI GameDay (TTX) tables — canonical DDL mirrors apps/ai_gameday/db.py::_DDL
CREATE TABLE IF NOT EXISTS ttx_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_slug TEXT NOT NULL,
    session_mode TEXT NOT NULL DEFAULT 'live',
    state TEXT NOT NULL DEFAULT 'pending',
    facilitator_name TEXT,
    join_code TEXT NOT NULL UNIQUE,
    duration_minutes INTEGER NOT NULL DEFAULT 120,
    max_teams INTEGER NOT NULL DEFAULT 8,
    started_at TEXT,
    ended_at TEXT,
    config_json TEXT DEFAULT '{}',
    ontology_tags_json TEXT DEFAULT '{}',
    tenant_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ttx_teams (
    team_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES ttx_sessions(session_id),
    team_name TEXT NOT NULL,
    join_code TEXT NOT NULL UNIQUE,
    total_score INTEGER NOT NULL DEFAULT 0,
    rank_pos INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ttx_team_members (
    member_id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES ttx_teams(team_id),
    player_name TEXT NOT NULL,
    role_id TEXT NOT NULL,
    persona_json TEXT DEFAULT '{}',
    joined_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ttx_injects (
    inject_id TEXT PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES ttx_sessions(session_id),
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    body_md TEXT,
    at_minute INTEGER,
    sequence_num INTEGER,
    depends_on_slug TEXT,
    state TEXT NOT NULL DEFAULT 'pending',
    config_json TEXT DEFAULT '{}',
    ontology_tags_json TEXT DEFAULT '{}',
    dispatched_at TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ttx_responses (
    response_id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES ttx_teams(team_id),
    inject_id TEXT NOT NULL REFERENCES ttx_injects(inject_id),
    response_text TEXT,
    ai_receipts_json TEXT DEFAULT '[]',
    submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
    time_taken_s REAL
);
CREATE TABLE IF NOT EXISTS ttx_scores (
    score_id INTEGER PRIMARY KEY AUTOINCREMENT,
    response_id INTEGER NOT NULL REFERENCES ttx_responses(response_id),
    team_id INTEGER NOT NULL REFERENCES ttx_teams(team_id),
    inject_id TEXT NOT NULL REFERENCES ttx_injects(inject_id),
    receipt_pts INTEGER NOT NULL DEFAULT 0,
    receipt_count INTEGER NOT NULL DEFAULT 0,
    judge_pts INTEGER NOT NULL DEFAULT 0,
    time_bonus_pts INTEGER NOT NULL DEFAULT 0,
    total_pts INTEGER NOT NULL DEFAULT 0,
    judge_rationale_json TEXT DEFAULT '{}',
    judged_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ttx_api_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES ttx_sessions(session_id),
    team_id INTEGER NOT NULL REFERENCES ttx_teams(team_id),
    tool_slug TEXT NOT NULL,
    endpoint TEXT,
    call_id TEXT NOT NULL UNIQUE,
    result_hash TEXT,
    token_count INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    called_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ttx_leaderboard (
    lb_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES ttx_sessions(session_id),
    team_id INTEGER NOT NULL REFERENCES ttx_teams(team_id),
    rank_pos INTEGER NOT NULL DEFAULT 0,
    total_score INTEGER NOT NULL DEFAULT 0,
    receipt_pts INTEGER NOT NULL DEFAULT 0,
    judge_pts INTEGER NOT NULL DEFAULT 0,
    time_bonus_pts INTEGER NOT NULL DEFAULT 0,
    computed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (session_id, team_id)
);
CREATE TABLE IF NOT EXISTS ttx_scenarios (
    scenario_id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    yaml_content TEXT NOT NULL,
    created_by TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ttx_inject_templates (
    template_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    inject_type TEXT NOT NULL DEFAULT 'custom',
    body_md TEXT,
    rubric_json TEXT DEFAULT '{}',
    ai_tools_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- LPX (LLM proxy) virtual-key storage (lpx-keys-01). Only a SHA-256 hash of the
-- issued key is stored, never plaintext. tenant_id + classification present so it
-- works with the global RLS predicate via get_connection().
CREATE TABLE IF NOT EXISTS llm_proxy_keys (
    key_id          TEXT PRIMARY KEY,
    key_hash        TEXT NOT NULL UNIQUE,
    key_prefix      TEXT NOT NULL,
    alias           TEXT,
    scope_type      TEXT NOT NULL DEFAULT 'tenant',
    scope_ref       TEXT,
    session_id      TEXT,
    max_budget_usd  REAL,
    budget_window   TEXT NOT NULL DEFAULT 'none',
    rpm_limit       INTEGER,
    tpm_limit       INTEGER,
    status          TEXT NOT NULL DEFAULT 'active',
    expires_at      TEXT,
    litellm_synced  INTEGER NOT NULL DEFAULT 0,
    rotated_from    TEXT,
    tenant_id       TEXT,
    classification  TEXT,
    created_by      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT
);
-- LPX key lifecycle audit (lpx-keys-03, NIST AU). APPEND-ONLY — see
-- APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py. Never UPDATE/DELETE.
CREATE TABLE IF NOT EXISTS llm_proxy_key_audit (
    audit_id       TEXT PRIMARY KEY,
    key_id         TEXT NOT NULL,
    action         TEXT NOT NULL,
    actor          TEXT,
    detail         TEXT,
    tenant_id      TEXT,
    classification TEXT,
    recorded_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
-- LPX per-team RPM/TPM rate ceilings + rolling usage (lpx-teams-01). Sibling of
-- ttx_api_log: gameday-scoped, no tenant_id/classification (get_connection with
-- no security context). Minute-bucket windows.
CREATE TABLE IF NOT EXISTS llm_proxy_team_limits (
    session_id   INTEGER NOT NULL,
    team_id      INTEGER NOT NULL,
    rpm_limit    INTEGER NOT NULL,
    tpm_limit    INTEGER NOT NULL,
    team_count   INTEGER,
    burst_factor REAL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (session_id, team_id)
);
CREATE TABLE IF NOT EXISTS llm_proxy_team_usage (
    session_id     INTEGER NOT NULL,
    team_id        INTEGER NOT NULL,
    window_minute  INTEGER NOT NULL,
    request_count  INTEGER NOT NULL DEFAULT 0,
    token_count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, team_id, window_minute)
);
-- LPX per-key spend ledger (lpx-keys-02). Budgets wire onto existing grouping
-- units via the key's scope; deny is scoped to a single key/window.
CREATE TABLE IF NOT EXISTS llm_proxy_spend (
    spend_id       TEXT PRIMARY KEY,
    key_id         TEXT NOT NULL,
    scope_type     TEXT NOT NULL DEFAULT 'tenant',
    scope_ref      TEXT,
    session_id     TEXT,
    window_key     TEXT NOT NULL DEFAULT 'none',
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd       REAL NOT NULL DEFAULT 0.0,
    tenant_id      TEXT,
    classification TEXT,
    recorded_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
-- AIMC (AI/ML Canvas) — model inventory + deployment-readiness check state.
-- Tenant-less canvas tables (classification, no tenant_id); read via
-- get_canvas_connection (RLS disabled). Mirrors tools/aimc/db/init_db.py.
CREATE TABLE IF NOT EXISTS aimc_models (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    metric_key      TEXT NOT NULL,
    metric_value    TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    classification  TEXT NOT NULL DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS aimc_deployment (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    check_key       TEXT NOT NULL,
    check_value     TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    classification  TEXT NOT NULL DEFAULT 'CUI'
);
-- sag-mem-01: standalone-agent per-user profile memory (migration 287).
CREATE TABLE IF NOT EXISTS sag_user_profiles (
    user_id          TEXT NOT NULL,
    tenant_id        TEXT DEFAULT '',
    classification   TEXT DEFAULT 'CUI',
    preferences_json TEXT DEFAULT '{}',
    facts_json       TEXT DEFAULT '[]',
    updated_at       TEXT,
    PRIMARY KEY (user_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS remote_agent_sessions (
    id               TEXT PRIMARY KEY,
    channel          TEXT NOT NULL,
    chat_id          TEXT NOT NULL,
    icdev_user_id    TEXT,
    tenant_id        TEXT DEFAULT '',
    context_id       TEXT NOT NULL,
    created_at       TEXT,
    last_activity_at TEXT,
    UNIQUE (channel, chat_id)
);
-- twx-fed-03: high-side compatibility report snapshots (migration 289).
CREATE TABLE IF NOT EXISTS twin_compat_reports (
    id             TEXT PRIMARY KEY,
    target_id      TEXT NOT NULL,
    source_canvas  TEXT,
    target_preset  TEXT,
    verdict        TEXT,
    blocker_count  INTEGER NOT NULL DEFAULT 0,
    content_hash   TEXT NOT NULL,
    report_json    TEXT NOT NULL,
    tenant_id      TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS divergence_idea_scores (
    id                 TEXT PRIMARY KEY,
    trace_id           TEXT NOT NULL,
    function           TEXT NOT NULL,
    idea_index         INTEGER NOT NULL,
    frame              TEXT DEFAULT '',
    idea_text          TEXT NOT NULL,
    novelty            TEXT DEFAULT 'unknown',
    viability          TEXT DEFAULT 'unknown',
    fit                TEXT DEFAULT 'unknown',
    composite          REAL DEFAULT 0.0,
    rationale          TEXT DEFAULT '',
    trap_flag          TEXT DEFAULT 'clear',
    trap_level         REAL DEFAULT 0.0,
    is_trap            INTEGER DEFAULT 0,
    trap_rationale     TEXT DEFAULT '',
    vocabulary_version TEXT DEFAULT '',
    tenant_id          TEXT NOT NULL DEFAULT 'default',
    classification     TEXT NOT NULL DEFAULT 'CUI',
    created_at         TEXT
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
