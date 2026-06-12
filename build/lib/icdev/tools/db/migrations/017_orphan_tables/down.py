#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 017 rollback: drop orphan tables created by up.py.

WARNING: This is destructive — only run in development environments.
"""

import sqlite3

MIGRATION_ID = "017"
MIGRATION_NAME = "orphan_tables"

_ORPHAN_TABLES = [
    "agent_chat_turns",
    "agent_registry",
    "ai_model_cards",
    "app_blueprints",
    "ato_packages",
    "autoresearch_experiments",
    "awareness_component_health",
    "boundary_assessments",
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


def down(conn: sqlite3.Connection) -> dict:
    """Drop all tables created by migration 017."""
    dropped = 0
    skipped = 0
    for table in _ORPHAN_TABLES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            skipped += 1
            continue
        conn.execute(f"DROP TABLE IF EXISTS {table}")
        dropped += 1
    conn.commit()
    return {"status": "applied", "dropped": dropped, "skipped": skipped}
