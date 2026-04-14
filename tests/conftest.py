#!/usr/bin/env python3
# CUI // SP-CTI
"""Shared pytest fixtures for ICDEV™ test suite.

D155: Project-root conftest.py centralizes test DB setup, Flask test clients,
and auth header helpers. Prevents duplication across 20+ test files.
"""

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on sys.path (MUST be first to avoid tools package shadowing)
BASE_DIR = Path(__file__).resolve().parent.parent
_base_str = str(BASE_DIR)
# Remove any tools subdirectory entries that could shadow the tools package
_to_remove = [p for p in sys.path if p.startswith(_base_str + os.sep + "tools")]
for p in _to_remove:
    sys.path.remove(p)
# Ensure project root is first
if _base_str in sys.path:
    sys.path.remove(_base_str)
sys.path.insert(0, _base_str)

# Force SQLite backend for tests (override .env PostgreSQL setting)
os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"


# ---------------------------------------------------------------------------
# Minimal ICDEV™ schema (subset for fast test DB creation)
# ---------------------------------------------------------------------------
MINIMAL_ICDEV_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    type TEXT NOT NULL DEFAULT 'webapp',
    classification TEXT NOT NULL DEFAULT 'CUI',
    status TEXT NOT NULL DEFAULT 'active',
    tech_stack_backend TEXT,
    tech_stack_frontend TEXT,
    tech_stack_database TEXT,
    directory_path TEXT NOT NULL DEFAULT '/tmp',
    created_by TEXT,
    impact_level TEXT DEFAULT 'IL5',
    cloud_environment TEXT DEFAULT 'aws-govcloud',
    target_frameworks TEXT,
    ato_status TEXT DEFAULT 'none',
    accrediting_authority TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    url TEXT NOT NULL DEFAULT 'http://localhost:8443',
    status TEXT NOT NULL DEFAULT 'inactive',
    capabilities TEXT,
    last_heartbeat TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    project_id TEXT,
    details TEXT,
    classification TEXT DEFAULT 'CUI',
    session_id TEXT,
    source_ip TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    title TEXT,
    severity TEXT,
    source TEXT,
    status TEXT DEFAULT 'active',
    project_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS poam_items (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    finding TEXT,
    status TEXT DEFAULT 'open',
    severity TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nist_controls (
    id TEXT PRIMARY KEY,
    control_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    title TEXT,
    status TEXT DEFAULT 'not_assessed',
    implementation_status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stig_findings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    rule_id TEXT,
    severity TEXT,
    status TEXT DEFAULT 'open',
    title TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dev_profiles (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK(scope IN ('platform','tenant','program','project','user')),
    scope_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    profile_md TEXT,
    profile_yaml TEXT NOT NULL,
    inherits_from TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_active INTEGER DEFAULT 1,
    change_summary TEXT,
    approved_by TEXT,
    approved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dev_profiles_scope ON dev_profiles(scope, scope_id, is_active);
CREATE INDEX IF NOT EXISTS idx_dev_profiles_active ON dev_profiles(scope_id, is_active, version);

CREATE TABLE IF NOT EXISTS dev_profile_locks (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES dev_profiles(id),
    dimension_path TEXT NOT NULL,
    lock_owner_role TEXT NOT NULL CHECK(lock_owner_role IN ('isso','architect','pm','admin')),
    locked_by TEXT NOT NULL,
    locked_at TEXT NOT NULL DEFAULT (datetime('now')),
    reason TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_dev_profile_locks_profile ON dev_profile_locks(profile_id, is_active);

CREATE TABLE IF NOT EXISTS dev_profile_detections (
    id TEXT PRIMARY KEY,
    tenant_id TEXT,
    project_id TEXT,
    session_id TEXT,
    repo_url TEXT,
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    detection_results TEXT NOT NULL,
    accepted INTEGER DEFAULT 0,
    accepted_by TEXT,
    accepted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dev_profile_detections_tenant ON dev_profile_detections(tenant_id);

-- Bayesian Teaching Intelligence (D-BT-1)
CREATE TABLE IF NOT EXISTS bayesian_teaching_scores (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    candidate_type TEXT DEFAULT 'pair',
    info_gain_score REAL DEFAULT 0.0,
    dimensions TEXT DEFAULT '{}',
    threshold_band TEXT DEFAULT 'medium',
    context_id TEXT,
    scored_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Workflow Discipline Engine (D-WF-1 through D-WF-7)
CREATE TABLE IF NOT EXISTS workflow_loops (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    phase TEXT,
    status TEXT DEFAULT 'planning' CHECK(status IN ('planning','planned','applying','applied','unifying','closed','abandoned')),
    plan_summary TEXT,
    task_count INTEGER DEFAULT 0,
    planned_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT,
    abandon_reason TEXT
);

CREATE TABLE IF NOT EXISTS workflow_acceptance_criteria (
    id TEXT PRIMARY KEY,
    loop_id TEXT NOT NULL,
    given_clause TEXT,
    when_clause TEXT,
    then_clause TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','passed','failed','skipped')),
    evidence TEXT,
    verified_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (loop_id) REFERENCES workflow_loops(id)
);

CREATE TABLE IF NOT EXISTS workflow_reconciliations (
    id TEXT PRIMARY KEY,
    loop_id TEXT NOT NULL,
    planned_items TEXT DEFAULT '[]',
    actual_items TEXT DEFAULT '[]',
    deviations TEXT DEFAULT '[]',
    severity TEXT DEFAULT 'minor',
    reconciled_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (loop_id) REFERENCES workflow_loops(id)
);

CREATE TABLE IF NOT EXISTS workflow_handoffs (
    id TEXT PRIMARY KEY,
    loop_id TEXT NOT NULL,
    content TEXT NOT NULL,
    generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (loop_id) REFERENCES workflow_loops(id)
);

-- Event-Sourced Workflow Replay (D-REPLAY-1 through D-REPLAY-7)
CREATE TABLE IF NOT EXISTS workflow_replay_sessions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    project_id TEXT,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','failed')),
    resume_step TEXT,
    completed_steps_snapshot TEXT DEFAULT '[]',
    total_steps INTEGER DEFAULT 0,
    skipped_steps INTEGER DEFAULT 0,
    replayed_steps INTEGER DEFAULT 0,
    error_message TEXT,
    triggered_by TEXT DEFAULT 'replay-engine',
    classification TEXT DEFAULT 'CUI',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

-- NemoClaw Sandboxing (D-NC-1 through D-NC-6)
CREATE TABLE IF NOT EXISTS credential_broker_log (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    function_name TEXT,
    action TEXT NOT NULL,
    provider TEXT,
    granted_at TEXT NOT NULL DEFAULT (datetime('now')),
    ttl_seconds INTEGER DEFAULT 3600,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS blueprint_digests (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    file_count INTEGER DEFAULT 0,
    computed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS egress_policy_audit (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    policy_snapshot TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS propagation_verifications (
    id TEXT PRIMARY KEY,
    propagation_id TEXT NOT NULL,
    checks TEXT DEFAULT '{}',
    overall_result TEXT DEFAULT 'pending',
    verified_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Evolution Daemon (D-EVO-1)
CREATE TABLE IF NOT EXISTS evolution_audit (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    reflex_name TEXT,
    risk_tier TEXT,
    details TEXT DEFAULT '{}',
    success INTEGER DEFAULT 1,
    duration_ms INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS evolution_reflex_state (
    reflex_name TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 1,
    last_run_at TEXT,
    consecutive_failures INTEGER DEFAULT 0,
    circuit_breaker_open INTEGER DEFAULT 0
);

-- Outcome Verifier (D-EVO-6)
CREATE TABLE IF NOT EXISTS outcome_verification_log (
    id TEXT PRIMARY KEY,
    resolution_id TEXT NOT NULL,
    pr_url TEXT,
    verification_type TEXT NOT NULL,
    result TEXT DEFAULT 'pending',
    confidence_delta REAL DEFAULT 0.0,
    checked_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Engineering Review Board (Phase 67, D-RB-1 through D-RB-7)
CREATE TABLE IF NOT EXISTS review_board_audit (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    reflex_name TEXT,
    risk_tier TEXT,
    details TEXT,
    success INTEGER,
    duration_ms INTEGER,
    metric_name TEXT,
    metric_value REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS review_board_reflex_state (
    reflex_name TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 1,
    last_run_at TEXT,
    consecutive_failures INTEGER DEFAULT 0,
    circuit_breaker_open INTEGER DEFAULT 0,
    total_runs INTEGER DEFAULT 0,
    total_successes INTEGER DEFAULT 0,
    total_failures INTEGER DEFAULT 0,
    last_metric_value REAL,
    last_error TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS review_board_findings (
    id TEXT PRIMARY KEY,
    reflex_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    recommendation TEXT,
    evidence TEXT,
    confidence REAL DEFAULT 0.0,
    auto_fixable INTEGER DEFAULT 0,
    fix_applied INTEGER DEFAULT 0,
    sha256 TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS review_board_remediation_log (
    id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL,
    reflex_name TEXT,
    category TEXT NOT NULL,
    severity TEXT,
    confidence REAL DEFAULT 0.0,
    tier TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    fix_description TEXT,
    fix_result TEXT,
    verification TEXT,
    dry_run INTEGER DEFAULT 0,
    duration_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Phase 68: Autonomy Engine (D-AE-1 through D-AE-12)
CREATE TABLE IF NOT EXISTS autonomy_trust_state (
    category TEXT PRIMARY KEY,
    alpha REAL NOT NULL DEFAULT 2.0,
    beta REAL NOT NULL DEFAULT 8.0,
    ceiling REAL NOT NULL DEFAULT 1.0,
    total_observations INTEGER DEFAULT 0,
    total_successes INTEGER DEFAULT 0,
    total_failures INTEGER DEFAULT 0,
    current_tier TEXT DEFAULT 'observer',
    last_updated TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS autonomy_observations (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    outcome TEXT NOT NULL,
    alpha_before REAL,
    beta_before REAL,
    alpha_after REAL,
    beta_after REAL,
    mean_before REAL,
    mean_after REAL,
    tier_before TEXT,
    tier_after TEXT,
    source TEXT,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS autonomy_actions (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    action_type TEXT NOT NULL,
    decision TEXT NOT NULL,
    trust_mean REAL,
    thompson_sample REAL,
    tier_required TEXT,
    tier_current TEXT,
    details TEXT,
    coherence_passed INTEGER,
    remediation_applied INTEGER,
    outcome TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS autonomy_behavior_log (
    id TEXT PRIMARY KEY,
    signal_type TEXT NOT NULL,
    finding_id TEXT,
    pr_url TEXT,
    alpha_delta REAL DEFAULT 0.0,
    beta_delta REAL DEFAULT 0.0,
    category TEXT,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Phase 67: Bayesian Autoresearch
CREATE TABLE IF NOT EXISTS experiment_programs (
    id TEXT PRIMARY KEY, domain TEXT NOT NULL UNIQUE,
    objective_metric TEXT NOT NULL, objective_direction TEXT NOT NULL,
    measurement_command TEXT NOT NULL, metric_path TEXT,
    modifiable_paths TEXT, forbidden_paths TEXT,
    time_budget_seconds INTEGER NOT NULL DEFAULT 300,
    keep_threshold REAL NOT NULL DEFAULT 0.005,
    category_order TEXT, categories TEXT, config TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_candidates (
    id TEXT PRIMARY KEY, domain TEXT NOT NULL,
    hypothesis TEXT NOT NULL, category TEXT,
    modifications TEXT, source TEXT DEFAULT 'manual',
    signal_id TEXT, status TEXT NOT NULL DEFAULT 'created',
    embedding BLOB, content_hash TEXT,
    info_gain_score REAL, thompson_sample REAL,
    estimated_impact TEXT, risk_level TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_results (
    id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
    domain TEXT NOT NULL, hypothesis TEXT NOT NULL,
    category TEXT, pre_metric REAL, post_metric REAL,
    metric_delta REAL, improvement_pct REAL,
    decision TEXT NOT NULL, decision_rationale TEXT,
    duration_ms INTEGER, git_branch TEXT, git_commit TEXT,
    tests_passed INTEGER, coherence_passed INTEGER,
    files_modified TEXT, details TEXT,
    classification TEXT DEFAULT 'CUI', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_landscapes (
    id TEXT PRIMARY KEY, domain TEXT NOT NULL,
    category TEXT NOT NULL, alpha REAL NOT NULL DEFAULT 2.0,
    beta_val REAL NOT NULL DEFAULT 8.0,
    total_experiments INTEGER NOT NULL DEFAULT 0,
    total_kept INTEGER NOT NULL DEFAULT 0,
    total_discarded INTEGER NOT NULL DEFAULT 0,
    best_improvement REAL DEFAULT 0.0,
    cumulative_improvement REAL DEFAULT 0.0,
    last_experiment_at TEXT, updated_at TEXT NOT NULL,
    UNIQUE(domain, category)
);
CREATE TABLE IF NOT EXISTS bayesian_experiment_scores (
    id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL,
    domain TEXT NOT NULL, info_gain_score REAL NOT NULL,
    dimensions TEXT, threshold_band TEXT,
    thompson_sample REAL, prior_distribution TEXT,
    selected INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI', scored_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    thinking_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    task_id TEXT,
    cost_estimate_usd REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agent_token_budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    month TEXT NOT NULL,
    budget_usd REAL NOT NULL DEFAULT 0.0,
    spent_usd REAL NOT NULL DEFAULT 0.0,
    warning_threshold REAL NOT NULL DEFAULT 0.8,
    hard_stop INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(agent_id, month)
);

CREATE TABLE IF NOT EXISTS agent_task_leases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL,
    leased_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'released', 'expired')),
    classification TEXT DEFAULT 'CUI'
);

CREATE TABLE IF NOT EXISTS scout_scans (
    id TEXT PRIMARY KEY,
    scan_date TEXT NOT NULL,
    pillar_results TEXT,
    digest_path TEXT,
    total_findings INTEGER DEFAULT 0,
    signals_fed INTEGER DEFAULT 0,
    repos_added INTEGER DEFAULT 0,
    genesis_status TEXT,
    genesis_branch TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS scout_audit (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    pillar TEXT,
    details TEXT,
    success INTEGER,
    duration_ms INTEGER,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT ''
);

-- OpenClaw Bridge (Phase 69)
CREATE TABLE IF NOT EXISTS openclaw_imports (
    id TEXT PRIMARY KEY,
    source_url TEXT,
    source_path TEXT NOT NULL,
    openclaw_slug TEXT,
    openclaw_author TEXT,
    skill_name TEXT NOT NULL,
    skill_version TEXT DEFAULT '1.0.0',
    quarantine_path TEXT NOT NULL,
    sha256_hash TEXT NOT NULL,
    has_executable_content INTEGER DEFAULT 0,
    scan_status TEXT NOT NULL DEFAULT 'pending',
    gate_results TEXT,
    review_required INTEGER DEFAULT 0,
    review_id TEXT,
    trust_score REAL DEFAULT 0.30,
    status TEXT NOT NULL DEFAULT 'quarantined',
    imported_by TEXT NOT NULL,
    promoted_by TEXT,
    promoted_at TIMESTAMP,
    rejected_by TEXT,
    rejected_reason TEXT,
    marketplace_asset_id TEXT,
    tenant_id TEXT NOT NULL,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS openclaw_exports (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    output_path TEXT,
    exported_by TEXT NOT NULL,
    review_id TEXT,
    review_status TEXT DEFAULT 'pending',
    stripping_log TEXT,
    sha256_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daemon_checkpoints (
    id              TEXT PRIMARY KEY,
    daemon_name     TEXT NOT NULL,
    reflex_name     TEXT NOT NULL,
    phase           TEXT NOT NULL,
    partial_results TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(daemon_name, reflex_name)
);

-- LLM Gateway Audit (append-only)
CREATE TABLE IF NOT EXISTS llm_gateway_audit (
    id TEXT PRIMARY KEY,
    request_id TEXT,
    agent_id TEXT,
    function_name TEXT,
    model_id TEXT,
    pre_check_result TEXT,
    post_check_result TEXT,
    created_at TEXT
);

-- Prompt Registry
CREATE TABLE IF NOT EXISTS prompt_versions (
    id TEXT PRIMARY KEY,
    name TEXT,
    version INTEGER,
    template TEXT,
    status TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS prompt_ab_tests (
    id TEXT PRIMARY KEY,
    prompt_name TEXT,
    version_a INTEGER,
    version_b INTEGER,
    status TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS prompt_audit_log (
    id TEXT PRIMARY KEY,
    prompt_name TEXT,
    action TEXT,
    created_at TEXT
);

-- LLM Cost Intelligence
CREATE TABLE IF NOT EXISTS llm_cost_alerts (
    id TEXT PRIMARY KEY,
    alert_type TEXT,
    severity TEXT,
    model_id TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS llm_cost_recommendations (
    id TEXT PRIMARY KEY,
    rec_type TEXT,
    function_name TEXT,
    status TEXT,
    created_at TEXT
);

-- Model Drift Monitoring
CREATE TABLE IF NOT EXISTS model_quality_scores (
    id TEXT PRIMARY KEY,
    model_id TEXT,
    function_name TEXT,
    quality_score REAL,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS model_drift_events (
    id TEXT PRIMARY KEY,
    model_id TEXT,
    drift_type TEXT,
    severity TEXT,
    created_at TEXT
);

-- Agent Topology
CREATE TABLE IF NOT EXISTS agent_topology_snapshots (
    id TEXT PRIMARY KEY,
    snapshot_at TEXT,
    node_count INTEGER,
    edge_count INTEGER
);

-- SRE SLO & Incident Management
CREATE TABLE IF NOT EXISTS sre_slos (
    id TEXT PRIMARY KEY,
    name TEXT,
    target REAL,
    window_days INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS sre_slo_measurements (
    id TEXT PRIMARY KEY,
    slo_id TEXT,
    value REAL,
    measured_at TEXT
);

CREATE TABLE IF NOT EXISTS sre_runbooks (
    id TEXT PRIMARY KEY,
    name TEXT,
    alert_pattern TEXT,
    risk_tier TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS sre_runbook_executions (
    id TEXT PRIMARY KEY,
    runbook_id TEXT,
    status TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS sre_incidents (
    id TEXT PRIMARY KEY,
    title TEXT,
    severity TEXT,
    status TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS sre_incident_events (
    id TEXT PRIMARY KEY,
    incident_id TEXT,
    event_type TEXT,
    created_at TEXT
);

-- CRAG Evaluation Framework (D-RAG-23)
CREATE TABLE IF NOT EXISTS rag_evaluation_campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scoring_mode TEXT DEFAULT 'both' CHECK(scoring_mode IN ('ragas','crag','both')),
    test_set_path TEXT,
    task_type TEXT DEFAULT 'T1' CHECK(task_type IN ('T1','T2','T3')),
    total_cases INTEGER DEFAULT 0,
    aggregate_crag_score REAL,
    aggregate_ragas_score REAL,
    status TEXT DEFAULT 'running' CHECK(status IN ('running','completed','failed')),
    details TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rag_evaluation_results (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    query TEXT NOT NULL,
    question_type TEXT CHECK(question_type IN (
        'simple','simple_condition','set','comparison',
        'aggregation','multi_hop','post_processing','false_premise'
    )),
    entity_popularity TEXT CHECK(entity_popularity IN ('head','torso','tail')),
    crag_score REAL,
    ragas_ndcg REAL,
    ragas_mrr REAL,
    ragas_faithfulness REAL,
    ragas_relevancy REAL,
    answer TEXT,
    ground_truth TEXT,
    is_false_premise INTEGER DEFAULT 0,
    details TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Phase 70: Redaction & Data Protection
CREATE TABLE IF NOT EXISTS redaction_registry (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    real_hash TEXT NOT NULL,
    surrogate TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    UNIQUE(session_id, entity_type, real_hash)
);
CREATE TABLE IF NOT EXISTS redaction_audit (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    text_length INTEGER NOT NULL,
    detection_count INTEGER NOT NULL,
    entity_types TEXT NOT NULL,
    impact_level TEXT NOT NULL,
    module TEXT DEFAULT 'unknown',
    classification TEXT DEFAULT 'CUI'
);

CREATE TABLE IF NOT EXISTS compliance_evidence_chain (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id       TEXT NOT NULL,
    event_id       TEXT NOT NULL,
    source         TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    actor          TEXT,
    action         TEXT NOT NULL,
    oscal_controls TEXT DEFAULT '[]',
    oscal_family   TEXT,
    evidence_type  TEXT DEFAULT 'audit',
    classification TEXT DEFAULT 'CUI // SP-CTI',
    event_ts       TEXT NOT NULL,
    details_json   TEXT,
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cec_chain ON compliance_evidence_chain(chain_id);
CREATE INDEX IF NOT EXISTS idx_cec_source ON compliance_evidence_chain(source);
CREATE INDEX IF NOT EXISTS idx_cec_ts ON compliance_evidence_chain(event_ts);

-- Oracle Anticipatory Agent (Phase Oracle)
CREATE TABLE IF NOT EXISTS oracle_predictions (
    id              TEXT PRIMARY KEY,
    lens            TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    confidence      REAL NOT NULL,
    evidence        TEXT DEFAULT '[]',
    proposed_action TEXT DEFAULT '',
    tags            TEXT DEFAULT '[]',
    created_at      TEXT NOT NULL,
    run_id          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oracle_pred_lens ON oracle_predictions(lens);
CREATE INDEX IF NOT EXISTS idx_oracle_pred_conf ON oracle_predictions(confidence);

CREATE TABLE IF NOT EXISTS genesis_gkp (
    id               TEXT PRIMARY KEY,
    artifact_type    TEXT NOT NULL,
    payload          TEXT NOT NULL DEFAULT '{}',
    content_hash     TEXT,
    confidence       REAL DEFAULT 0.0,
    promotion_status TEXT DEFAULT 'pending',
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gkp_type ON genesis_gkp(artifact_type);
CREATE INDEX IF NOT EXISTS idx_gkp_status ON genesis_gkp(promotion_status);

CREATE TABLE IF NOT EXISTS genesis_audit (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    reflex_name     TEXT,
    risk_tier       TEXT,
    details         TEXT,
    success         INTEGER,
    duration_ms     INTEGER,
    metric_name     TEXT,
    metric_value    REAL,
    gkp_id          TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_genesis_audit_type ON genesis_audit(event_type);
CREATE INDEX IF NOT EXISTS idx_genesis_audit_reflex ON genesis_audit(reflex_name);
CREATE INDEX IF NOT EXISTS idx_genesis_audit_created ON genesis_audit(created_at);

CREATE TABLE IF NOT EXISTS genesis_generated_goals (
    id              TEXT PRIMARY KEY,
    version         INTEGER NOT NULL DEFAULT 1,
    domain_label    TEXT NOT NULL,
    title           TEXT NOT NULL,
    slug            TEXT NOT NULL,
    novelty_score   REAL NOT NULL DEFAULT 0.0,
    quality_score   REAL NOT NULL DEFAULT 0.0,
    evidence_count  INTEGER NOT NULL DEFAULT 0,
    keywords        TEXT,
    goal_markdown   TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'suggested'
        CHECK(status IN ('suggested','approved','rejected','superseded')),
    gkp_id          TEXT,
    goal_file_path  TEXT,
    rejection_reason TEXT,
    approved_at     TEXT,
    rejected_at     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gg_status ON genesis_generated_goals(status);
CREATE INDEX IF NOT EXISTS idx_gg_domain ON genesis_generated_goals(domain_label);
CREATE INDEX IF NOT EXISTS idx_gg_created ON genesis_generated_goals(created_at);

CREATE TABLE IF NOT EXISTS kanban_tasks (
    id                   TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    description          TEXT,
    task_type            TEXT DEFAULT 'chore',
    priority             TEXT DEFAULT 'low',
    status               TEXT DEFAULT 'backlog',
    scheduled_at         TEXT,
    completed_at         TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now')),
    source_prediction_id TEXT,
    executor_type        TEXT DEFAULT 'claude_cli',
    execution_id         TEXT,
    executor_url         TEXT,
    depends_on_task_id   TEXT REFERENCES kanban_tasks(id),
    failure_count        INTEGER DEFAULT 0,
    last_failure_reason  TEXT,
    last_failure_at      TEXT,
    dispatch_source      TEXT DEFAULT 'unknown'
);
CREATE INDEX IF NOT EXISTS idx_kanban_depends ON kanban_tasks(depends_on_task_id);
CREATE INDEX IF NOT EXISTS idx_kanban_failure_count ON kanban_tasks(failure_count);
CREATE INDEX IF NOT EXISTS idx_kanban_dispatch_source ON kanban_tasks(dispatch_source);

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
    remediation_attempted INTEGER DEFAULT 0,
    remediation_success   INTEGER,
    remediation_type      TEXT,
    dispatch_source       TEXT DEFAULT 'unknown',
    created_at            TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_kv_task_id ON kanban_verifications (task_id);
CREATE INDEX IF NOT EXISTS idx_kv_result ON kanban_verifications (result);
CREATE INDEX IF NOT EXISTS idx_kv_dispatch_source ON kanban_verifications (dispatch_source);

CREATE TABLE IF NOT EXISTS scoped_memory_entries (
    id           TEXT PRIMARY KEY,
    partition    TEXT NOT NULL,
    entry_type   TEXT NOT NULL,
    content      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    importance   INTEGER NOT NULL DEFAULT 5,
    metadata     TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sme_partition ON scoped_memory_entries (partition);
CREATE INDEX IF NOT EXISTS idx_sme_hash ON scoped_memory_entries (partition, content_hash);

CREATE TABLE IF NOT EXISTS canvas_projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    links_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS canvas_kg_nodes (
    id TEXT PRIMARY KEY,
    canvas TEXT NOT NULL,
    design_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    node_type TEXT,
    label TEXT,
    metadata_json TEXT DEFAULT '{}',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS canvas_kg_edges (
    id TEXT PRIMARY KEY,
    canvas TEXT NOT NULL,
    design_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    edge_type TEXT,
    metadata_json TEXT DEFAULT '{}',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS canvas_kg_build_log (
    build_id TEXT PRIMARY KEY,
    canvas TEXT NOT NULL,
    design_id TEXT NOT NULL,
    nodes_upserted INTEGER DEFAULT 0,
    edges_upserted INTEGER DEFAULT 0,
    duration_ms REAL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    type TEXT DEFAULT 'event',
    importance INTEGER DEFAULT 5,
    embedding BLOB,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT,
    user_id TEXT,
    tenant_id TEXT,
    source TEXT DEFAULT 'manual'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_content_hash_user
    ON memory_entries(content_hash, user_id);
CREATE INDEX IF NOT EXISTS idx_memory_user_id ON memory_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_memory_tenant_id ON memory_entries(tenant_id);
CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_entries(created_at);

CREATE TABLE IF NOT EXISTS daily_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_daily_logs_date ON daily_logs(date);

CREATE TABLE IF NOT EXISTS memory_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT,
    query TEXT,
    results_count INTEGER,
    search_type TEXT,
    accessed_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mem_access_search ON memory_access_log(search_type);

CREATE TABLE IF NOT EXISTS memory_consolidation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entry_id INTEGER,
    target_entry_id INTEGER,
    action TEXT NOT NULL CHECK(action IN ('MERGE','REPLACE','KEEP_SEPARATE','UPDATE','SKIP')),
    method TEXT CHECK(method IN ('llm','keyword')),
    similarity_score REAL,
    reasoning TEXT,
    merged_content TEXT,
    dry_run INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mem_consol_action ON memory_consolidation_log(action);
CREATE INDEX IF NOT EXISTS idx_mem_consol_source ON memory_consolidation_log(source_entry_id);

-- AlphaDesk: fundamental metrics (migration 011)
CREATE TABLE IF NOT EXISTS ad_fundamental_metrics (
    id                      TEXT PRIMARY KEY,
    ticker                  TEXT NOT NULL,
    as_of_date              TEXT NOT NULL,
    pe_ratio                REAL,
    pb_ratio                REAL,
    fcf_yield               REAL,
    roe                     REAL,
    roa                     REAL,
    roic                    REAL,
    gross_margin            REAL,
    operating_margin        REAL,
    net_margin              REAL,
    eps_ttm                 REAL,
    eps_growth_3y           REAL,
    dividend_yield          REAL,
    payout_ratio            REAL,
    dividend_growth_5y      REAL,
    debt_to_equity          REAL,
    accruals_ratio          REAL,
    insider_buy_ratio       REAL,
    source                  TEXT DEFAULT 'manual',
    classification          TEXT DEFAULT 'CUI // SP-CTI',
    created_at              TEXT DEFAULT (datetime('now')),
    updated_at              TEXT DEFAULT (datetime('now')),
    UNIQUE(ticker, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_ad_fm_ticker_date ON ad_fundamental_metrics(ticker, as_of_date);

-- AlphaDesk: quality scores (migration 011)
CREATE TABLE IF NOT EXISTS ad_quality_scores (
    id                              TEXT PRIMARY KEY,
    ticker                          TEXT NOT NULL,
    as_of_date                      TEXT NOT NULL,
    value_quality                   REAL,
    growth_quality                  REAL,
    profitability_quality           REAL,
    balance_sheet_quality           REAL,
    capital_allocation_quality      REAL,
    moat_score                      REAL,
    composite_quality_score         REAL,
    stagflation_quality_adjustment  REAL DEFAULT 0,
    model_version                   TEXT DEFAULT 'v1',
    classification                  TEXT DEFAULT 'CUI // SP-CTI',
    created_at                      TEXT DEFAULT (datetime('now')),
    updated_at                      TEXT DEFAULT (datetime('now')),
    UNIQUE(ticker, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_ad_qs_ticker_date ON ad_quality_scores(ticker, as_of_date);
CREATE INDEX IF NOT EXISTS idx_ad_qs_composite ON ad_quality_scores(composite_quality_score);

-- AlphaDesk: perspective scores (confluence_scorer lookups)
CREATE TABLE IF NOT EXISTS ad_perspective_scores (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    run_id TEXT,
    net_score REAL NOT NULL DEFAULT 0.0,
    consensus INTEGER NOT NULL DEFAULT 0,
    signal TEXT DEFAULT '',
    bull_conviction REAL DEFAULT 0.0,
    bear_conviction REAL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ad_ps_ticker ON ad_perspective_scores(ticker);

-- AlphaDesk: event_stack confluence pillar tables (migration 022)
CREATE TABLE IF NOT EXISTS ad_earnings_history (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    report_date TEXT NOT NULL,
    surprise_pct REAL,
    eps_actual REAL,
    eps_estimate REAL,
    revenue_actual REAL,
    revenue_estimate REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ad_earnings_history_ticker_date ON ad_earnings_history (ticker, report_date DESC);

CREATE TABLE IF NOT EXISTS ad_analyst_actions (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    action TEXT DEFAULT '',
    analyst TEXT DEFAULT '',
    firm TEXT DEFAULT '',
    price_target REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ad_analyst_actions_ticker_date ON ad_analyst_actions (ticker, created_at DESC);

CREATE TABLE IF NOT EXISTS ad_analyst_ratings (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    change TEXT DEFAULT '',
    rating TEXT DEFAULT '',
    analyst TEXT DEFAULT '',
    firm TEXT DEFAULT '',
    price_target REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ad_analyst_ratings_ticker_date ON ad_analyst_ratings (ticker, created_at DESC);

CREATE TABLE IF NOT EXISTS ad_insider_transactions (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    transaction_type TEXT DEFAULT '',
    insider_name TEXT DEFAULT '',
    insider_title TEXT DEFAULT '',
    shares REAL,
    price REAL,
    value REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ad_insider_txn_ticker_date ON ad_insider_transactions (ticker, created_at DESC);

-- AlphaDesk: news catalyst table (migration 024)
CREATE TABLE IF NOT EXISTS ad_news_catalysts (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    headline TEXT DEFAULT '',
    source TEXT DEFAULT '',
    sentiment TEXT DEFAULT '',
    url TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ad_news_catalysts_ticker_date ON ad_news_catalysts (ticker, created_at DESC);
"""

# ---------------------------------------------------------------------------
# Minimal Platform DB schema (SaaS)
# ---------------------------------------------------------------------------
MINIMAL_PLATFORM_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    tier TEXT DEFAULT 'starter',
    impact_level TEXT DEFAULT 'IL4',
    status TEXT DEFAULT 'active',
    settings TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    email TEXT NOT NULL,
    display_name TEXT,
    role TEXT DEFAULT 'developer',
    password_hash TEXT,
    last_login TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL,
    scopes TEXT DEFAULT '["*"]',
    is_active INTEGER DEFAULT 1,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL UNIQUE,
    tier TEXT NOT NULL DEFAULT 'starter',
    status TEXT DEFAULT 'active',
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    status_code INTEGER,
    response_time_ms INTEGER,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS audit_platform (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    details TEXT,
    ip_address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Knowledge Graph (D-KARL-1 through D-KARL-8)
CREATE TABLE IF NOT EXISTS kg_graphs (
    id TEXT PRIMARY KEY, project_id TEXT, name TEXT NOT NULL,
    description TEXT, entity_count INTEGER DEFAULT 0,
    edge_count INTEGER DEFAULT 0, metadata TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kg_nodes (
    id TEXT PRIMARY KEY, graph_id TEXT NOT NULL,
    label TEXT NOT NULL, entity_type TEXT NOT NULL,
    properties TEXT DEFAULT '{}', embedding BLOB,
    centrality REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kg_edges (
    id TEXT PRIMARY KEY, graph_id TEXT NOT NULL,
    source_id TEXT NOT NULL, target_id TEXT NOT NULL,
    relationship TEXT NOT NULL, weight REAL DEFAULT 1.0,
    properties TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kg_retrieval_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, query_hash TEXT NOT NULL,
    profile TEXT, project_id TEXT, node_count INTEGER DEFAULT 0,
    edge_count INTEGER DEFAULT 0, top_score REAL DEFAULT 0.0,
    compressed INTEGER DEFAULT 0, duration_ms REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Fine-Tuning datasets and examples (D-FT-9)
CREATE TABLE IF NOT EXISTS ft_datasets (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, purpose TEXT DEFAULT 'general',
    description TEXT, base_model TEXT, version INTEGER DEFAULT 1,
    example_count INTEGER DEFAULT 0, classification TEXT DEFAULT 'CUI',
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ft_dataset_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT, dataset_id TEXT,
    system_prompt TEXT DEFAULT '', user_input TEXT,
    expected_output TEXT, source TEXT DEFAULT 'manual',
    source_chunk_id TEXT, source_document_id TEXT,
    quality_score REAL DEFAULT 0.0, compliance_score REAL DEFAULT 0.0,
    relevance_score REAL DEFAULT 0.0, approved INTEGER DEFAULT 0,
    labeled_by TEXT, labeled_at TIMESTAMP, content_hash TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- RAG-to-FT Pipeline (D-KARL-5)
CREATE TABLE IF NOT EXISTS ft_pipeline_runs (
    id TEXT PRIMARY KEY, run_type TEXT NOT NULL DEFAULT 'rag_to_ft',
    source_type TEXT, chunks_processed INTEGER DEFAULT 0,
    pairs_generated INTEGER DEFAULT 0, pairs_approved INTEGER DEFAULT 0,
    retrain_triggered INTEGER DEFAULT 0, status TEXT DEFAULT 'running',
    error TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- Quality Monitoring (D-KARL-8)
CREATE TABLE IF NOT EXISTS ft_quality_snapshots (
    id TEXT PRIMARY KEY, snapshot_type TEXT NOT NULL,
    metric_name TEXT NOT NULL, metric_value REAL NOT NULL,
    baseline_value REAL, below_threshold INTEGER DEFAULT 0,
    details TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Hyperparameter Search
CREATE TABLE IF NOT EXISTS ft_hp_searches (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'grid',
    search_space TEXT NOT NULL DEFAULT '{}',
    max_trials INTEGER DEFAULT 9,
    completed_trials INTEGER DEFAULT 0,
    best_trial_id TEXT,
    best_score REAL,
    best_params TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ft_hp_trials (
    id TEXT PRIMARY KEY,
    search_id TEXT NOT NULL,
    trial_number INTEGER NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    training_job_id TEXT,
    eval_score REAL,
    eval_metrics TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Phase 71 — Sandbox Executor (D-SEC-10)
CREATE TABLE IF NOT EXISTS sandbox_execution_log (
    id TEXT PRIMARY KEY,
    executor_type TEXT NOT NULL,
    language TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    runtime TEXT NOT NULL,
    exit_code INTEGER,
    runtime_ms INTEGER,
    stdout_preview TEXT,
    stderr_preview TEXT,
    network_enabled INTEGER DEFAULT 0,
    memory_limit_mb INTEGER,
    timeout_seconds INTEGER,
    container_id TEXT,
    artifact_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'completed' CHECK(status IN ('running','completed','failed','timeout')),
    actor TEXT,
    project_id TEXT,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Phase 72 — ICDEV™ Studio
CREATE TABLE IF NOT EXISTS studio_workflows (
    workflow_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    template_yaml TEXT NOT NULL,
    category TEXT,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    version INTEGER DEFAULT 1,
    shared INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS studio_forms (
    form_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    schema_json TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft','published','archived'))
);
CREATE TABLE IF NOT EXISTS studio_form_submissions (
    submission_id TEXT PRIMARY KEY,
    form_id TEXT NOT NULL REFERENCES studio_forms(form_id),
    data_json TEXT NOT NULL,
    submitted_by TEXT,
    submitted_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS studio_case_types (
    type_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lifecycle_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS studio_cases (
    case_id TEXT PRIMARY KEY,
    type_id TEXT NOT NULL REFERENCES studio_case_types(type_id),
    title TEXT NOT NULL,
    description TEXT,
    current_state TEXT NOT NULL,
    priority TEXT DEFAULT 'medium' CHECK(priority IN ('critical','high','medium','low')),
    assigned_to TEXT,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    due_date TEXT,
    form_submission_id TEXT REFERENCES studio_form_submissions(submission_id)
);
CREATE TABLE IF NOT EXISTS studio_case_history (
    history_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES studio_cases(case_id),
    from_state TEXT,
    to_state TEXT NOT NULL,
    changed_by TEXT,
    changed_at TEXT DEFAULT (datetime('now')),
    comment TEXT
);
CREATE TABLE IF NOT EXISTS studio_automations (
    automation_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    trigger_json TEXT NOT NULL,
    condition_json TEXT,
    action_json TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS studio_automation_runs (
    run_id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL REFERENCES studio_automations(automation_id),
    trigger_event TEXT,
    status TEXT CHECK(status IN ('triggered','running','success','failed','skipped')),
    result_json TEXT,
    started_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS studio_dashboards (
    dashboard_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    layout_json TEXT NOT NULL,
    role_default TEXT,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    shared INTEGER DEFAULT 0
);

-- Supply Chain / CVE Triage / Passive Watcher (Phase task-f8a39b1e48, NIST SI-4, CA-7)
CREATE TABLE IF NOT EXISTS supply_chain_vendors (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    vendor_type TEXT CHECK(vendor_type IN ('cots', 'gots', 'oss', 'saas', 'paas', 'iaas', 'contractor', 'subcontractor')),
    country_of_origin TEXT,
    scrm_risk_tier TEXT CHECK(scrm_risk_tier IN ('low', 'moderate', 'high', 'critical')),
    section_889_status TEXT CHECK(section_889_status IN ('compliant', 'under_review', 'prohibited', 'exempt')),
    dod_approved INTEGER DEFAULT 0,
    contact_info TEXT,
    isa_required INTEGER DEFAULT 0,
    last_assessed TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, vendor_name)
);
CREATE TABLE IF NOT EXISTS supply_chain_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    source_type TEXT NOT NULL
        CHECK(source_type IN ('project', 'system', 'component', 'vendor', 'package')),
    source_id TEXT NOT NULL,
    target_type TEXT NOT NULL
        CHECK(target_type IN ('project', 'system', 'component', 'vendor', 'package')),
    target_id TEXT NOT NULL,
    dependency_type TEXT NOT NULL
        CHECK(dependency_type IN ('depends_on', 'supplies', 'integrates_with',
            'data_flows_to', 'inherits_ato', 'shares_boundary')),
    criticality TEXT DEFAULT 'medium'
        CHECK(criticality IN ('critical', 'high', 'medium', 'low')),
    isa_id TEXT,
    metadata TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, source_type, source_id, target_type, target_id, dependency_type)
);
CREATE TABLE IF NOT EXISTS isa_agreements (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    agreement_type TEXT NOT NULL CHECK(agreement_type IN ('isa', 'mou', 'moa', 'sla', 'ila')),
    partner_system TEXT NOT NULL,
    partner_org TEXT,
    status TEXT DEFAULT 'draft'
        CHECK(status IN ('draft', 'review', 'signed', 'active', 'expiring', 'expired', 'terminated')),
    signed_date TEXT,
    expiry_date TEXT,
    data_types_shared TEXT,
    ports_protocols TEXT,
    security_controls TEXT,
    poc_name TEXT,
    poc_email TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cve_triage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    cve_id TEXT NOT NULL,
    package_name TEXT NOT NULL,
    package_version TEXT,
    severity TEXT CHECK(severity IN ('critical', 'high', 'medium', 'low')),
    cvss_score REAL,
    exploitability TEXT CHECK(exploitability IN ('active', 'poc', 'theoretical', 'none_known')),
    triage_decision TEXT CHECK(triage_decision IN ('remediate', 'mitigate', 'accept_risk', 'defer', 'false_positive', 'not_applicable')),
    triage_rationale TEXT,
    upstream_impact TEXT,
    downstream_impact TEXT,
    sla_deadline TEXT,
    triaged_by TEXT,
    triaged_at TEXT DEFAULT (datetime('now')),
    remediated_at TEXT,
    UNIQUE(project_id, cve_id, package_name)
);
CREATE TABLE IF NOT EXISTS cve_passive_watch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_trail_id INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    cve_id TEXT NOT NULL,
    component TEXT,
    triage_id INTEGER,
    skipped INTEGER DEFAULT 0,
    source_event_type TEXT,
    processed_at TEXT DEFAULT (datetime('now'))
);
"""

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------
SEED_TENANT_ID = "tenant-test-001"
SEED_USER_ID = "user-test-001"
SEED_API_KEY_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SEED_API_KEY_PREFIX = "icdev_test"
SEED_PROJECT_ID = "proj-test-001"


def _seed_platform_db(conn):
    """Insert minimal seed data into platform DB."""
    conn.execute(
        "INSERT OR IGNORE INTO tenants (id, name, slug, tier, impact_level, status) VALUES (?, ?, ?, ?, ?, ?)",
        (SEED_TENANT_ID, "Test Org", "test-org", "professional", "IL4", "active"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO users (id, tenant_id, email, role, display_name) VALUES (?, ?, ?, ?, ?)",
        (SEED_USER_ID, SEED_TENANT_ID, "dev@test.gov", "admin", "Test Admin"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO api_keys (id, tenant_id, user_id, name, key_hash, key_prefix, scopes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("key-test-001", SEED_TENANT_ID, SEED_USER_ID, "test-key", SEED_API_KEY_HASH, SEED_API_KEY_PREFIX, '["*"]'),
    )
    conn.execute(
        "INSERT OR IGNORE INTO subscriptions (id, tenant_id, tier, status) VALUES (?, ?, ?, ?)",
        ("sub-test-001", SEED_TENANT_ID, "professional", "active"),
    )
    conn.commit()


def _seed_icdev_db(conn):
    """Insert minimal seed data into ICDEV™ DB."""
    conn.execute(
        "INSERT OR IGNORE INTO projects (id, name, type, classification, status, directory_path, impact_level) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (SEED_PROJECT_ID, "Test Project", "webapp", "CUI", "active", "/tmp/test", "IL5"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO agents (id, name, url, status) VALUES (?, ?, ?, ?)",
        ("builder-agent", "Builder", "http://localhost:8445", "active"),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def icdev_db(tmp_path):
    """Temporary ICDEV™ database with minimal schema and seed data."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    _seed_icdev_db(conn)
    conn.close()
    return db_path


@pytest.fixture
def platform_db(tmp_path):
    """Temporary platform database with minimal schema and seed data."""
    db_path = tmp_path / "platform.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(MINIMAL_PLATFORM_SCHEMA)
    _seed_platform_db(conn)
    conn.close()
    return db_path


@pytest.fixture
def api_gateway_app(platform_db, icdev_db):
    """Flask test app for the SaaS API gateway with mocked auth."""
    os.environ["PLATFORM_DB_PATH"] = str(platform_db)

    from icdev.tools.saas.api_gateway import create_app

    app = create_app(config={"TESTING": True})

    yield app

    os.environ.pop("PLATFORM_DB_PATH", None)


@pytest.fixture
def api_client(api_gateway_app):
    """Flask test client for the API gateway."""
    return api_gateway_app.test_client()


@pytest.fixture
def dashboard_app(icdev_db):
    """Dashboard Flask test app with patched DB path."""
    with patch("icdev.tools.dashboard.app.DB_PATH", str(icdev_db)):
        from icdev.tools.dashboard.app import create_app

        app = create_app()
        app.config["TESTING"] = True
        yield app


@pytest.fixture
def dashboard_client(dashboard_app):
    """Dashboard test client."""
    return dashboard_app.test_client()


@pytest.fixture
def auth_headers():
    """Default Bearer token headers for authenticated requests."""
    return {
        "Authorization": "Bearer icdev_test_key_for_testing",
        "Content-Type": "application/json",
    }


@pytest.fixture
def admin_headers():
    """Admin-level auth headers."""
    return {
        "Authorization": "Bearer icdev_admin_test_key",
        "Content-Type": "application/json",
        "X-Tenant-ID": SEED_TENANT_ID,
    }


# ---------------------------------------------------------------------------
# Phase 4 fixtures — compliance_db, llm_config, rate_limiter_backend
# ---------------------------------------------------------------------------
@pytest.fixture
def compliance_db(tmp_path):
    """ICDEV™ database seeded with compliance data for testing.

    Seeds nist_controls with AC-2, AC-3, SC-7, SI-4 in mixed statuses
    and STIG findings at various severity levels.
    """
    db_path = tmp_path / "compliance.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    _seed_icdev_db(conn)

    # Seed NIST controls in various statuses
    controls = [
        ("ctrl-ac2", "AC-2", SEED_PROJECT_ID, "Account Management", "satisfied", "implemented"),
        ("ctrl-ac3", "AC-3", SEED_PROJECT_ID, "Access Enforcement", "partially_satisfied", "partial"),
        ("ctrl-sc7", "SC-7", SEED_PROJECT_ID, "Boundary Protection", "not_satisfied", "planned"),
        ("ctrl-si4", "SI-4", SEED_PROJECT_ID, "Information System Monitoring", "satisfied", "implemented"),
        ("ctrl-au2", "AU-2", SEED_PROJECT_ID, "Audit Events", "not_assessed", None),
        ("ctrl-ia2", "IA-2", SEED_PROJECT_ID, "Identification and Authentication", "satisfied", "implemented"),
    ]
    for c in controls:
        conn.execute(
            "INSERT OR IGNORE INTO nist_controls (id, control_id, project_id, title, status, implementation_status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            c,
        )

    # Seed STIG findings
    findings = [
        ("stig-001", SEED_PROJECT_ID, "SV-230221r1", "high", "open", "CAT-I: Disable root login"),
        ("stig-002", SEED_PROJECT_ID, "SV-230222r1", "medium", "open", "CAT-II: Set password complexity"),
        ("stig-003", SEED_PROJECT_ID, "SV-230223r1", "medium", "closed", "CAT-II: Enable audit logging"),
        ("stig-004", SEED_PROJECT_ID, "SV-230224r1", "low", "open", "CAT-III: Set login banner"),
    ]
    for f in findings:
        conn.execute(
            "INSERT OR IGNORE INTO stig_findings (id, project_id, rule_id, severity, status, title) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            f,
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def llm_config(tmp_path):
    """Write a mock llm_config.yaml to tmp_path for LLM router tests."""
    import yaml

    config = {
        "default_provider": "mock-openai",
        "providers": {
            "mock-openai": {
                "type": "openai_compat",
                "base_url": "http://localhost:11434/v1",
                "models": {
                    "mock-model": {
                        "model_id": "mock-model-v1",
                        "capabilities": ["text_generation", "code_generation"],
                        "max_tokens": 4096,
                    }
                },
            }
        },
        "routing": {
            "code_generation": {
                "provider": "mock-openai",
                "model": "mock-model",
                "fallback_chain": [],
            },
            "task_decomposition": {
                "provider": "mock-openai",
                "model": "mock-model",
                "fallback_chain": [],
            },
            "collaboration": {
                "provider": "mock-openai",
                "model": "mock-model",
                "fallback_chain": [],
            },
            "narrative_generation": {
                "provider": "mock-openai",
                "model": "mock-model",
                "fallback_chain": [],
            },
            "compliance_export": {
                "provider": "mock-openai",
                "model": "mock-model",
                "fallback_chain": [],
            },
        },
        "agent_effort_defaults": {
            "orchestrator-agent": "high",
            "builder-agent": "max",
        },
    }

    config_path = tmp_path / "llm_config.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


@pytest.fixture
def rate_limiter_backend():
    """Fresh in-memory rate limiter backend for testing."""
    try:
        from icdev.tools.saas.rate_limiter import InMemoryBackend

        return InMemoryBackend()
    except ImportError:
        pytest.skip("rate_limiter module not available")
