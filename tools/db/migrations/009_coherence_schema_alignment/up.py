#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 009: Schema alignment for 78 missing columns across 25 tables.

Detected by Coherence Engine (D-WF-8). These columns existed in Python INSERT
statements but were missing from CREATE TABLE definitions. The columns were
added to init_icdev_db.py for fresh installs; this migration adds them to
existing databases.

Safe to re-run: uses try/except to skip columns that already exist.
"""

MIGRATION_ID = "009"
MIGRATION_NAME = "coherence_schema_alignment"
DESCRIPTION = "Add 78 missing columns across 25 tables (coherence checker D-WF-8)"

# (table, column, type_with_default)
_COLUMNS = [
    ("compliance_detection_log", "data_categories", "TEXT"),
    ("compliance_detection_log", "detected_frameworks", "TEXT"),
    ("compliance_detection_log", "recommended_frameworks", "TEXT"),
    ("compliance_detection_log", "required_frameworks", "TEXT"),
    ("compliance_detection_log", "rules_matched", "TEXT"),
    ("innovation_signals", "body", "TEXT"),
    ("innovation_signals", "composite_score", "REAL"),
    ("innovation_signals", "created_at", "TEXT"),
    ("innovation_signals", "implementation_status", "TEXT"),
    ("code_quality_metrics", "avg_cognitive", "REAL"),
    ("code_quality_metrics", "avg_cyclomatic", "REAL"),
    ("code_quality_metrics", "avg_loc", "REAL"),
    ("code_quality_metrics", "avg_nesting", "REAL"),
    ("code_quality_metrics", "avg_params", "REAL"),
    ("code_quality_metrics", "smells", "TEXT"),
    ("code_quality_metrics", "total_functions", "INTEGER"),
    ("knowledge_patterns", "source", "TEXT"),
    ("knowledge_patterns", "resolution", "TEXT"),
    ("knowledge_patterns", "detection_rule", "TEXT"),
    ("knowledge_patterns", "name", "TEXT"),
    ("knowledge_patterns", "solution", "TEXT"),
    ("self_healing_events", "status", "TEXT"),
    ("icd_documents", "approval_status", "TEXT"),
    ("icd_documents", "updated_at", "TEXT"),
    ("tsp_documents", "approval_status", "TEXT"),
    ("tsp_documents", "updated_at", "TEXT"),
    ("dev_profiles", "dimensions", "TEXT"),
    ("dev_profiles", "template", "TEXT"),
    ("propagation_log", "classification", "TEXT DEFAULT 'CUI'"),
    ("propagation_log", "genome_version", "TEXT"),
    ("propagation_log", "initiated_at", "TEXT"),
    ("propagation_log", "initiated_by", "TEXT"),
    ("propagation_log", "propagation_status", "TEXT"),
    ("propagation_log", "source_child_id", "TEXT"),
    ("propagation_log", "target_child_id", "TEXT"),
    ("child_app_registry", "blueprint_json", "TEXT"),
    ("child_app_registry", "child_type", "TEXT"),
    ("child_app_registry", "compliance_required", "INTEGER DEFAULT 0"),
    ("child_app_registry", "project_path", "TEXT"),
    ("child_app_registry", "status", "TEXT"),
    ("child_app_registry", "target_cloud", "TEXT"),
    ("child_telemetry", "metric_data", "TEXT"),
    ("child_telemetry", "metric_type", "TEXT"),
    ("child_telemetry", "classification", "TEXT DEFAULT 'CUI'"),
    ("child_telemetry", "endpoint_url", "TEXT"),
    ("child_telemetry", "raw_response", "TEXT"),
    ("child_telemetry", "response_time_ms", "REAL"),
    ("child_learned_behaviors", "classification", "TEXT DEFAULT 'CUI'"),
    ("ai_bom", "classification", "TEXT DEFAULT 'CUI'"),
    ("ai_bom", "component_name", "TEXT"),
    ("ai_bom", "component_type", "TEXT"),
    ("ai_bom", "license", "TEXT"),
    ("ai_bom", "risk_level", "TEXT"),
    ("ai_bom", "updated_at", "TEXT"),
    ("ai_telemetry", "api_key_source", "TEXT"),
    ("ai_telemetry", "cost_usd", "REAL"),
    ("ai_telemetry", "injection_scan_result", "TEXT"),
    ("ai_telemetry", "logged_at", "TEXT"),
    ("ai_telemetry", "thinking_tokens", "INTEGER"),
    ("atlas_red_team_results", "classification", "TEXT DEFAULT 'CUI'"),
    ("atlas_red_team_results", "findings_json", "TEXT"),
    ("atlas_red_team_results", "passed", "INTEGER"),
    ("atlas_red_team_results", "scanned_at", "TEXT"),
    ("atlas_red_team_results", "technique", "TEXT"),
    ("atlas_red_team_results", "tests_passed", "INTEGER"),
    ("atlas_red_team_results", "tests_run", "INTEGER"),
    ("prompt_injection_log", "classification", "TEXT DEFAULT 'CUI'"),
    ("prompt_injection_log", "finding_count", "INTEGER"),
    ("prompt_injection_log", "findings_json", "TEXT"),
    ("prompt_injection_log", "scanned_at", "TEXT"),
    ("translation_units", "candidate_selected", "INTEGER"),
    ("translation_units", "source_file", "TEXT"),
    ("translation_units", "unit_kind", "TEXT"),
    ("translation_units", "unit_name", "TEXT"),
    ("pg_proposal_quality_scores", "check_details", "TEXT"),
    ("pg_proposal_quality_scores", "composite_score", "REAL"),
    ("forge_hub_ratings", "updated_at", "TEXT"),
    ("forge_hub_trust_scores", "breakdown", "TEXT"),
    ("emass_sync_log", "artifacts_synced", "INTEGER"),
    ("emass_sync_log", "classification", "TEXT DEFAULT 'CUI'"),
    ("emass_sync_log", "completed_at", "TEXT"),
    ("emass_sync_log", "controls_synced", "INTEGER"),
    ("emass_sync_log", "details", "TEXT"),
    ("emass_sync_log", "error_message", "TEXT"),
    ("emass_sync_log", "poam_synced", "INTEGER"),
    ("emass_sync_log", "started_at", "TEXT"),
    ("emass_sync_log", "sync_mode", "TEXT"),
    ("emass_sync_log", "sync_status", "TEXT"),
    ("emass_sync_log", "test_results_synced", "INTEGER"),
    ("emass_systems", "authorization_termination_date", "TEXT"),
    ("emass_systems", "classification", "TEXT DEFAULT 'CUI'"),
    ("emass_systems", "last_sync_status", "TEXT"),
    ("emass_systems", "sync_mode", "TEXT"),
]


def up(conn):
    """Apply migration -- add 78 missing columns."""
    cursor = conn.cursor()
    added = 0
    skipped = 0
    for table, column, col_type in _COLUMNS:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            added += 1
        except Exception:
            skipped += 1  # Column already exists
    conn.commit()
    return {"added": added, "skipped": skipped, "total": len(_COLUMNS)}
