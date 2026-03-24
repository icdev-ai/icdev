#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 006: Phase 36 Evolution Engine — child capabilities, telemetry, genome.

Targets data/icdev.db.
Adds: child_capabilities, child_telemetry, child_learned_behaviors,
      capability_genome, genome_versions, capability_evaluations,
      staging_environments, propagation_log.
"""

import sqlite3


def _table_exists(conn, table):
    """Check if a table exists."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


EVOLUTION_SCHEMA = """
-- ============================================================
-- CHILD CAPABILITIES — per-child capability tracking (Phase 36)
-- ============================================================
CREATE TABLE IF NOT EXISTS child_capabilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id TEXT NOT NULL,
    capability_name TEXT NOT NULL,
    version TEXT DEFAULT '1.0.0',
    status TEXT DEFAULT 'active'
        CHECK(status IN ('active', 'disabled', 'deprecated',
                         'staging', 'evaluating')),
    source TEXT DEFAULT 'parent'
        CHECK(source IN ('parent', 'learned', 'marketplace',
                         'evolved', 'manual')),
    learned_at TEXT DEFAULT (datetime('now')),
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(child_id, capability_name)
);

CREATE INDEX IF NOT EXISTS idx_child_capabilities_child
    ON child_capabilities(child_id);
CREATE INDEX IF NOT EXISTS idx_child_capabilities_status
    ON child_capabilities(status);
CREATE INDEX IF NOT EXISTS idx_child_capabilities_source
    ON child_capabilities(source);

-- ============================================================
-- CHILD TELEMETRY — pull-based health + performance data (D210)
-- ============================================================
CREATE TABLE IF NOT EXISTS child_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    health_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK(health_status IN ('healthy', 'degraded', 'unhealthy',
                                'unreachable', 'unknown')),
    genome_version TEXT,
    uptime_hours REAL DEFAULT 0.0,
    error_rate REAL DEFAULT 0.0,
    compliance_scores_json TEXT DEFAULT '{}',
    learned_behaviors_json TEXT DEFAULT '[]',
    response_time_ms INTEGER DEFAULT 0,
    raw_response TEXT,
    endpoint_url TEXT,
    classification TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_child_telemetry_child
    ON child_telemetry(child_id);
CREATE INDEX IF NOT EXISTS idx_child_telemetry_collected
    ON child_telemetry(collected_at);
CREATE INDEX IF NOT EXISTS idx_child_telemetry_status
    ON child_telemetry(health_status);

-- ============================================================
-- CHILD LEARNED BEHAVIORS — behaviors discovered by children
-- ============================================================
CREATE TABLE IF NOT EXISTS child_learned_behaviors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id TEXT NOT NULL,
    behavior_type TEXT NOT NULL
        CHECK(behavior_type IN ('optimization', 'error_recovery',
                                'compliance_shortcut', 'performance_tuning',
                                'security_pattern', 'workflow_improvement',
                                'configuration', 'other')),
    description TEXT NOT NULL,
    evidence_json TEXT DEFAULT '{}',
    confidence REAL DEFAULT 0.0 CHECK(confidence >= 0.0 AND confidence <= 1.0),
    evaluated INTEGER DEFAULT 0,
    absorbed INTEGER DEFAULT 0,
    discovered_at TEXT DEFAULT (datetime('now')),
    evaluated_at TEXT,
    absorbed_at TEXT,
    classification TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_child_learned_child
    ON child_learned_behaviors(child_id);
CREATE INDEX IF NOT EXISTS idx_child_learned_type
    ON child_learned_behaviors(behavior_type);
CREATE INDEX IF NOT EXISTS idx_child_learned_confidence
    ON child_learned_behaviors(confidence);
CREATE INDEX IF NOT EXISTS idx_child_learned_evaluated
    ON child_learned_behaviors(evaluated);
CREATE INDEX IF NOT EXISTS idx_child_learned_absorbed
    ON child_learned_behaviors(absorbed);

-- ============================================================
-- CAPABILITY GENOME — canonical capability definitions
-- ============================================================
CREATE TABLE IF NOT EXISTS capability_genome (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    category TEXT NOT NULL
        CHECK(category IN ('security', 'compliance', 'build', 'test',
                           'deploy', 'monitor', 'knowledge', 'integration',
                           'ai_ml', 'infrastructure', 'other')),
    current_version TEXT DEFAULT '1.0.0',
    spec_json TEXT DEFAULT '{}',
    dependencies TEXT DEFAULT '[]',
    status TEXT DEFAULT 'active'
        CHECK(status IN ('active', 'deprecated', 'experimental', 'archived')),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_capability_genome_category
    ON capability_genome(category);
CREATE INDEX IF NOT EXISTS idx_capability_genome_status
    ON capability_genome(status);

-- ============================================================
-- GENOME VERSIONS — version history for capability genome
-- ============================================================
CREATE TABLE IF NOT EXISTS genome_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    genome_id TEXT NOT NULL REFERENCES capability_genome(id),
    version TEXT NOT NULL,
    changelog TEXT,
    spec_json TEXT DEFAULT '{}',
    released_by TEXT DEFAULT 'evolution-engine',
    released_at TEXT DEFAULT (datetime('now')),
    UNIQUE(genome_id, version)
);

CREATE INDEX IF NOT EXISTS idx_genome_versions_genome
    ON genome_versions(genome_id);
CREATE INDEX IF NOT EXISTS idx_genome_versions_version
    ON genome_versions(version);

-- ============================================================
-- CAPABILITY EVALUATIONS — evaluation results for candidate capabilities
-- ============================================================
CREATE TABLE IF NOT EXISTS capability_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capability_name TEXT NOT NULL,
    source_child_id TEXT,
    evaluation_type TEXT NOT NULL
        CHECK(evaluation_type IN ('automated', 'manual', 'a_b_test',
                                   'staging', 'production_canary')),
    score REAL DEFAULT 0.0 CHECK(score >= 0.0 AND score <= 1.0),
    metrics_json TEXT DEFAULT '{}',
    gate_results_json TEXT DEFAULT '{}',
    verdict TEXT DEFAULT 'pending'
        CHECK(verdict IN ('pending', 'approved', 'rejected',
                          'needs_review', 'deferred')),
    evaluator TEXT DEFAULT 'evolution-engine',
    notes TEXT,
    evaluated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_capability_evaluations_name
    ON capability_evaluations(capability_name);
CREATE INDEX IF NOT EXISTS idx_capability_evaluations_source
    ON capability_evaluations(source_child_id);
CREATE INDEX IF NOT EXISTS idx_capability_evaluations_verdict
    ON capability_evaluations(verdict);
CREATE INDEX IF NOT EXISTS idx_capability_evaluations_type
    ON capability_evaluations(evaluation_type);

-- ============================================================
-- STAGING ENVIRONMENTS — isolated test environments for capabilities
-- ============================================================
CREATE TABLE IF NOT EXISTS staging_environments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    purpose TEXT
        CHECK(purpose IN ('capability_test', 'integration_test',
                          'compliance_validation', 'performance_benchmark',
                          'security_audit', 'other')),
    status TEXT DEFAULT 'provisioning'
        CHECK(status IN ('provisioning', 'ready', 'in_use',
                         'teardown', 'destroyed', 'error')),
    config_json TEXT DEFAULT '{}',
    child_id TEXT,
    capability_under_test TEXT,
    infrastructure_json TEXT DEFAULT '{}',
    provisioned_at TEXT,
    last_used_at TEXT,
    destroyed_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_staging_environments_status
    ON staging_environments(status);
CREATE INDEX IF NOT EXISTS idx_staging_environments_child
    ON staging_environments(child_id);
CREATE INDEX IF NOT EXISTS idx_staging_environments_purpose
    ON staging_environments(purpose);

-- ============================================================
-- PROPAGATION LOG — tracks capability propagation to children
-- (append-only, D6 pattern)
-- ============================================================
CREATE TABLE IF NOT EXISTS propagation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capability_name TEXT NOT NULL,
    genome_version TEXT NOT NULL,
    source_type TEXT NOT NULL
        CHECK(source_type IN ('genome', 'child_learned', 'marketplace',
                              'manual', 'rollback')),
    source_child_id TEXT,
    target_child_id TEXT NOT NULL,
    propagation_status TEXT DEFAULT 'pending'
        CHECK(propagation_status IN ('pending', 'in_progress', 'success',
                                      'failed', 'rolled_back', 'skipped')),
    evaluation_id INTEGER REFERENCES capability_evaluations(id),
    staging_env_id TEXT REFERENCES staging_environments(id),
    error_details TEXT,
    initiated_by TEXT DEFAULT 'evolution-engine',
    initiated_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    classification TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_propagation_log_capability
    ON propagation_log(capability_name);
CREATE INDEX IF NOT EXISTS idx_propagation_log_target
    ON propagation_log(target_child_id);
CREATE INDEX IF NOT EXISTS idx_propagation_log_source_child
    ON propagation_log(source_child_id);
CREATE INDEX IF NOT EXISTS idx_propagation_log_status
    ON propagation_log(propagation_status);
CREATE INDEX IF NOT EXISTS idx_propagation_log_initiated
    ON propagation_log(initiated_at);

-- ============================================================
-- ATLAS ASSESSMENTS — MITRE ATLAS framework assessment results
-- ============================================================
CREATE TABLE IF NOT EXISTS atlas_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    family TEXT,
    status TEXT DEFAULT 'not_assessed'
        CHECK(status IN ('not_assessed', 'satisfied', 'partially_satisfied',
                         'not_satisfied', 'not_applicable', 'risk_accepted')),
    evidence_description TEXT,
    evidence_path TEXT,
    automation_result TEXT,
    notes TEXT,
    nist_800_53_crosswalk TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, requirement_id)
);

CREATE INDEX IF NOT EXISTS idx_atlas_assessments_project
    ON atlas_assessments(project_id);
"""


def up(conn):
    """Apply Phase 36 evolution engine tables to icdev.db."""
    tables = [
        "child_capabilities",
        "child_telemetry",
        "child_learned_behaviors",
        "capability_genome",
        "genome_versions",
        "capability_evaluations",
        "staging_environments",
        "propagation_log",
        "atlas_assessments",
    ]

    # Only create tables that don't exist yet (idempotent)
    missing = [t for t in tables if not _table_exists(conn, t)]
    if missing:
        conn.executescript(EVOLUTION_SCHEMA)

    conn.commit()


def down(conn):
    """Rollback: drop Phase 36 evolution engine tables."""
    tables = [
        "propagation_log",
        "staging_environments",
        "capability_evaluations",
        "genome_versions",
        "capability_genome",
        "child_learned_behaviors",
        "child_telemetry",
        "child_capabilities",
        "atlas_assessments",
    ]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
