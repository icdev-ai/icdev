#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 005: Phase 37 AI Security — MITRE ATLAS integration.

Targets data/icdev.db.
Adds: prompt_injection_log (D217), ai_telemetry (D218),
      ai_bom, atlas_assessments, atlas_red_team_results,
      owasp_llm_assessments, nist_ai_rmf_assessments,
      iso42001_assessments.
"""

import sqlite3


def _table_exists(conn, table):
    """Check if a table exists."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


AI_SECURITY_SCHEMA = """
-- ============================================================
-- PROMPT INJECTION LOG — append-only detection log (D217, D6)
-- ============================================================
CREATE TABLE IF NOT EXISTS prompt_injection_log (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    user_id TEXT,
    source TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    detected INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.0,
    action TEXT NOT NULL DEFAULT 'allow'
        CHECK(action IN ('block', 'flag', 'warn', 'allow')),
    finding_count INTEGER NOT NULL DEFAULT 0,
    findings_json TEXT,
    scanned_at TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_pil_project ON prompt_injection_log(project_id);
CREATE INDEX IF NOT EXISTS idx_pil_action ON prompt_injection_log(action);
CREATE INDEX IF NOT EXISTS idx_pil_scanned ON prompt_injection_log(scanned_at);
CREATE INDEX IF NOT EXISTS idx_pil_source ON prompt_injection_log(source);
CREATE INDEX IF NOT EXISTS idx_pil_detected ON prompt_injection_log(detected);

-- ============================================================
-- AI TELEMETRY — LLM interaction audit trail (D218, D6)
-- ============================================================
CREATE TABLE IF NOT EXISTS ai_telemetry (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    user_id TEXT,
    agent_id TEXT,
    model_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    function TEXT,
    prompt_hash TEXT NOT NULL,
    response_hash TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    thinking_tokens INTEGER DEFAULT 0,
    latency_ms REAL DEFAULT 0.0,
    cost_usd REAL DEFAULT 0.0,
    classification TEXT DEFAULT 'CUI',
    api_key_source TEXT DEFAULT 'system'
        CHECK(api_key_source IN ('system', 'byok', 'department')),
    injection_scan_result TEXT
        CHECK(injection_scan_result IS NULL OR injection_scan_result IN ('clean', 'flagged', 'blocked')),
    logged_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_tel_project ON ai_telemetry(project_id);
CREATE INDEX IF NOT EXISTS idx_ai_tel_user ON ai_telemetry(user_id);
CREATE INDEX IF NOT EXISTS idx_ai_tel_model ON ai_telemetry(model_id);
CREATE INDEX IF NOT EXISTS idx_ai_tel_provider ON ai_telemetry(provider);
CREATE INDEX IF NOT EXISTS idx_ai_tel_logged ON ai_telemetry(logged_at);
CREATE INDEX IF NOT EXISTS idx_ai_tel_function ON ai_telemetry(function);

-- ============================================================
-- AI BOM — AI Bill of Materials (models, datasets, frameworks)
-- ============================================================
CREATE TABLE IF NOT EXISTS ai_bom (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    component_type TEXT NOT NULL
        CHECK(component_type IN ('model', 'dataset', 'framework', 'library', 'service')),
    component_name TEXT NOT NULL,
    version TEXT,
    provider TEXT,
    license TEXT,
    risk_level TEXT DEFAULT 'medium'
        CHECK(risk_level IN ('critical', 'high', 'medium', 'low')),
    atlas_techniques_json TEXT,
    mitigations_json TEXT,
    last_assessed TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ai_bom_project ON ai_bom(project_id);
CREATE INDEX IF NOT EXISTS idx_ai_bom_type ON ai_bom(component_type);
CREATE INDEX IF NOT EXISTS idx_ai_bom_risk ON ai_bom(risk_level);

-- ============================================================
-- ATLAS ASSESSMENTS — MITRE ATLAS compliance assessments
-- ============================================================
CREATE TABLE IF NOT EXISTS atlas_assessments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    framework_version TEXT NOT NULL DEFAULT 'v5.4.0',
    assessment_date TEXT NOT NULL,
    overall_score REAL DEFAULT 0.0,
    coverage_pct REAL DEFAULT 0.0,
    mitigations_implemented INTEGER DEFAULT 0,
    mitigations_total INTEGER DEFAULT 0,
    techniques_covered INTEGER DEFAULT 0,
    techniques_total INTEGER DEFAULT 0,
    results_json TEXT,
    automated_checks_json TEXT,
    assessor TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_atlas_assess_project ON atlas_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_atlas_assess_date ON atlas_assessments(assessment_date);

-- ============================================================
-- ATLAS RED TEAM RESULTS — AI-specific red team scan results (D219)
-- ============================================================
CREATE TABLE IF NOT EXISTS atlas_red_team_results (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    technique_id TEXT NOT NULL,
    technique_name TEXT NOT NULL,
    tactic TEXT,
    test_name TEXT NOT NULL,
    result TEXT NOT NULL
        CHECK(result IN ('pass', 'fail', 'partial', 'error', 'skipped')),
    severity TEXT DEFAULT 'medium'
        CHECK(severity IN ('critical', 'high', 'medium', 'low', 'info')),
    evidence_json TEXT,
    remediation TEXT,
    scanner_version TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_atlas_rt_project ON atlas_red_team_results(project_id);
CREATE INDEX IF NOT EXISTS idx_atlas_rt_technique ON atlas_red_team_results(technique_id);
CREATE INDEX IF NOT EXISTS idx_atlas_rt_result ON atlas_red_team_results(result);
CREATE INDEX IF NOT EXISTS idx_atlas_rt_date ON atlas_red_team_results(scan_date);

-- ============================================================
-- OWASP LLM ASSESSMENTS — OWASP LLM Top 10 compliance
-- ============================================================
CREATE TABLE IF NOT EXISTS owasp_llm_assessments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    framework_version TEXT NOT NULL DEFAULT '2025',
    assessment_date TEXT NOT NULL,
    overall_score REAL DEFAULT 0.0,
    coverage_pct REAL DEFAULT 0.0,
    items_satisfied INTEGER DEFAULT 0,
    items_total INTEGER DEFAULT 10,
    results_json TEXT,
    assessor TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_owasp_llm_project ON owasp_llm_assessments(project_id);

-- ============================================================
-- NIST AI RMF ASSESSMENTS — NIST AI Risk Management Framework
-- ============================================================
CREATE TABLE IF NOT EXISTS nist_ai_rmf_assessments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    framework_version TEXT NOT NULL DEFAULT '1.0',
    assessment_date TEXT NOT NULL,
    overall_score REAL DEFAULT 0.0,
    govern_score REAL DEFAULT 0.0,
    map_score REAL DEFAULT 0.0,
    measure_score REAL DEFAULT 0.0,
    manage_score REAL DEFAULT 0.0,
    functions_assessed INTEGER DEFAULT 0,
    functions_total INTEGER DEFAULT 4,
    results_json TEXT,
    assessor TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nist_ai_project ON nist_ai_rmf_assessments(project_id);

-- ============================================================
-- ISO 42001 ASSESSMENTS — AI Management System
-- ============================================================
CREATE TABLE IF NOT EXISTS iso42001_assessments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    framework_version TEXT NOT NULL DEFAULT '2023',
    assessment_date TEXT NOT NULL,
    overall_score REAL DEFAULT 0.0,
    coverage_pct REAL DEFAULT 0.0,
    controls_satisfied INTEGER DEFAULT 0,
    controls_total INTEGER DEFAULT 0,
    results_json TEXT,
    assessor TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_iso42001_project ON iso42001_assessments(project_id);
"""


def up(conn: sqlite3.Connection):
    """Apply migration 005 — Phase 37 AI Security tables."""
    tables = [
        "prompt_injection_log", "ai_telemetry", "ai_bom",
        "atlas_assessments", "atlas_red_team_results",
        "owasp_llm_assessments", "nist_ai_rmf_assessments",
        "iso42001_assessments",
    ]

    existing = [t for t in tables if _table_exists(conn, t)]
    if existing:
        print(f"  Note: tables already exist (skipping): {', '.join(existing)}")

    conn.executescript(AI_SECURITY_SCHEMA)
    conn.commit()
    print(f"  Migration 005 applied: {len(tables)} AI security tables created")


if __name__ == "__main__":
    import sys
    from pathlib import Path

    BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
    DB_PATH = BASE_DIR / "data" / "icdev.db"

    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    up(conn)
    conn.close()
    print("Migration 005 complete.")
