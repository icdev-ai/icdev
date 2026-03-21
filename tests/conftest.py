#!/usr/bin/env python3
# CUI // SP-CTI
"""Shared pytest fixtures for ICDEV test suite.

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
# Minimal ICDEV schema (subset for fast test DB creation)
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
        "INSERT OR IGNORE INTO tenants (id, name, slug, tier, impact_level, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (SEED_TENANT_ID, "Test Org", "test-org", "professional", "IL4", "active"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO users (id, tenant_id, email, role, display_name) "
        "VALUES (?, ?, ?, ?, ?)",
        (SEED_USER_ID, SEED_TENANT_ID, "dev@test.gov", "admin", "Test Admin"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO api_keys (id, tenant_id, user_id, name, key_hash, key_prefix, scopes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("key-test-001", SEED_TENANT_ID, SEED_USER_ID, "test-key",
         SEED_API_KEY_HASH, SEED_API_KEY_PREFIX, '["*"]'),
    )
    conn.execute(
        "INSERT OR IGNORE INTO subscriptions (id, tenant_id, tier, status) "
        "VALUES (?, ?, ?, ?)",
        ("sub-test-001", SEED_TENANT_ID, "professional", "active"),
    )
    conn.commit()


def _seed_icdev_db(conn):
    """Insert minimal seed data into ICDEV DB."""
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
    """Temporary ICDEV database with minimal schema and seed data."""
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
    """ICDEV database seeded with compliance data for testing.

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
