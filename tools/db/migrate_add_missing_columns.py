#!/usr/bin/env python3
"""One-time migration: add missing columns to live icdev.db.

Fixes 30 schema-code column drift issues where Python tools INSERT columns
that did not exist in the CREATE TABLE definitions.

Safe to re-run: uses try/except to skip columns that already exist.

Usage:
    python tools/db/migrate_add_missing_columns.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "icdev.db"


ALTER_STATEMENTS = [
    # compliance_detection_log
    ("compliance_detection_log", "data_categories", "TEXT"),
    ("compliance_detection_log", "detected_frameworks", "TEXT"),
    ("compliance_detection_log", "recommended_frameworks", "TEXT"),
    ("compliance_detection_log", "required_frameworks", "TEXT"),
    ("compliance_detection_log", "rules_matched", "TEXT"),
    # innovation_signals
    ("innovation_signals", "body", "TEXT"),
    ("innovation_signals", "composite_score", "REAL"),
    ("innovation_signals", "created_at", "TEXT"),
    ("innovation_signals", "implementation_status", "TEXT"),
    # code_quality_metrics
    ("code_quality_metrics", "avg_cognitive", "REAL"),
    ("code_quality_metrics", "avg_cyclomatic", "REAL"),
    ("code_quality_metrics", "avg_loc", "REAL"),
    ("code_quality_metrics", "avg_nesting", "REAL"),
    ("code_quality_metrics", "avg_params", "REAL"),
    ("code_quality_metrics", "smells", "TEXT"),
    ("code_quality_metrics", "total_functions", "INTEGER"),
    # knowledge_patterns
    ("knowledge_patterns", "source", "TEXT"),
    ("knowledge_patterns", "resolution", "TEXT"),
    ("knowledge_patterns", "detection_rule", "TEXT"),
    ("knowledge_patterns", "name", "TEXT"),
    ("knowledge_patterns", "solution", "TEXT"),
    # self_healing_events
    ("self_healing_events", "status", "TEXT"),
    # icd_documents
    ("icd_documents", "approval_status", "TEXT"),
    ("icd_documents", "updated_at", "TEXT"),
    # tsp_documents
    ("tsp_documents", "approval_status", "TEXT"),
    ("tsp_documents", "updated_at", "TEXT"),
    # dev_profiles
    ("dev_profiles", "dimensions", "TEXT"),
    ("dev_profiles", "template", "TEXT"),
    # propagation_log
    ("propagation_log", "classification", "TEXT DEFAULT 'CUI'"),
    ("propagation_log", "genome_version", "TEXT"),
    ("propagation_log", "initiated_at", "TEXT"),
    ("propagation_log", "initiated_by", "TEXT"),
    ("propagation_log", "propagation_status", "TEXT"),
    ("propagation_log", "source_child_id", "TEXT"),
    ("propagation_log", "target_child_id", "TEXT"),
    # child_app_registry
    ("child_app_registry", "blueprint_json", "TEXT"),
    ("child_app_registry", "child_type", "TEXT"),
    ("child_app_registry", "compliance_required", "INTEGER DEFAULT 0"),
    ("child_app_registry", "project_path", "TEXT"),
    ("child_app_registry", "status", "TEXT"),
    ("child_app_registry", "target_cloud", "TEXT"),
    # child_telemetry
    ("child_telemetry", "metric_data", "TEXT"),
    ("child_telemetry", "metric_type", "TEXT"),
    ("child_telemetry", "classification", "TEXT DEFAULT 'CUI'"),
    ("child_telemetry", "endpoint_url", "TEXT"),
    ("child_telemetry", "raw_response", "TEXT"),
    ("child_telemetry", "response_time_ms", "REAL"),
    # child_learned_behaviors
    ("child_learned_behaviors", "classification", "TEXT DEFAULT 'CUI'"),
    # ai_bom
    ("ai_bom", "classification", "TEXT DEFAULT 'CUI'"),
    ("ai_bom", "component_name", "TEXT"),
    ("ai_bom", "component_type", "TEXT"),
    ("ai_bom", "license", "TEXT"),
    ("ai_bom", "risk_level", "TEXT"),
    ("ai_bom", "updated_at", "TEXT"),
    # ai_telemetry
    ("ai_telemetry", "api_key_source", "TEXT"),
    ("ai_telemetry", "cost_usd", "REAL"),
    ("ai_telemetry", "injection_scan_result", "TEXT"),
    ("ai_telemetry", "logged_at", "TEXT"),
    ("ai_telemetry", "thinking_tokens", "INTEGER"),
    # atlas_red_team_results
    ("atlas_red_team_results", "classification", "TEXT DEFAULT 'CUI'"),
    ("atlas_red_team_results", "findings_json", "TEXT"),
    ("atlas_red_team_results", "passed", "INTEGER"),
    ("atlas_red_team_results", "scanned_at", "TEXT"),
    ("atlas_red_team_results", "technique", "TEXT"),
    ("atlas_red_team_results", "tests_passed", "INTEGER"),
    ("atlas_red_team_results", "tests_run", "INTEGER"),
    # prompt_injection_log
    ("prompt_injection_log", "classification", "TEXT DEFAULT 'CUI'"),
    ("prompt_injection_log", "finding_count", "INTEGER"),
    ("prompt_injection_log", "findings_json", "TEXT"),
    ("prompt_injection_log", "scanned_at", "TEXT"),
    # translation_units
    ("translation_units", "candidate_selected", "INTEGER"),
    ("translation_units", "source_file", "TEXT"),
    ("translation_units", "unit_kind", "TEXT"),
    ("translation_units", "unit_name", "TEXT"),
    # pg_proposal_quality_scores
    ("pg_proposal_quality_scores", "check_details", "TEXT"),
    ("pg_proposal_quality_scores", "composite_score", "REAL"),
    # forge_hub_ratings
    ("forge_hub_ratings", "updated_at", "TEXT"),
    # forge_hub_trust_scores
    ("forge_hub_trust_scores", "breakdown", "TEXT"),
    # emass_sync_log
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
    # emass_systems
    ("emass_systems", "authorization_termination_date", "TEXT"),
    ("emass_systems", "classification", "TEXT DEFAULT 'CUI'"),
    ("emass_systems", "last_sync_status", "TEXT"),
    ("emass_systems", "sync_mode", "TEXT"),
]


def main():
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    success = 0
    skipped = 0
    errors = 0

    for table, column, col_type in ALTER_STATEMENTS:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            success += 1
            print(f"  Added: {table}.{column} ({col_type})")
        except Exception as e:
            err_msg = str(e).lower()
            if "duplicate column name" in err_msg:
                skipped += 1
            else:
                errors += 1
                print(f"  ERROR: {table}.{column}: {e}")

    conn.commit()
    conn.close()

    print(f"\nMigration complete: {success} added, {skipped} already existed, {errors} errors")


if __name__ == "__main__":
    main()
