#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 017: auto-generate CREATE TABLE for orphan tables.

Generated 2026-04-12 by tools/awareness/orphan_table_generator.py.
Updated 2026-04-21: added 'evidence' table (tools/boundary_canvas/twin.py).

Resolves orphan_db_table findings from the Internal Awareness Engine
gap detector. These tables are written by tool code (INSERT INTO / SELECT)
but had no CREATE TABLE statement in any migration, so fresh sqlite
deployments would 500 on first use.

This migration covers 58 distinct orphan tables.
All CREATE TABLE statements are idempotent (IF NOT EXISTS).

REVIEW GATE: Human review required before applying. Run:
    python tools/awareness/orphan_table_generator.py --stats --json
    python tools/db/migrate.py --up --dry-run
"""

import sqlite3

MIGRATION_ID = "017"
MIGRATION_NAME = "orphan_tables"
DESCRIPTION = (
    "Create 58 orphan tables identified by the Internal Awareness Engine "
    "gap detector. Resolves schema drift — tables referenced by tool code but missing "
    "from any migration, causing fresh sqlite deploys to 500 on first use."
)

_ORPHAN_TABLES = [
    "ad_news_catalysts",
"agent_chat_turns",
    "agent_registry",
    "ai_model_cards",
    "app_blueprints",
    "ato_packages",
    "autoresearch_experiments",
    "awareness_component_health",
    "boundary_assessments",
    "build_items",
    "code_quality_findings",
    "compliance_artifacts",
    "contact_submissions",
    "control_mappings",
    "cpmp_compliance_items",
    "creative_gaps",
    "crosswalk_results",
    "decision_records",
    "deploy_history",
    "dh_enrichment_cache",
    "evidence",
    "ft_training_pairs",
    "genesis_knowledge_packets",
    "genesis_runs",
    "gitlab_pipeline_runs",
    "harness_trace_recommendations",
    "intake_gaps",
    "legacy_apps",
    "mkt_feedback",
    "mkt_licenses",
    "nc_governance_reviews",
    "ndc_audit",
    "ndc_designs",
    "notifications",
    "page_agent_routes",
    "pg_contract_vehicles",
    "pg_cost_line_items",
    "pg_extension",
    "pg_proposal_knowledge_base",
    "pg_stat_user_indexes",
    "pg_stat_user_tables",
    "pg_training_pair_sources",
    "posts",
    "project_team_members",
    "proposal_compliance_items",
    "rag_evaluations",
    "research_cache",
    "risk_monitor_history",
    "schedule_log",
    "security_findings",
    "security_scan_results",
    "self_healing_patterns",
    "ssp_controls",
    "test_results",
    "topic_clusters",
    "tour_config",
    "vulnerability_records",
    "workflow_tasks"
]

_CREATE_STMTS = [
    # ad_news_catalysts — first referenced in tools/trading/analysis/confluence_pillars/event_stack.py [known_schema]
    "CREATE TABLE IF NOT EXISTS ad_news_catalysts (\n    id TEXT PRIMARY KEY,\n    ticker TEXT NOT NULL,\n    headline TEXT DEFAULT '',\n    source TEXT DEFAULT '',\n    sentiment TEXT DEFAULT '',\n    url TEXT DEFAULT '',\n    created_at TEXT\n);",
    # agent_chat_turns — first referenced in tools/gateway/adapters/internal.py [known_schema]
    "CREATE TABLE IF NOT EXISTS agent_chat_turns (\n    id TEXT PRIMARY KEY,\n    session_id TEXT NOT NULL,\n    role TEXT DEFAULT 'agent',\n    content TEXT DEFAULT '',\n    created_at TEXT\n);",
    # agent_registry — first referenced in tools/agent/a2a_discovery_server.py [known_schema]
    "CREATE TABLE IF NOT EXISTS agent_registry (\n    id TEXT PRIMARY KEY,\n    name TEXT DEFAULT '',\n    url TEXT DEFAULT '',\n    status TEXT DEFAULT 'unknown',\n    last_heartbeat TEXT,\n    created_at TEXT\n);",
    # ai_model_cards — first referenced in tools/requirements/ai_governance_scorer.py [known_schema]
    "CREATE TABLE IF NOT EXISTS ai_model_cards (\n    id TEXT PRIMARY KEY,\n    model_name TEXT DEFAULT '',\n    provider TEXT DEFAULT '',\n    version TEXT DEFAULT '',\n    description TEXT DEFAULT '',\n    status TEXT DEFAULT 'active',\n    created_at TEXT\n);",
    # app_blueprints — first referenced in tools/builder/app_blueprint.py [known_schema]
    "CREATE TABLE IF NOT EXISTS app_blueprints (\n    id TEXT PRIMARY KEY,\n    app_name TEXT DEFAULT '',\n    classification TEXT DEFAULT 'public',\n    impact_level TEXT DEFAULT 'IL4',\n    capabilities TEXT DEFAULT '[]',\n    agents TEXT DEFAULT '[]',\n    cloud_provider TEXT DEFAULT 'aws',\n    blueprint_hash TEXT DEFAULT '',\n    generated_at TEXT,\n    full_blueprint TEXT DEFAULT '{}'\n);",
    # ato_packages — first referenced in tools/dashboard/app.py [known_schema]
    "CREATE TABLE IF NOT EXISTS ato_packages (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    authorization_status TEXT DEFAULT 'pending',\n    impact_level TEXT DEFAULT 'IL4',\n    created_at TEXT,\n    updated_at TEXT\n);",
    # autoresearch_experiments — first referenced in tools/govcon/shipley_mapper.py [known_schema]
    "CREATE TABLE IF NOT EXISTS autoresearch_experiments (\n    id TEXT PRIMARY KEY,\n    name TEXT DEFAULT '',\n    status TEXT DEFAULT 'pending',\n    result TEXT DEFAULT '{}',\n    created_at TEXT\n);",
    # awareness_component_health — first referenced in tools/awareness/drift_detector.py [known_schema]
    "CREATE TABLE IF NOT EXISTS awareness_component_health (\n    id TEXT PRIMARY KEY,\n    node_id TEXT NOT NULL,\n    probe_type TEXT NOT NULL,\n    status TEXT DEFAULT 'unknown',\n    detail TEXT DEFAULT '{}',\n    probed_at TEXT\n);",
    # boundary_assessments — first referenced in tools/extensions/builtins/080_intake_enrichment_chat.py [known_schema]
    "CREATE TABLE IF NOT EXISTS boundary_assessments (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    status TEXT DEFAULT 'pending',\n    findings TEXT DEFAULT '[]',\n    created_at TEXT\n);",
    # build_items — first referenced in tools/dashboard/api/intake.py [known_schema]
    "CREATE TABLE IF NOT EXISTS build_items (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    session_id TEXT DEFAULT '',\n    item_type TEXT DEFAULT '',\n    name TEXT DEFAULT '',\n    status TEXT DEFAULT 'pending',\n    content TEXT DEFAULT '{}',\n    created_at TEXT\n);",
    # code_quality_findings — first referenced in tools/simulation/simulation_engine.py [known_schema]
    "CREATE TABLE IF NOT EXISTS code_quality_findings (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    rule_id TEXT DEFAULT '',\n    severity TEXT DEFAULT 'low',\n    message TEXT DEFAULT '',\n    file_path TEXT DEFAULT '',\n    created_at TEXT\n);",
    # compliance_artifacts — first referenced in tools/testing/goveval.py [known_schema]
    "CREATE TABLE IF NOT EXISTS compliance_artifacts (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    artifact_type TEXT DEFAULT '',\n    content TEXT DEFAULT '',\n    classification TEXT DEFAULT 'public',\n    created_at TEXT,\n    updated_at TEXT\n);",
    # contact_submissions — first referenced in tools/dashboard/app.py [known_schema]
    "CREATE TABLE IF NOT EXISTS contact_submissions (\n    id TEXT PRIMARY KEY,\n    name TEXT DEFAULT '',\n    email TEXT DEFAULT '',\n    organization TEXT DEFAULT '',\n    role TEXT DEFAULT '',\n    interest TEXT DEFAULT '',\n    message TEXT DEFAULT '',\n    status TEXT DEFAULT 'new',\n    notes TEXT DEFAULT '',\n    created_at TEXT,\n    updated_at TEXT\n);",
    # control_mappings — first referenced in tools/testing/goveval.py [known_schema]
    "CREATE TABLE IF NOT EXISTS control_mappings (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    control_id TEXT DEFAULT '',\n    status TEXT DEFAULT 'not_implemented',\n    evidence TEXT DEFAULT '',\n    created_at TEXT\n);",
    # cpmp_compliance_items — first referenced in tools/simulation/risk_monitor.py [known_schema]
    "CREATE TABLE IF NOT EXISTS cpmp_compliance_items (\n    id TEXT PRIMARY KEY,\n    contract_id TEXT DEFAULT '',\n    control_id TEXT DEFAULT '',\n    status TEXT DEFAULT 'not_assessed',\n    notes TEXT DEFAULT '',\n    created_at TEXT\n);",
    # creative_gaps — first referenced in tools/appforge/reflexes/discover.py [known_schema]
    "CREATE TABLE IF NOT EXISTS creative_gaps (\n    id TEXT PRIMARY KEY,\n    gap_type TEXT DEFAULT '',\n    description TEXT DEFAULT '',\n    priority TEXT DEFAULT 'medium',\n    status TEXT DEFAULT 'open',\n    created_at TEXT\n);",
    # crosswalk_results — first referenced in tools/testing/goveval.py [known_schema]
    "CREATE TABLE IF NOT EXISTS crosswalk_results (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    source_framework TEXT DEFAULT '',\n    target_framework TEXT DEFAULT '',\n    mapping TEXT DEFAULT '{}',\n    created_at TEXT\n);",
    # decision_records — first referenced in tools/compliance/xai_assessor.py [known_schema]
    "CREATE TABLE IF NOT EXISTS decision_records (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    title TEXT DEFAULT '',\n    decision TEXT DEFAULT '',\n    rationale TEXT DEFAULT '',\n    status TEXT DEFAULT 'proposed',\n    created_at TEXT\n);",
    # deploy_history — first referenced in tools/dashboard/platform_health.py [known_schema]
    "CREATE TABLE IF NOT EXISTS deploy_history (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    environment TEXT DEFAULT 'staging',\n    status TEXT DEFAULT 'pending',\n    deployed_by TEXT DEFAULT '',\n    deployed_at TEXT,\n    created_at TEXT\n);",
    # dh_enrichment_cache — first referenced in tools/genesis/promoter.py [known_schema]
    "CREATE TABLE IF NOT EXISTS dh_enrichment_cache (\n    id TEXT PRIMARY KEY,\n    source TEXT DEFAULT '',\n    query_hash TEXT DEFAULT '',\n    result_data TEXT DEFAULT '{}',\n    expires_at TEXT,\n    created_at TEXT\n);",
    # evidence — first referenced in tools/boundary_canvas/twin.py [known_schema]
    "CREATE TABLE IF NOT EXISTS evidence (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    control_id TEXT DEFAULT '',\n    evidence_type TEXT DEFAULT '',\n    evidence_source TEXT DEFAULT '',\n    evidence_path TEXT DEFAULT '',\n    collected_at TEXT,\n    status TEXT DEFAULT 'fresh',\n    created_at TEXT\n);",
    # ft_training_pairs — first referenced in tools/genesis/promoter.py [known_schema]
    "CREATE TABLE IF NOT EXISTS ft_training_pairs (\n    id TEXT PRIMARY KEY,\n    dataset_id TEXT DEFAULT '',\n    instruction TEXT DEFAULT '',\n    input_text TEXT DEFAULT '',\n    output_text TEXT DEFAULT '',\n    purpose TEXT DEFAULT 'general',\n    approved INTEGER DEFAULT 0,\n    source TEXT DEFAULT '',\n    created_at TEXT\n);",
    # genesis_knowledge_packets — first referenced in tools/dashboard/app.py [known_schema]
    "CREATE TABLE IF NOT EXISTS genesis_knowledge_packets (\n    id TEXT PRIMARY KEY,\n    app_key TEXT DEFAULT 'icdev',\n    packet_type TEXT DEFAULT '',\n    content TEXT DEFAULT '{}',\n    status TEXT DEFAULT 'pending',\n    created_at TEXT\n);",
    # genesis_runs — first referenced in tools/dashboard/app.py [known_schema]
    "CREATE TABLE IF NOT EXISTS genesis_runs (\n    id TEXT PRIMARY KEY,\n    app_key TEXT DEFAULT 'icdev',\n    status TEXT DEFAULT 'running',\n    started_at TEXT,\n    completed_at TEXT,\n    result TEXT DEFAULT '{}'\n);",
    # gitlab_pipeline_runs — first referenced in tools/dashboard/platform_health.py [known_schema]
    "CREATE TABLE IF NOT EXISTS gitlab_pipeline_runs (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    pipeline_id TEXT DEFAULT '',\n    status TEXT DEFAULT 'pending',\n    branch TEXT DEFAULT 'main',\n    started_at TEXT,\n    finished_at TEXT,\n    created_at TEXT\n);",
    # harness_trace_recommendations — first referenced in tools/genesis/feedback_collector.py [known_schema]
    "CREATE TABLE IF NOT EXISTS harness_trace_recommendations (\n    id TEXT PRIMARY KEY,\n    session_id TEXT DEFAULT '',\n    recommendation_type TEXT DEFAULT '',\n    severity TEXT DEFAULT 'info',\n    message TEXT DEFAULT '',\n    created_at TEXT\n);",
    # intake_gaps — first referenced in tools/extensions/builtins/080_intake_enrichment_chat.py [known_schema]
    "CREATE TABLE IF NOT EXISTS intake_gaps (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    gap_type TEXT DEFAULT '',\n    description TEXT DEFAULT '',\n    severity TEXT DEFAULT 'medium',\n    status TEXT DEFAULT 'open',\n    created_at TEXT\n);",
    # legacy_apps — first referenced in tools/modernization/ui_analyzer.py [known_schema]
    "CREATE TABLE IF NOT EXISTS legacy_apps (\n    id TEXT PRIMARY KEY,\n    name TEXT DEFAULT '',\n    language TEXT DEFAULT '',\n    framework TEXT DEFAULT '',\n    metadata TEXT DEFAULT '{}',\n    status TEXT DEFAULT 'active',\n    created_at TEXT\n);",
    # mkt_feedback — first referenced in tools/genesis/reflexes/market.py [known_schema]
    "CREATE TABLE IF NOT EXISTS mkt_feedback (\n    id TEXT PRIMARY KEY,\n    module_slug TEXT DEFAULT '',\n    rating REAL DEFAULT 0.0,\n    comment TEXT DEFAULT '',\n    user_id TEXT DEFAULT '',\n    created_at TEXT\n);",
    # mkt_licenses — first referenced in tools/genesis/reflexes/market.py [known_schema]
    "CREATE TABLE IF NOT EXISTS mkt_licenses (\n    id TEXT PRIMARY KEY,\n    module_slug TEXT DEFAULT '',\n    tenant_id TEXT DEFAULT '',\n    status TEXT DEFAULT 'active',\n    issued_at TEXT,\n    expires_at TEXT,\n    created_at TEXT\n);",
    # nc_governance_reviews — first referenced in tools/network/blueprint.py [known_schema]
    "CREATE TABLE IF NOT EXISTS nc_governance_reviews (\n    id TEXT PRIMARY KEY,\n    design_id TEXT DEFAULT '',\n    status TEXT DEFAULT 'pending',\n    reviewer TEXT DEFAULT '',\n    notes TEXT DEFAULT '',\n    reviewed_at TEXT,\n    created_at TEXT\n);",
    # ndc_audit — first referenced in tools/network/blueprint_helpers.py [known_schema]
    "CREATE TABLE IF NOT EXISTS ndc_audit (\n    id TEXT PRIMARY KEY,\n    design_id TEXT DEFAULT '',\n    \"user\" TEXT DEFAULT '',\n    action TEXT DEFAULT '',\n    detail TEXT DEFAULT '',\n    classification TEXT DEFAULT 'public',\n    created_at TEXT\n);",
    # ndc_designs — first referenced in tools/network/routes/projects.py [known_schema]
    "CREATE TABLE IF NOT EXISTS ndc_designs (\n    id TEXT PRIMARY KEY,\n    name TEXT DEFAULT '',\n    classification TEXT DEFAULT 'public',\n    status TEXT DEFAULT 'draft',\n    metadata TEXT DEFAULT '{}',\n    created_at TEXT,\n    updated_at TEXT\n);",
    # notifications — first referenced in tools/genesis/reflexes/kanban.py [known_schema]
    "CREATE TABLE IF NOT EXISTS notifications (\n    id TEXT PRIMARY KEY,\n    title TEXT DEFAULT '',\n    message TEXT DEFAULT '',\n    severity TEXT DEFAULT 'info',\n    source TEXT DEFAULT '',\n    read_at TEXT,\n    created_at TEXT\n);",
    # page_agent_routes — first referenced in tools/dashboard/app.py [known_schema]
    "CREATE TABLE IF NOT EXISTS page_agent_routes (\n    id TEXT PRIMARY KEY,\n    keyword TEXT DEFAULT '',\n    route TEXT DEFAULT '',\n    agent_name TEXT DEFAULT '',\n    created_at TEXT\n);",
    # pg_contract_vehicles — first referenced in tools/proposal_genesis/reflexes/vehicle.py [known_schema]
    "CREATE TABLE IF NOT EXISTS pg_contract_vehicles (\n    id TEXT PRIMARY KEY,\n    vehicle_name TEXT DEFAULT '',\n    vehicle_type TEXT DEFAULT '',\n    status TEXT DEFAULT 'active',\n    created_at TEXT\n);",
    # pg_cost_line_items — first referenced in tools/proposal_genesis/reflexes/price.py [known_schema]
    "CREATE TABLE IF NOT EXISTS pg_cost_line_items (\n    id TEXT PRIMARY KEY,\n    cost_volume_id TEXT DEFAULT '',\n    category TEXT DEFAULT '',\n    description TEXT DEFAULT '',\n    amount REAL DEFAULT 0.0,\n    unit TEXT DEFAULT '',\n    quantity REAL DEFAULT 1.0,\n    created_at TEXT\n);",
    # pg_extension — first referenced in tools/rag/pg_vector_store.py [known_schema]
    "CREATE TABLE IF NOT EXISTS pg_extension (\n    name TEXT PRIMARY KEY,\n    default_version TEXT DEFAULT '',\n    installed_version TEXT DEFAULT '',\n    comment TEXT DEFAULT ''\n);",
    # pg_proposal_knowledge_base — first referenced in tools/govcon/capability_enricher.py [known_schema]
    "CREATE TABLE IF NOT EXISTS pg_proposal_knowledge_base (\n    id TEXT PRIMARY KEY,\n    proposal_id TEXT DEFAULT '',\n    content TEXT DEFAULT '',\n    source TEXT DEFAULT '',\n    embedding TEXT DEFAULT '[]',\n    created_at TEXT\n);",
    # pg_stat_user_indexes — first referenced in tools/db/pg_optimize_all.py [known_schema]
    "CREATE TABLE IF NOT EXISTS pg_stat_user_indexes (\n    indexrelid INTEGER PRIMARY KEY,\n    schemaname TEXT DEFAULT '',\n    relname TEXT DEFAULT '',\n    indexrelname TEXT DEFAULT '',\n    idx_scan INTEGER DEFAULT 0,\n    idx_tup_read INTEGER DEFAULT 0,\n    idx_tup_fetch INTEGER DEFAULT 0\n);",
    # pg_stat_user_tables — first referenced in tools/db/pg_optimize_all.py [known_schema]
    "CREATE TABLE IF NOT EXISTS pg_stat_user_tables (\n    relid INTEGER PRIMARY KEY,\n    schemaname TEXT DEFAULT '',\n    relname TEXT DEFAULT '',\n    seq_scan INTEGER DEFAULT 0,\n    seq_tup_read INTEGER DEFAULT 0,\n    n_live_tup INTEGER DEFAULT 0,\n    n_dead_tup INTEGER DEFAULT 0,\n    last_vacuum TEXT,\n    last_analyze TEXT\n);",
    # pg_training_pair_sources — first referenced in tools/proposal_genesis/reflexes/train.py [known_schema]
    "CREATE TABLE IF NOT EXISTS pg_training_pair_sources (\n    id TEXT PRIMARY KEY,\n    source_type TEXT NOT NULL,\n    source_id TEXT NOT NULL,\n    pair_count INTEGER NOT NULL DEFAULT 0,\n    content_hash TEXT NOT NULL,\n    created_at TEXT\n);",
    # posts — first referenced in tools/pulse/cli.py [known_schema]
    "CREATE TABLE IF NOT EXISTS posts (\n    id TEXT PRIMARY KEY,\n    title TEXT DEFAULT '',\n    slug TEXT DEFAULT '',\n    body TEXT DEFAULT '',\n    status TEXT DEFAULT 'draft',\n    published_at TEXT,\n    created_at TEXT,\n    updated_at TEXT\n);",
    # project_team_members — first referenced in tools/simulation/risk_monitor.py [known_schema]
    "CREATE TABLE IF NOT EXISTS project_team_members (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    name TEXT DEFAULT '',\n    role TEXT DEFAULT '',\n    email TEXT DEFAULT '',\n    status TEXT DEFAULT 'active',\n    created_at TEXT\n);",
    # proposal_compliance_items — first referenced in tools/govcon/compliance_populator.py [known_schema]
    "CREATE TABLE IF NOT EXISTS proposal_compliance_items (\n    id TEXT PRIMARY KEY,\n    section_id TEXT DEFAULT '',\n    requirement_id TEXT DEFAULT '',\n    requirement_text TEXT DEFAULT '',\n    compliance_status TEXT DEFAULT 'not_started',\n    compliance_notes TEXT DEFAULT '',\n    evidence_reference TEXT DEFAULT '',\n    created_at TEXT,\n    updated_at TEXT\n);",
    # rag_evaluations — first referenced in tools/finetune/quality_monitor.py [known_schema]
    "CREATE TABLE IF NOT EXISTS rag_evaluations (\n    id TEXT PRIMARY KEY,\n    query_id TEXT DEFAULT '',\n    ndcg_score REAL DEFAULT 0.0,\n    mrr_score REAL DEFAULT 0.0,\n    precision_at_k REAL DEFAULT 0.0,\n    created_at TEXT\n);",
    # research_cache — first referenced in tools/pulse/cli.py [known_schema]
    "CREATE TABLE IF NOT EXISTS research_cache (\n    id TEXT PRIMARY KEY,\n    query_hash TEXT DEFAULT '',\n    query TEXT DEFAULT '',\n    result TEXT DEFAULT '{}',\n    expires_at TEXT,\n    created_at TEXT\n);",
    # risk_monitor_history — first referenced in tools/simulation/risk_monitor.py [known_schema]
    "CREATE TABLE IF NOT EXISTS risk_monitor_history (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    risk_type TEXT DEFAULT '',\n    risk_score REAL DEFAULT 0.0,\n    severity TEXT DEFAULT 'low',\n    components TEXT DEFAULT '[]',\n    alerts TEXT DEFAULT '[]',\n    calculated_at TEXT\n);",
    # schedule_log — first referenced in tools/pulse/cli.py [known_schema]
    "CREATE TABLE IF NOT EXISTS schedule_log (\n    id TEXT PRIMARY KEY,\n    job_name TEXT DEFAULT '',\n    status TEXT DEFAULT 'pending',\n    message TEXT DEFAULT '',\n    ran_at TEXT,\n    created_at TEXT\n);",
    # security_findings — first referenced in tools/workflow/next_action.py [known_schema]
    "CREATE TABLE IF NOT EXISTS security_findings (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    severity TEXT DEFAULT 'low',\n    status TEXT DEFAULT 'open',\n    title TEXT DEFAULT '',\n    description TEXT DEFAULT '',\n    rule_id TEXT DEFAULT '',\n    file_path TEXT DEFAULT '',\n    created_at TEXT\n);",
    # security_scan_results — first referenced in tools/dashboard/platform_health.py [known_schema]
    "CREATE TABLE IF NOT EXISTS security_scan_results (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    scanner TEXT DEFAULT '',\n    severity TEXT DEFAULT 'low',\n    status TEXT DEFAULT 'open',\n    result TEXT DEFAULT '{}',\n    created_at TEXT\n);",
    # self_healing_patterns — first referenced in tools/genesis/reflexes/heal.py [known_schema]
    "CREATE TABLE IF NOT EXISTS self_healing_patterns (\n    id TEXT PRIMARY KEY,\n    pattern_name TEXT DEFAULT '',\n    error_pattern TEXT DEFAULT '',\n    resolution_type TEXT DEFAULT '',\n    resolution_action TEXT DEFAULT '',\n    confidence REAL DEFAULT 0.0,\n    success_count INTEGER DEFAULT 0,\n    failure_count INTEGER DEFAULT 0,\n    created_at TEXT,\n    updated_at TEXT\n);",
    # ssp_controls — first referenced in tools/dashboard/app.py [known_schema]
    "CREATE TABLE IF NOT EXISTS ssp_controls (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    control_id TEXT DEFAULT '',\n    status TEXT DEFAULT 'not_implemented',\n    implementation TEXT DEFAULT '',\n    created_at TEXT,\n    updated_at TEXT\n);",
    # test_results — first referenced in tools/simulation/simulation_engine.py [known_schema]
    "CREATE TABLE IF NOT EXISTS test_results (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    test_type TEXT DEFAULT 'unit',\n    status TEXT DEFAULT 'pending',\n    passed INTEGER DEFAULT 0,\n    failed INTEGER DEFAULT 0,\n    coverage REAL DEFAULT 0.0,\n    ran_at TEXT,\n    created_at TEXT\n);",
    # topic_clusters — first referenced in tools/pulse/cli.py [known_schema]
    "CREATE TABLE IF NOT EXISTS topic_clusters (\n    id TEXT PRIMARY KEY,\n    topic TEXT DEFAULT '',\n    cluster_label TEXT DEFAULT '',\n    keywords TEXT DEFAULT '[]',\n    post_count INTEGER DEFAULT 0,\n    created_at TEXT\n);",
    # tour_config — first referenced in tools/dashboard/app.py [known_schema]
    "CREATE TABLE IF NOT EXISTS tour_config (\n    id TEXT PRIMARY KEY,\n    selector TEXT DEFAULT '',\n    title TEXT DEFAULT '',\n    description TEXT DEFAULT '',\n    step_order INTEGER DEFAULT 0,\n    created_at TEXT\n);",
    # vulnerability_records — first referenced in tools/compliance/slsa_attestation_generator.py [known_schema]
    "CREATE TABLE IF NOT EXISTS vulnerability_records (\n    id TEXT PRIMARY KEY,\n    project_id TEXT DEFAULT '',\n    cve_id TEXT DEFAULT '',\n    severity TEXT DEFAULT 'low',\n    status TEXT DEFAULT 'not_affected',\n    description TEXT DEFAULT '',\n    affected_component TEXT DEFAULT '',\n    created_at TEXT,\n    updated_at TEXT\n);",
    # workflow_tasks — first referenced in tools/workflow/reconciler.py [known_schema]
    "CREATE TABLE IF NOT EXISTS workflow_tasks (\n    id TEXT PRIMARY KEY,\n    loop_id TEXT DEFAULT '',\n    task_type TEXT DEFAULT '',\n    status TEXT DEFAULT 'pending',\n    payload TEXT DEFAULT '{}',\n    result TEXT DEFAULT '{}',\n    created_at TEXT,\n    updated_at TEXT\n);",
]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def up(conn: sqlite3.Connection) -> dict:
    """Apply migration: create all orphan tables (idempotent)."""
    created = 0
    skipped = 0
    errors = []

    for table, stmt in zip(_ORPHAN_TABLES, _CREATE_STMTS):
        try:
            conn.execute(stmt)
            # Track whether we actually created it or it existed
            created += 1
        except sqlite3.OperationalError as exc:
            errors.append(f"{table}: {exc}")

    conn.commit()
    return {
        "status": "applied" if not errors else "partial",
        "created_or_verified": created,
        "skipped": skipped,
        "errors": errors,
        "total": len(_ORPHAN_TABLES),
    }
