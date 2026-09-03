#!/usr/bin/env python3
# CUI // SP-CTI
"""Initialize the ICDEV™ operational database with full schema."""

import argparse
import os
import sqlite3
import sys
from pathlib import Path
# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# UTF-8 safe stdout on Windows (default cp1252 mangles ™ and other glyphs).
try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _default_db_path() -> Path:
    # Mirror tools/db/storage.py: don't default inside site-packages.
    base = str(BASE_DIR).replace("\\", "/").lower()
    prefix = str(Path(sys.prefix).resolve()).replace("\\", "/").lower()
    if "site-packages" in base or (prefix and base.startswith(prefix)):
        return Path.cwd() / "data" / "icdev.db"
    return BASE_DIR / "data" / "icdev.db"


DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_default_db_path())))

SCHEMA_SQL = """
-- ============================================================
-- PROJECTS
-- ============================================================
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    type TEXT NOT NULL CHECK(type IN ('webapp', 'microservice', 'api', 'cli', 'data_pipeline', 'iac')),
    classification TEXT NOT NULL DEFAULT 'CUI',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'paused', 'completed', 'archived')),
    tech_stack_backend TEXT,
    tech_stack_frontend TEXT,
    tech_stack_database TEXT,
    directory_path TEXT NOT NULL,
    created_by TEXT,
    impact_level TEXT DEFAULT 'IL5' CHECK(impact_level IN ('IL2', 'IL4', 'IL5', 'IL6')),
    cloud_environment TEXT DEFAULT 'aws-govcloud',
    target_frameworks TEXT,
    ato_status TEXT DEFAULT 'none' CHECK(ato_status IN ('none', 'in_progress', 'iato', 'ato', 'cato', 'dato', 'denied')),
    accrediting_authority TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- AGENTS
-- ============================================================
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'inactive' CHECK(status IN ('active', 'inactive', 'error')),
    capabilities TEXT,
    last_heartbeat TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT
);

-- ============================================================
-- A2A TASKS
-- ============================================================
CREATE TABLE IF NOT EXISTS a2a_tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    source_agent_id TEXT REFERENCES agents(id),
    target_agent_id TEXT REFERENCES agents(id),
    skill_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted' CHECK(status IN ('submitted', 'working', 'input-required', 'completed', 'failed', 'canceled')),
    input_data TEXT,
    output_data TEXT,
    error_message TEXT,
    priority INTEGER DEFAULT 5,
    parent_task_id TEXT REFERENCES a2a_tasks(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS a2a_task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES a2a_tasks(id),
    status TEXT NOT NULL,
    message TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS a2a_task_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES a2a_tasks(id),
    name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    data TEXT,
    data_blob BLOB,
    file_path TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- AUDIT TRAIL (append-only, immutable — NIST AU controls)
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT REFERENCES projects(id),
    event_type TEXT NOT NULL CHECK(event_type IN (
@@AUDIT_EVENT_TYPES@@
    )),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    affected_files TEXT,
    classification TEXT DEFAULT 'CUI',
    ip_address TEXT,
    session_id TEXT,
    recorded_at TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_project ON audit_trail(project_id);
CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_trail(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_trail(actor);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_trail(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_trail_actor_action ON audit_trail(actor, action);

-- ============================================================
-- AUDIT (lightweight general event log — alias sink for DELETE/UPDATE guards)
-- ============================================================
CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor      TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL DEFAULT '',
    table_name TEXT NOT NULL DEFAULT '',
    row_id     TEXT,
    detail     TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit(actor);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit(action);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(created_at);

-- ============================================================
-- CHAIN ORCHESTRATION TELEMETRY (CoT / CoD)
-- ============================================================
CREATE TABLE IF NOT EXISTS llm_chain_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    function TEXT NOT NULL,
    chain_mode TEXT NOT NULL,
    models_used TEXT NOT NULL DEFAULT '[]',
    rounds TEXT NOT NULL DEFAULT '{}',
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    duration_ms INTEGER DEFAULT 0,
    final_model_id TEXT,
    stop_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chain_telemetry_function ON llm_chain_telemetry (function);
CREATE INDEX IF NOT EXISTS idx_chain_telemetry_created ON llm_chain_telemetry (created_at);

-- ============================================================
-- CONTINUOUS COMPLIANCE EVIDENCE CHAIN (D-CHAIN-1)
-- Unified PDC/NDC/SDC audit trail snapshots aligned to OSCAL 1.1.2
-- ============================================================
CREATE TABLE IF NOT EXISTS compliance_evidence_chain (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id       TEXT NOT NULL,
    event_id       TEXT NOT NULL,
    source         TEXT NOT NULL CHECK (source IN ('pdc', 'ndc', 'sdc', 'icdev')),
    event_type     TEXT NOT NULL,
    actor          TEXT,
    action         TEXT NOT NULL,
    oscal_controls TEXT DEFAULT '[]',
    oscal_family   TEXT,
    evidence_type  TEXT DEFAULT 'audit' CHECK (evidence_type IN ('audit', 'test', 'assessment', 'deployment', 'compliance', 'other')),
    classification TEXT DEFAULT 'CUI // SP-CTI',
    event_ts       TEXT NOT NULL,
    details_json   TEXT,
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cec_chain ON compliance_evidence_chain(chain_id);
CREATE INDEX IF NOT EXISTS idx_cec_source ON compliance_evidence_chain(source);
CREATE INDEX IF NOT EXISTS idx_cec_ts ON compliance_evidence_chain(event_ts);
CREATE INDEX IF NOT EXISTS idx_cec_family ON compliance_evidence_chain(oscal_family);

-- ============================================================
-- COMPLIANCE TRACKING
-- ============================================================
CREATE TABLE IF NOT EXISTS compliance_controls (
    id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    supplemental_guidance TEXT,
    impact_level TEXT,
    enhancements TEXT
);

CREATE TABLE IF NOT EXISTS project_controls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    control_id TEXT NOT NULL REFERENCES compliance_controls(id),
    implementation_status TEXT NOT NULL DEFAULT 'planned' CHECK(implementation_status IN ('planned', 'implemented', 'partially_implemented', 'not_applicable', 'compensating')),
    implementation_description TEXT,
    responsible_role TEXT,
    evidence_path TEXT,
    last_assessed TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, control_id)
);

CREATE TABLE IF NOT EXISTS control_narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    control_id TEXT NOT NULL,
    narrative_text TEXT NOT NULL,
    generation_method TEXT DEFAULT 'template',
    generated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, control_id)
);

CREATE TABLE IF NOT EXISTS ssp_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    version TEXT NOT NULL,
    system_name TEXT NOT NULL,
    system_boundary TEXT,
    authorization_type TEXT,
    content TEXT NOT NULL,
    file_path TEXT,
    classification TEXT DEFAULT 'CUI',
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft', 'review', 'approved', 'superseded')),
    approved_by TEXT,
    approved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS poam_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    weakness_id TEXT NOT NULL,
    weakness_description TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('critical', 'high', 'moderate', 'low')),
    source TEXT NOT NULL,
    control_id TEXT REFERENCES compliance_controls(id),
    status TEXT DEFAULT 'open' CHECK(status IN ('open', 'in_progress', 'completed', 'accepted_risk')),
    corrective_action TEXT,
    milestone_date DATE,
    completion_date DATE,
    responsible_party TEXT,
    resources_required TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Canvas-finding approval state (one row per unique finding across all canvas DBs).
-- Findings live in their source canvas DBs (security_canvas.db, data_canvas.db, etc.)
-- and are re-generated each scan. This table persists the human approval decision
-- keyed by a stable SHA-256 hash of (canvas, rule_id, title, affected_entity).
-- Mutable: a finding can move pending -> approved -> remediated. Each transition
-- is also logged to audit_trail (append-only) for compliance.
CREATE TABLE IF NOT EXISTS finding_approvals (
    finding_hash TEXT PRIMARY KEY,
    canvas_source TEXT NOT NULL,
    rule_id TEXT,
    severity TEXT,
    title TEXT NOT NULL,
    affected_entity TEXT,
    decision TEXT DEFAULT 'pending' CHECK(decision IN ('pending', 'approved', 'declined', 'accepted_risk', 'remediated')),
    decision_by TEXT,
    decision_at TIMESTAMP,
    decision_rationale TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stig_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    stig_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('CAT1', 'CAT2', 'CAT3')),
    title TEXT NOT NULL,
    description TEXT,
    check_content TEXT,
    fix_text TEXT,
    status TEXT DEFAULT 'Open' CHECK(status IN ('Open', 'NotAFinding', 'Not_Applicable', 'Not_Reviewed')),
    comments TEXT,
    target_type TEXT,
    assessed_by TEXT,
    assessed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sbom_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    version TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'cyclonedx',
    file_path TEXT NOT NULL,
    component_count INTEGER,
    vulnerability_count INTEGER,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sbom_components (
    id              TEXT    PRIMARY KEY,
    component_name  TEXT    NOT NULL,
    version         TEXT,
    vendor          TEXT,
    component_type  TEXT    CHECK(component_type IN (
                                'library', 'framework', 'container', 'os',
                                'firmware', 'device', 'application', 'service', 'other')),
    purl            TEXT,
    license         TEXT,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_name   ON sbom_components(component_name);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_vendor ON sbom_components(vendor);

CREATE TABLE IF NOT EXISTS supply_chain_vulnerabilities (
    id                TEXT    PRIMARY KEY,
    sbom_id           TEXT    NOT NULL REFERENCES sbom_components(id),
    cve_id            TEXT    NOT NULL,
    cvss_score        REAL    NOT NULL DEFAULT 0.0,
    severity          TEXT    CHECK(severity IN (
                                  'critical', 'high', 'medium', 'low',
                                  'informational', 'none')),
    affected_versions TEXT,
    fixed_version     TEXT,
    classification    TEXT    NOT NULL DEFAULT 'CUI',
    created_at        TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scv_sbom_id    ON supply_chain_vulnerabilities(sbom_id);
CREATE INDEX IF NOT EXISTS idx_scv_cvss_score ON supply_chain_vulnerabilities(cvss_score);

CREATE TABLE IF NOT EXISTS supply_chain_risk_scores (
    id              TEXT    PRIMARY KEY,
    sbom_id         TEXT    NOT NULL REFERENCES sbom_components(id),
    risk_level      TEXT    CHECK(risk_level IN (
                                'critical', 'high', 'medium', 'low', 'none')),
    exploitability  TEXT    CHECK(exploitability IN (
                                'functional', 'poc', 'unproven', 'not_defined')),
    patch_available INTEGER NOT NULL DEFAULT 0,
    last_assessed   TEXT    NOT NULL,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scrs_sbom_id      ON supply_chain_risk_scores(sbom_id);
CREATE INDEX IF NOT EXISTS idx_scrs_last_assessed ON supply_chain_risk_scores(last_assessed);

-- Component Dependency Relationship edges — the 2026 SBOM Minimum Elements
-- graph (migration 20260808030213_sbom_2026_minimum_elements, sbx-fnd-02).
-- Declared here as well as in the migration for the same reason migration 209's
-- tables are: a NEW table is safe to declare in both places because both sides
-- say CREATE TABLE IF NOT EXISTS, whichever runs first wins and the other is a
-- no-op. The COLUMNS that same migration adds to sbom_records and
-- sbom_components are deliberately NOT mirrored into their CREATE TABLE bodies
-- above: those are ALTER TABLE ADD COLUMN, and SQLite has no IF NOT EXISTS
-- clause for them, so declaring them here would make a fresh SQLite install
-- create the column and then fail the migration on a duplicate-column error the
-- runner's "already exists" guard does not match.
CREATE TABLE IF NOT EXISTS sbom_dependencies (
    id                  TEXT    PRIMARY KEY,
    sbom_record_id      INTEGER NOT NULL REFERENCES sbom_records(id),
    parent_component_id TEXT    NOT NULL REFERENCES sbom_components(id),
    child_component_id  TEXT    NOT NULL REFERENCES sbom_components(id),
    relationship_type   TEXT    NOT NULL DEFAULT 'depends_on',
    scope               TEXT,
    classification      TEXT    NOT NULL DEFAULT 'CUI',
    tenant_id           TEXT,
    created_at          TEXT    DEFAULT (datetime('now')),
    UNIQUE (sbom_record_id, parent_component_id, child_component_id, relationship_type)
);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_record ON sbom_dependencies(sbom_record_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_parent ON sbom_dependencies(parent_component_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_child  ON sbom_dependencies(child_component_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_tenant ON sbom_dependencies(tenant_id);

-- ============================================================
-- CODE REVIEW GATES
-- ============================================================
CREATE TABLE IF NOT EXISTS code_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    branch TEXT NOT NULL,
    merge_request_id TEXT,
    reviewer TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected', 'changes_requested')),
    security_gate_passed BOOLEAN DEFAULT FALSE,
    compliance_gate_passed BOOLEAN DEFAULT FALSE,
    test_gate_passed BOOLEAN DEFAULT FALSE,
    comments TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- SELF-HEALING & KNOWLEDGE
-- ============================================================
CREATE TABLE IF NOT EXISTS knowledge_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT NOT NULL CHECK(pattern_type IN ('failure', 'success', 'optimization', 'security', 'compliance', 'performance')),
    pattern_signature TEXT NOT NULL,
    name TEXT,
    description TEXT NOT NULL,
    root_cause TEXT,
    remediation TEXT,
    resolution TEXT,
    detection_rule TEXT,
    solution TEXT,
    source TEXT,
    confidence REAL DEFAULT 0.0,
    occurrence_count INTEGER DEFAULT 1,
    -- cch-obs-05: how often this pattern was USED to heal something, which is a
    -- different fact from how often the PROBLEM occurred. knowledge_server.py has
    -- ordered by, read and incremented it since it was written, against a table that
    -- never had it -- so every search_knowledge raised `column "use_count" does not
    -- exist` and the Cortex `kb` backend failed on every resolution.
    use_count INTEGER DEFAULT 0,
    last_occurrence TIMESTAMP,
    auto_healable BOOLEAN DEFAULT FALSE,
    embedding BLOB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS self_healing_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT REFERENCES projects(id),
    pattern_id INTEGER REFERENCES knowledge_patterns(id),
    trigger_source TEXT NOT NULL,
    trigger_data TEXT NOT NULL,
    action_taken TEXT,
    outcome TEXT CHECK(outcome IN ('success', 'failure', 'escalated', 'pending')),
    status TEXT,
    escalated_to TEXT,
    duration_seconds REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS failure_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT REFERENCES projects(id),
    source TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    stack_trace TEXT,
    context TEXT,
    resolved BOOLEAN DEFAULT FALSE,
    resolution TEXT,
    pattern_id INTEGER REFERENCES knowledge_patterns(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- DEPLOYMENT TRACKING
-- ============================================================
CREATE TABLE IF NOT EXISTS deployments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    environment TEXT NOT NULL CHECK(environment IN ('dev', 'staging', 'prod')),
    version TEXT NOT NULL,
    pipeline_id TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'running', 'succeeded', 'failed', 'rolled_back')),
    terraform_plan TEXT,
    deployed_by TEXT,
    rollback_version TEXT,
    health_check_passed BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- ============================================================
-- MONITORING & METRICS
-- ============================================================
CREATE TABLE IF NOT EXISTS metric_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    labels TEXT,
    source TEXT DEFAULT 'prometheus',
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- CSSP CERTIFICATION (DoD Instruction 8530.01)
-- ============================================================
CREATE TABLE IF NOT EXISTS cssp_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    functional_area TEXT NOT NULL CHECK(functional_area IN ('Identify', 'Protect', 'Detect', 'Respond', 'Sustain')),
    requirement_id TEXT NOT NULL,
    status TEXT DEFAULT 'not_assessed'
        CHECK(status IN ('not_assessed', 'satisfied', 'partially_satisfied', 'not_satisfied', 'not_applicable', 'risk_accepted')),
    evidence_description TEXT,
    evidence_path TEXT,
    automation_result TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, requirement_id)
);

CREATE INDEX IF NOT EXISTS idx_cssp_assess_project ON cssp_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_cssp_assess_area ON cssp_assessments(functional_area);

CREATE TABLE IF NOT EXISTS cssp_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    incident_id TEXT UNIQUE NOT NULL,
    severity TEXT CHECK(severity IN ('critical', 'high', 'moderate', 'low')),
    category TEXT,
    description TEXT NOT NULL,
    detection_method TEXT,
    detected_at TEXT NOT NULL,
    reported_to_soc_at TEXT,
    contained_at TEXT,
    resolved_at TEXT,
    status TEXT DEFAULT 'detected'
        CHECK(status IN ('detected', 'reported', 'contained', 'eradicated', 'recovered', 'closed', 'lessons_learned')),
    soc_ticket_id TEXT,
    root_cause TEXT,
    corrective_actions TEXT,
    lessons_learned TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cssp_incident_project ON cssp_incidents(project_id);
CREATE INDEX IF NOT EXISTS idx_cssp_incident_status ON cssp_incidents(status);

CREATE TABLE IF NOT EXISTS cssp_vuln_management (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    scan_date TEXT DEFAULT (datetime('now')),
    scan_type TEXT CHECK(scan_type IN ('sast', 'dast', 'dependency', 'container', 'infrastructure', 'penetration')),
    scanner TEXT,
    total_findings INTEGER DEFAULT 0,
    critical_count INTEGER DEFAULT 0,
    high_count INTEGER DEFAULT 0,
    medium_count INTEGER DEFAULT 0,
    low_count INTEGER DEFAULT 0,
    remediated_count INTEGER DEFAULT 0,
    accepted_risk_count INTEGER DEFAULT 0,
    false_positive_count INTEGER DEFAULT 0,
    report_path TEXT,
    sla_compliant INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cssp_vuln_project ON cssp_vuln_management(project_id);

CREATE TABLE IF NOT EXISTS cssp_certifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT UNIQUE NOT NULL REFERENCES projects(id),
    certification_type TEXT DEFAULT 'CSSP+ATO',
    status TEXT DEFAULT 'in_progress'
        CHECK(status IN ('in_progress', 'submitted', 'under_review', 'certified', 'denied', 'expired', 'revoked')),
    submitted_date TEXT,
    certified_date TEXT,
    expiration_date TEXT,
    authorizing_official TEXT,
    cssp_provider TEXT,
    ato_boundary TEXT,
    risk_level TEXT CHECK(risk_level IN ('low', 'moderate', 'high', 'very_high')),
    conditions TEXT,
    continuous_monitoring_plan TEXT,
    next_assessment_date TEXT,
    xacta_system_id TEXT,
    last_xacta_sync TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- SECURE BY DESIGN (SbD) ASSESSMENT TRACKING
-- ============================================================
CREATE TABLE IF NOT EXISTS sbd_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    domain TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    status TEXT DEFAULT 'not_assessed'
        CHECK(status IN ('not_assessed','satisfied','partially_satisfied','not_satisfied','not_applicable','risk_accepted')),
    evidence_description TEXT,
    evidence_path TEXT,
    automation_result TEXT,
    cisa_commitment INTEGER,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, requirement_id)
);

CREATE INDEX IF NOT EXISTS idx_sbd_assess_project ON sbd_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_sbd_assess_domain ON sbd_assessments(domain);

-- ============================================================
-- IV&V ASSESSMENT TRACKING (IEEE 1012)
-- ============================================================
CREATE TABLE IF NOT EXISTS ivv_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-ivv-engine',
    process_area TEXT NOT NULL,
    verification_type TEXT NOT NULL CHECK(verification_type IN ('verification','validation')),
    requirement_id TEXT NOT NULL,
    status TEXT DEFAULT 'not_assessed'
        CHECK(status IN ('not_assessed','pass','fail','partial','not_applicable','deferred')),
    evidence_description TEXT,
    evidence_path TEXT,
    automation_result TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, requirement_id)
);

CREATE INDEX IF NOT EXISTS idx_ivv_assess_project ON ivv_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_ivv_assess_area ON ivv_assessments(process_area);

-- ============================================================
-- IV&V FINDINGS (independent findings from V&V process)
-- ============================================================
CREATE TABLE IF NOT EXISTS ivv_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_id INTEGER,
    finding_id TEXT UNIQUE NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('critical','high','moderate','low')),
    process_area TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    recommendation TEXT,
    status TEXT DEFAULT 'open'
        CHECK(status IN ('open','in_progress','resolved','accepted_risk','deferred')),
    resolution TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ivv_finding_project ON ivv_findings(project_id);
CREATE INDEX IF NOT EXISTS idx_ivv_finding_status ON ivv_findings(status);

-- ============================================================
-- IV&V CERTIFICATION STATUS
-- ============================================================
CREATE TABLE IF NOT EXISTS ivv_certifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT UNIQUE NOT NULL,
    certification_type TEXT DEFAULT 'IV&V',
    status TEXT DEFAULT 'in_progress'
        CHECK(status IN ('in_progress','submitted','under_review','certified','conditional','denied','expired')),
    verification_score REAL,
    validation_score REAL,
    overall_score REAL,
    ivv_authority TEXT,
    independence_declaration TEXT,
    submitted_date TEXT,
    certified_date TEXT,
    expiration_date TEXT,
    conditions TEXT,
    open_findings_count INTEGER DEFAULT 0,
    critical_findings_count INTEGER DEFAULT 0,
    next_review_date TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- MAINTENANCE AUDIT SYSTEM (Phase 16F)
-- ============================================================
CREATE TABLE IF NOT EXISTS dependency_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    language TEXT NOT NULL,
    package_name TEXT NOT NULL,
    current_version TEXT NOT NULL,
    latest_version TEXT,
    latest_check_date TEXT,
    days_stale INTEGER DEFAULT 0,
    purl TEXT,
    scope TEXT DEFAULT 'required',
    dependency_file TEXT,
    direct INTEGER DEFAULT 1,
    license TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, language, package_name)
);

CREATE INDEX IF NOT EXISTS idx_dep_inv_project ON dependency_inventory(project_id);
CREATE INDEX IF NOT EXISTS idx_dep_inv_stale ON dependency_inventory(days_stale);

CREATE TABLE IF NOT EXISTS dependency_vulnerabilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    dependency_id INTEGER REFERENCES dependency_inventory(id),
    cve_id TEXT,
    advisory_id TEXT,
    severity TEXT NOT NULL CHECK(severity IN ('critical','high','medium','low','unknown')),
    cvss_score REAL,
    title TEXT NOT NULL,
    description TEXT,
    affected_versions TEXT,
    fix_version TEXT,
    fix_available INTEGER DEFAULT 0,
    exploit_available INTEGER DEFAULT 0,
    sla_category TEXT CHECK(sla_category IN ('critical','high','medium','low')),
    sla_deadline TEXT,
    status TEXT DEFAULT 'open' CHECK(status IN ('open','in_progress','remediated','accepted_risk','false_positive')),
    remediated_at TEXT,
    remediation_action TEXT,
    source TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, cve_id, dependency_id)
);

CREATE INDEX IF NOT EXISTS idx_dep_vuln_project ON dependency_vulnerabilities(project_id);
CREATE INDEX IF NOT EXISTS idx_dep_vuln_severity ON dependency_vulnerabilities(severity);
CREATE INDEX IF NOT EXISTS idx_dep_vuln_status ON dependency_vulnerabilities(status);
CREATE INDEX IF NOT EXISTS idx_dep_vuln_sla ON dependency_vulnerabilities(sla_deadline);

CREATE TABLE IF NOT EXISTS maintenance_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    audit_date TEXT DEFAULT (datetime('now')),
    auditor TEXT DEFAULT 'icdev-maintenance-engine',
    total_dependencies INTEGER DEFAULT 0,
    outdated_count INTEGER DEFAULT 0,
    vulnerable_count INTEGER DEFAULT 0,
    critical_vulns INTEGER DEFAULT 0,
    high_vulns INTEGER DEFAULT 0,
    medium_vulns INTEGER DEFAULT 0,
    low_vulns INTEGER DEFAULT 0,
    avg_staleness_days REAL DEFAULT 0.0,
    max_staleness_days INTEGER DEFAULT 0,
    sla_compliant_pct REAL DEFAULT 100.0,
    overdue_critical INTEGER DEFAULT 0,
    overdue_high INTEGER DEFAULT 0,
    maintenance_score REAL DEFAULT 100.0,
    languages_audited TEXT,
    report_path TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_maint_audit_project ON maintenance_audits(project_id);

CREATE TABLE IF NOT EXISTS remediation_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    vulnerability_id INTEGER REFERENCES dependency_vulnerabilities(id),
    dependency_id INTEGER REFERENCES dependency_inventory(id),
    action_type TEXT NOT NULL CHECK(action_type IN ('version_bump','patch_apply','replacement','risk_accept','manual_fix')),
    from_version TEXT,
    to_version TEXT,
    dependency_file TEXT,
    git_branch TEXT,
    git_commit TEXT,
    pr_url TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','applied','tested','merged','failed','rolled_back')),
    applied_at TEXT,
    tested_at TEXT,
    merged_at TEXT,
    applied_by TEXT DEFAULT 'icdev-maintenance-engine',
    test_results TEXT,
    rollback_reason TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_remed_project ON remediation_actions(project_id);
CREATE INDEX IF NOT EXISTS idx_remed_status ON remediation_actions(status);

-- ============================================================
-- MULTI-FRAMEWORK COMPLIANCE (Phase 17C)
-- ============================================================

-- Framework registry (FedRAMP, CMMC, 800-171, etc.)
CREATE TABLE IF NOT EXISTS framework_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    source TEXT,
    control_count INTEGER,
    baseline TEXT,
    description TEXT,
    catalog_path TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Cross-framework control mapping
CREATE TABLE IF NOT EXISTS control_crosswalk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nist_800_53_id TEXT NOT NULL,
    framework_id TEXT NOT NULL REFERENCES framework_profiles(id),
    framework_control_id TEXT NOT NULL,
    mapping_type TEXT DEFAULT 'equivalent' CHECK(mapping_type IN ('equivalent', 'partial', 'overlay', 'additional')),
    notes TEXT,
    UNIQUE(nist_800_53_id, framework_id)
);

CREATE INDEX IF NOT EXISTS idx_crosswalk_nist ON control_crosswalk(nist_800_53_id);
CREATE INDEX IF NOT EXISTS idx_crosswalk_framework ON control_crosswalk(framework_id);

-- Per-project framework compliance status
CREATE TABLE IF NOT EXISTS project_framework_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    framework_id TEXT NOT NULL REFERENCES framework_profiles(id),
    target_baseline TEXT,
    total_controls INTEGER DEFAULT 0,
    implemented_count INTEGER DEFAULT 0,
    partially_implemented_count INTEGER DEFAULT 0,
    planned_count INTEGER DEFAULT 0,
    not_applicable_count INTEGER DEFAULT 0,
    coverage_pct REAL DEFAULT 0.0,
    gate_status TEXT DEFAULT 'incomplete' CHECK(gate_status IN ('pass', 'fail', 'incomplete', 'waived')),
    last_assessed TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, framework_id)
);

-- FedRAMP assessment results
CREATE TABLE IF NOT EXISTS fedramp_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    baseline TEXT NOT NULL CHECK(baseline IN ('moderate', 'high')),
    control_id TEXT NOT NULL,
    status TEXT DEFAULT 'not_assessed' CHECK(status IN ('not_assessed', 'satisfied', 'other_than_satisfied', 'not_applicable', 'risk_accepted')),
    implementation_status TEXT,
    customer_responsible TEXT,
    evidence_description TEXT,
    evidence_path TEXT,
    automation_result TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, baseline, control_id)
);

CREATE INDEX IF NOT EXISTS idx_fedramp_project ON fedramp_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_fedramp_baseline ON fedramp_assessments(baseline);

-- CMMC practice assessment results
CREATE TABLE IF NOT EXISTS cmmc_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    level INTEGER NOT NULL CHECK(level IN (2, 3)),
    practice_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    status TEXT DEFAULT 'not_assessed' CHECK(status IN ('not_assessed', 'met', 'not_met', 'partially_met', 'not_applicable')),
    evidence_description TEXT,
    evidence_path TEXT,
    automation_result TEXT,
    nist_171_id TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, practice_id)
);

CREATE INDEX IF NOT EXISTS idx_cmmc_project ON cmmc_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_cmmc_level ON cmmc_assessments(level);

-- OSCAL artifact tracking
CREATE TABLE IF NOT EXISTS oscal_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    artifact_type TEXT NOT NULL CHECK(artifact_type IN ('ssp', 'poam', 'assessment_results', 'assessment_plan', 'component_definition', 'catalog', 'profile')),
    oscal_version TEXT DEFAULT '1.1.2',
    format TEXT DEFAULT 'json' CHECK(format IN ('json', 'xml', 'yaml')),
    file_path TEXT NOT NULL,
    file_hash TEXT,
    schema_valid INTEGER DEFAULT 0,
    validation_errors TEXT,
    generated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    UNIQUE(project_id, artifact_type, format)
);

CREATE INDEX IF NOT EXISTS idx_oscal_project ON oscal_artifacts(project_id);

-- eMASS system registration and sync
CREATE TABLE IF NOT EXISTS emass_systems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT UNIQUE NOT NULL REFERENCES projects(id),
    emass_system_id TEXT,
    system_name TEXT,
    emass_org_id TEXT,
    ditpr_id TEXT,
    registration_type TEXT,
    impact_level TEXT CHECK(impact_level IN ('IL2', 'IL4', 'IL5', 'IL6')),
    authorization_status TEXT CHECK(authorization_status IN ('not_yet_authorized', 'ato', 'iato', 'dato', 'cato', 'denied', 'decommissioned')),
    authorization_date TEXT,
    authorization_expiry TEXT,
    authorization_termination_date TEXT,
    authorizing_official TEXT,
    last_sync TEXT,
    last_sync_status TEXT,
    sync_status TEXT DEFAULT 'never' CHECK(sync_status IN ('never', 'success', 'partial', 'failed')),
    sync_mode TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- eMASS sync history log
CREATE TABLE IF NOT EXISTS emass_sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    sync_direction TEXT NOT NULL CHECK(sync_direction IN ('push', 'pull', 'bidirectional')),
    sync_mode TEXT,
    sync_status TEXT,
    artifact_type TEXT,
    status TEXT NOT NULL CHECK(status IN ('started', 'success', 'partial', 'failed')),
    items_synced INTEGER DEFAULT 0,
    items_failed INTEGER DEFAULT 0,
    controls_synced INTEGER,
    poam_synced INTEGER,
    artifacts_synced INTEGER,
    test_results_synced INTEGER,
    error_details TEXT,
    error_message TEXT,
    details TEXT,
    sync_duration_ms INTEGER,
    started_at TEXT,
    completed_at TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_emass_sync_project ON emass_sync_log(project_id);

-- cATO continuous evidence tracking
CREATE TABLE IF NOT EXISTS cato_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    control_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL CHECK(evidence_type IN ('scan_result', 'test_result', 'config_check', 'manual_review', 'attestation', 'artifact')),
    evidence_source TEXT NOT NULL,
    evidence_path TEXT,
    evidence_hash TEXT,
    collected_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT,
    is_fresh INTEGER DEFAULT 1,
    freshness_check_at TEXT,
    status TEXT DEFAULT 'current' CHECK(status IN ('current', 'stale', 'expired', 'superseded')),
    automation_frequency TEXT CHECK(automation_frequency IN ('continuous', 'daily', 'weekly', 'monthly', 'per_change', 'manual')),
    UNIQUE(project_id, control_id, evidence_type, evidence_source)
);

CREATE INDEX IF NOT EXISTS idx_cato_evidence_project ON cato_evidence(project_id);
CREATE INDEX IF NOT EXISTS idx_cato_evidence_status ON cato_evidence(status);

-- SAFe PI compliance tracking
CREATE TABLE IF NOT EXISTS pi_compliance_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    pi_number TEXT NOT NULL,
    pi_start_date TEXT,
    pi_end_date TEXT,
    compliance_score_start REAL,
    compliance_score_end REAL,
    controls_implemented INTEGER DEFAULT 0,
    controls_remaining INTEGER DEFAULT 0,
    poam_items_closed INTEGER DEFAULT 0,
    poam_items_opened INTEGER DEFAULT 0,
    findings_remediated INTEGER DEFAULT 0,
    artifacts_generated TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, pi_number)
);

-- ============================================================
-- MBSE INTEGRATION (Phase 18A)
-- ============================================================

-- SysML model elements imported from Cameo XMI
CREATE TABLE IF NOT EXISTS sysml_elements (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    xmi_id TEXT NOT NULL,
    element_type TEXT NOT NULL CHECK(element_type IN (
        'block', 'interface_block', 'value_type', 'constraint_block',
        'activity', 'action', 'object_node', 'control_flow', 'object_flow',
        'requirement', 'use_case', 'actor', 'state_machine', 'state',
        'package', 'profile', 'stereotype', 'port', 'connector'
    )),
    name TEXT NOT NULL,
    qualified_name TEXT,
    parent_id TEXT REFERENCES sysml_elements(id),
    stereotype TEXT,
    description TEXT,
    properties TEXT,
    diagram_type TEXT CHECK(diagram_type IN ('bdd', 'ibd', 'act', 'stm', 'uc', 'req', 'pkg', NULL)),
    source_file TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    imported_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, xmi_id)
);

CREATE INDEX IF NOT EXISTS idx_sysml_project ON sysml_elements(project_id);
CREATE INDEX IF NOT EXISTS idx_sysml_type ON sysml_elements(element_type);
CREATE INDEX IF NOT EXISTS idx_sysml_parent ON sysml_elements(parent_id);

-- SysML relationships between elements
CREATE TABLE IF NOT EXISTS sysml_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    source_element_id TEXT NOT NULL REFERENCES sysml_elements(id),
    target_element_id TEXT NOT NULL REFERENCES sysml_elements(id),
    relationship_type TEXT NOT NULL CHECK(relationship_type IN (
        'association', 'composition', 'aggregation', 'generalization',
        'dependency', 'realization', 'usage', 'allocate',
        'satisfy', 'derive', 'verify', 'refine', 'trace', 'copy'
    )),
    name TEXT,
    properties TEXT,
    source_file TEXT,
    UNIQUE(project_id, source_element_id, target_element_id, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_sysml_rel_project ON sysml_relationships(project_id);
CREATE INDEX IF NOT EXISTS idx_sysml_rel_source ON sysml_relationships(source_element_id);
CREATE INDEX IF NOT EXISTS idx_sysml_rel_target ON sysml_relationships(target_element_id);
CREATE INDEX IF NOT EXISTS idx_sysml_rel_type ON sysml_relationships(relationship_type);

-- DOORS NG requirements imported via ReqIF
CREATE TABLE IF NOT EXISTS doors_requirements (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    doors_id TEXT NOT NULL,
    module_name TEXT,
    requirement_type TEXT CHECK(requirement_type IN ('functional', 'non_functional', 'interface', 'design', 'security', 'performance', 'constraint')),
    title TEXT NOT NULL,
    description TEXT,
    priority TEXT CHECK(priority IN ('critical', 'high', 'medium', 'low')),
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'approved', 'implemented', 'verified', 'deleted', 'deferred')),
    parent_req_id TEXT,
    source_file TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    imported_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, doors_id)
);

CREATE INDEX IF NOT EXISTS idx_doors_project ON doors_requirements(project_id);
CREATE INDEX IF NOT EXISTS idx_doors_type ON doors_requirements(requirement_type);
CREATE INDEX IF NOT EXISTS idx_doors_status ON doors_requirements(status);

-- Digital thread traceability links (N:M)
CREATE TABLE IF NOT EXISTS digital_thread_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    source_type TEXT NOT NULL CHECK(source_type IN ('doors_requirement', 'sysml_element', 'code_module', 'test_file', 'nist_control', 'stig_rule', 'compliance_artifact', 'legacy_component', 'migration_task', 'intake_requirement', 'safe_item', 'coa_definition', 'uat_test')),
    source_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK(target_type IN ('doors_requirement', 'sysml_element', 'code_module', 'test_file', 'nist_control', 'stig_rule', 'compliance_artifact', 'legacy_component', 'migration_task', 'intake_requirement', 'safe_item', 'coa_definition', 'uat_test')),
    target_id TEXT NOT NULL,
    link_type TEXT NOT NULL CHECK(link_type IN ('satisfies', 'derives_from', 'implements', 'verifies', 'traces_to', 'allocates', 'refines', 'maps_to', 'replaces', 'migrates_to', 'decomposes_into', 'assessed_against', 'approved_for')),
    confidence REAL DEFAULT 1.0 CHECK(confidence >= 0.0 AND confidence <= 1.0),
    evidence TEXT,
    created_by TEXT DEFAULT 'icdev-mbse-engine',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, source_type, source_id, target_type, target_id, link_type)
);

CREATE INDEX IF NOT EXISTS idx_thread_project ON digital_thread_links(project_id);
CREATE INDEX IF NOT EXISTS idx_thread_source ON digital_thread_links(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_thread_target ON digital_thread_links(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_thread_link_type ON digital_thread_links(link_type);

-- Model import history log
CREATE TABLE IF NOT EXISTS model_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    import_type TEXT NOT NULL CHECK(import_type IN ('xmi', 'reqif', 'csv', 'json')),
    source_file TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    elements_imported INTEGER DEFAULT 0,
    relationships_imported INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    error_details TEXT,
    status TEXT DEFAULT 'completed' CHECK(status IN ('in_progress', 'completed', 'failed', 'partial')),
    imported_by TEXT DEFAULT 'icdev-mbse-engine',
    imported_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_imports_project ON model_imports(project_id);

-- PI-cadenced model snapshots
CREATE TABLE IF NOT EXISTS model_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    pi_number TEXT,
    snapshot_type TEXT NOT NULL CHECK(snapshot_type IN ('pi_start', 'pi_end', 'baseline', 'milestone', 'manual')),
    element_count INTEGER DEFAULT 0,
    relationship_count INTEGER DEFAULT 0,
    requirement_count INTEGER DEFAULT 0,
    thread_link_count INTEGER DEFAULT 0,
    content_hash TEXT NOT NULL,
    snapshot_data TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, pi_number, snapshot_type)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_project ON model_snapshots(project_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_pi ON model_snapshots(pi_number);

-- Model-to-code mapping with sync tracking
CREATE TABLE IF NOT EXISTS model_code_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    sysml_element_id TEXT NOT NULL REFERENCES sysml_elements(id),
    code_path TEXT NOT NULL,
    code_type TEXT NOT NULL CHECK(code_type IN ('class', 'module', 'function', 'interface', 'api_endpoint', 'config', 'test', 'migration')),
    mapping_direction TEXT DEFAULT 'model_to_code' CHECK(mapping_direction IN ('model_to_code', 'code_to_model', 'bidirectional')),
    sync_status TEXT DEFAULT 'synced' CHECK(sync_status IN ('synced', 'model_ahead', 'code_ahead', 'conflict', 'unknown')),
    last_synced TEXT DEFAULT (datetime('now')),
    model_hash TEXT,
    code_hash TEXT,
    UNIQUE(project_id, sysml_element_id, code_path)
);

CREATE INDEX IF NOT EXISTS idx_mcm_project ON model_code_mappings(project_id);
CREATE INDEX IF NOT EXISTS idx_mcm_element ON model_code_mappings(sysml_element_id);

-- DES (DoDI 5000.87) compliance tracking
CREATE TABLE IF NOT EXISTS des_compliance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    requirement_id TEXT NOT NULL,
    requirement_title TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('model_authority', 'data_management', 'infrastructure', 'workforce', 'policy', 'lifecycle')),
    status TEXT DEFAULT 'not_assessed' CHECK(status IN ('not_assessed', 'compliant', 'partially_compliant', 'non_compliant', 'not_applicable')),
    evidence TEXT,
    automation_result TEXT,
    assessed_at TEXT DEFAULT (datetime('now')),
    notes TEXT,
    UNIQUE(project_id, requirement_id)
);

CREATE INDEX IF NOT EXISTS idx_des_project ON des_compliance(project_id);

-- ============================================================
-- APPLICATION MODERNIZATION (Phase 19A — 7Rs Migration)
-- ============================================================

-- Legacy applications registered for analysis
CREATE TABLE IF NOT EXISTS legacy_applications (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    description TEXT,
    source_path TEXT NOT NULL,
    primary_language TEXT NOT NULL,
    language_version TEXT,
    framework TEXT,
    framework_version TEXT,
    app_type TEXT DEFAULT 'monolith' CHECK(app_type IN ('monolith','distributed','client_server','mainframe','embedded')),
    analysis_status TEXT DEFAULT 'registered' CHECK(analysis_status IN ('registered','analyzing','analyzed','planning','migrating','completed','failed')),
    loc_total INTEGER DEFAULT 0,
    loc_code INTEGER DEFAULT 0,
    loc_comment INTEGER DEFAULT 0,
    loc_blank INTEGER DEFAULT 0,
    file_count INTEGER DEFAULT 0,
    complexity_score REAL DEFAULT 0.0,
    tech_debt_hours REAL DEFAULT 0.0,
    maintainability_index REAL DEFAULT 0.0,
    source_hash TEXT,
    registered_at TEXT DEFAULT (datetime('now')),
    analyzed_at TEXT,
    UNIQUE(project_id, name)
);
CREATE INDEX IF NOT EXISTS idx_legacy_app_project ON legacy_applications(project_id);
CREATE INDEX IF NOT EXISTS idx_legacy_app_status ON legacy_applications(analysis_status);

-- Legacy application components (classes, modules, services)
CREATE TABLE IF NOT EXISTS legacy_components (
    id TEXT PRIMARY KEY,
    legacy_app_id TEXT NOT NULL REFERENCES legacy_applications(id),
    name TEXT NOT NULL,
    component_type TEXT NOT NULL CHECK(component_type IN (
        'class','module','package','service','controller','model',
        'view','repository','util','config','test','migration',
        'interface','abstract_class','enum','servlet','ejb','entity',
        'stored_procedure','trigger','function','api_endpoint'
    )),
    file_path TEXT NOT NULL,
    qualified_name TEXT,
    parent_component_id TEXT REFERENCES legacy_components(id),
    loc INTEGER DEFAULT 0,
    cyclomatic_complexity REAL DEFAULT 0.0,
    coupling_score REAL DEFAULT 0.0,
    cohesion_score REAL DEFAULT 0.0,
    dependencies_in INTEGER DEFAULT 0,
    dependencies_out INTEGER DEFAULT 0,
    properties TEXT,
    discovered_at TEXT DEFAULT (datetime('now')),
    UNIQUE(legacy_app_id, qualified_name)
);
CREATE INDEX IF NOT EXISTS idx_legacy_comp_app ON legacy_components(legacy_app_id);
CREATE INDEX IF NOT EXISTS idx_legacy_comp_type ON legacy_components(component_type);
CREATE INDEX IF NOT EXISTS idx_legacy_comp_parent ON legacy_components(parent_component_id);

-- Dependencies between legacy components
CREATE TABLE IF NOT EXISTS legacy_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_app_id TEXT NOT NULL REFERENCES legacy_applications(id),
    source_component_id TEXT NOT NULL REFERENCES legacy_components(id),
    target_component_id TEXT REFERENCES legacy_components(id),
    dependency_type TEXT NOT NULL CHECK(dependency_type IN (
        'import','inheritance','composition','aggregation','method_call',
        'field_access','annotation','injection','event','database','api_call',
        'file_io','message_queue','external_service'
    )),
    weight REAL DEFAULT 1.0,
    is_bidirectional INTEGER DEFAULT 0,
    evidence TEXT,
    UNIQUE(legacy_app_id, source_component_id, target_component_id, dependency_type)
);
CREATE INDEX IF NOT EXISTS idx_legacy_dep_app ON legacy_dependencies(legacy_app_id);
CREATE INDEX IF NOT EXISTS idx_legacy_dep_source ON legacy_dependencies(source_component_id);
CREATE INDEX IF NOT EXISTS idx_legacy_dep_target ON legacy_dependencies(target_component_id);

-- Discovered API endpoints in legacy applications
CREATE TABLE IF NOT EXISTS legacy_apis (
    id TEXT PRIMARY KEY,
    legacy_app_id TEXT NOT NULL REFERENCES legacy_applications(id),
    component_id TEXT REFERENCES legacy_components(id),
    method TEXT CHECK(method IN ('GET','POST','PUT','DELETE','PATCH','HEAD','OPTIONS','ALL')),
    path TEXT NOT NULL,
    handler_function TEXT,
    parameters TEXT,
    request_body TEXT,
    response_type TEXT,
    auth_required INTEGER DEFAULT 0,
    discovered_at TEXT DEFAULT (datetime('now')),
    UNIQUE(legacy_app_id, method, path)
);
CREATE INDEX IF NOT EXISTS idx_legacy_api_app ON legacy_apis(legacy_app_id);

-- Discovered database schemas in legacy applications
CREATE TABLE IF NOT EXISTS legacy_db_schemas (
    id TEXT PRIMARY KEY,
    legacy_app_id TEXT NOT NULL REFERENCES legacy_applications(id),
    db_type TEXT NOT NULL CHECK(db_type IN ('postgresql','mysql','oracle','mssql','db2','sybase','sqlite','h2','derby')),
    schema_name TEXT DEFAULT 'public',
    table_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    data_type TEXT NOT NULL,
    is_nullable INTEGER DEFAULT 1,
    is_primary_key INTEGER DEFAULT 0,
    is_foreign_key INTEGER DEFAULT 0,
    foreign_table TEXT,
    foreign_column TEXT,
    default_value TEXT,
    constraints TEXT,
    discovered_at TEXT DEFAULT (datetime('now')),
    UNIQUE(legacy_app_id, schema_name, table_name, column_name)
);
CREATE INDEX IF NOT EXISTS idx_legacy_db_app ON legacy_db_schemas(legacy_app_id);
CREATE INDEX IF NOT EXISTS idx_legacy_db_table ON legacy_db_schemas(table_name);

-- 7R migration assessment scoring
CREATE TABLE IF NOT EXISTS migration_assessments (
    id TEXT PRIMARY KEY,
    legacy_app_id TEXT NOT NULL REFERENCES legacy_applications(id),
    component_id TEXT REFERENCES legacy_components(id),
    assessment_scope TEXT DEFAULT 'application' CHECK(assessment_scope IN ('application','component','database','api')),
    rehost_score REAL DEFAULT 0.0,
    replatform_score REAL DEFAULT 0.0,
    refactor_score REAL DEFAULT 0.0,
    rearchitect_score REAL DEFAULT 0.0,
    repurchase_score REAL DEFAULT 0.0,
    retire_score REAL DEFAULT 0.0,
    retain_score REAL DEFAULT 0.0,
    recommended_strategy TEXT CHECK(recommended_strategy IN ('rehost','replatform','refactor','rearchitect','repurchase','retire','retain')),
    cost_estimate_hours REAL,
    risk_score REAL DEFAULT 0.0,
    timeline_weeks INTEGER,
    ato_impact TEXT CHECK(ato_impact IN ('none','low','medium','high','critical')),
    tech_debt_reduction REAL DEFAULT 0.0,
    scoring_weights TEXT,
    evidence TEXT,
    assessed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(legacy_app_id, component_id, assessment_scope)
);
CREATE INDEX IF NOT EXISTS idx_migration_assess_app ON migration_assessments(legacy_app_id);
CREATE INDEX IF NOT EXISTS idx_migration_assess_strategy ON migration_assessments(recommended_strategy);

-- Migration plans
CREATE TABLE IF NOT EXISTS migration_plans (
    id TEXT PRIMARY KEY,
    legacy_app_id TEXT NOT NULL REFERENCES legacy_applications(id),
    plan_name TEXT NOT NULL,
    strategy TEXT NOT NULL CHECK(strategy IN ('rehost','replatform','refactor','rearchitect','repurchase','retire','retain','hybrid')),
    target_language TEXT,
    target_framework TEXT,
    target_database TEXT,
    target_architecture TEXT CHECK(target_architecture IN ('microservices','modular_monolith','serverless','event_driven','layered','hexagonal')),
    migration_approach TEXT DEFAULT 'strangler_fig' CHECK(migration_approach IN ('big_bang','strangler_fig','parallel_run','blue_green','canary','phased')),
    total_tasks INTEGER DEFAULT 0,
    completed_tasks INTEGER DEFAULT 0,
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft','approved','in_progress','paused','completed','cancelled')),
    estimated_hours REAL,
    actual_hours REAL DEFAULT 0.0,
    start_date TEXT,
    target_date TEXT,
    completion_date TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(legacy_app_id, plan_name)
);
CREATE INDEX IF NOT EXISTS idx_migration_plan_app ON migration_plans(legacy_app_id);
CREATE INDEX IF NOT EXISTS idx_migration_plan_status ON migration_plans(status);

-- Individual migration tasks within a plan
CREATE TABLE IF NOT EXISTS migration_tasks (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES migration_plans(id),
    legacy_component_id TEXT REFERENCES legacy_components(id),
    task_type TEXT NOT NULL CHECK(task_type IN (
        'analyze','document','decompose','generate_scaffold',
        'generate_adapter','generate_facade','generate_test',
        'migrate_schema','migrate_data','upgrade_version',
        'upgrade_framework','extract_service','create_api',
        'create_acl','validate','deploy','cutover','decommission'
    )),
    title TEXT NOT NULL,
    description TEXT,
    priority TEXT DEFAULT 'medium' CHECK(priority IN ('critical','high','medium','low')),
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','in_progress','completed','blocked','skipped')),
    pi_number TEXT,
    assigned_to TEXT,
    estimated_hours REAL,
    actual_hours REAL DEFAULT 0.0,
    dependencies TEXT,
    output_path TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_migration_task_plan ON migration_tasks(plan_id);
CREATE INDEX IF NOT EXISTS idx_migration_task_status ON migration_tasks(status);
CREATE INDEX IF NOT EXISTS idx_migration_task_pi ON migration_tasks(pi_number);

-- Migration artifacts (generated files)
CREATE TABLE IF NOT EXISTS migration_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL REFERENCES migration_plans(id),
    task_id TEXT REFERENCES migration_tasks(id),
    artifact_type TEXT NOT NULL CHECK(artifact_type IN (
        'architecture_doc','api_doc','data_flow_doc','component_doc',
        'migration_script','adapter_code','facade_code','scaffold_code',
        'test_code','schema_ddl','data_migration_sql','acl_code',
        'deployment_manifest','rollback_script','validation_report',
        'assessment_report','progress_report'
    )),
    file_path TEXT NOT NULL,
    file_hash TEXT,
    description TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_migration_artifact_plan ON migration_artifacts(plan_id);
CREATE INDEX IF NOT EXISTS idx_migration_artifact_type ON migration_artifacts(artifact_type);

-- Migration progress snapshots (PI-cadenced)
CREATE TABLE IF NOT EXISTS migration_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL REFERENCES migration_plans(id),
    pi_number TEXT,
    snapshot_type TEXT DEFAULT 'manual' CHECK(snapshot_type IN ('pi_start','pi_end','milestone','manual')),
    tasks_total INTEGER DEFAULT 0,
    tasks_completed INTEGER DEFAULT 0,
    tasks_in_progress INTEGER DEFAULT 0,
    tasks_blocked INTEGER DEFAULT 0,
    components_migrated INTEGER DEFAULT 0,
    components_remaining INTEGER DEFAULT 0,
    apis_migrated INTEGER DEFAULT 0,
    tables_migrated INTEGER DEFAULT 0,
    test_coverage REAL DEFAULT 0.0,
    compliance_score REAL DEFAULT 0.0,
    hours_spent REAL DEFAULT 0.0,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(plan_id, pi_number, snapshot_type)
);
CREATE INDEX IF NOT EXISTS idx_migration_progress_plan ON migration_progress(plan_id);
CREATE INDEX IF NOT EXISTS idx_migration_progress_pi ON migration_progress(pi_number);

-- ============================================================
-- ALERTS
-- ============================================================
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT REFERENCES projects(id),
    severity TEXT NOT NULL CHECK(severity IN ('critical', 'warning', 'info')),
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'firing' CHECK(status IN ('firing', 'acknowledged', 'resolved')),
    acknowledged_by TEXT,
    resolved_at TIMESTAMP,
    auto_healed BOOLEAN DEFAULT FALSE,
    healing_event_id INTEGER REFERENCES self_healing_events(id),
    watchcon_tier INTEGER DEFAULT 4 CHECK(watchcon_tier IN (2,3,4)),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- RICOAS: REQUIREMENTS INTAKE (Phase 20A)
-- ============================================================

CREATE TABLE IF NOT EXISTS intake_sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    customer_name TEXT NOT NULL,
    customer_org TEXT,
    session_status TEXT DEFAULT 'active'
        CHECK(session_status IN ('active', 'paused', 'completed', 'abandoned', 'approved')),
    classification TEXT DEFAULT 'CUI',
    impact_level TEXT DEFAULT 'IL5'
        CHECK(impact_level IN ('IL2', 'IL4', 'IL5', 'IL6')),
    readiness_score REAL DEFAULT 0.0,
    readiness_breakdown TEXT,
    gap_count INTEGER DEFAULT 0,
    ambiguity_count INTEGER DEFAULT 0,
    total_requirements INTEGER DEFAULT 0,
    decomposed_count INTEGER DEFAULT 0,
    context_summary TEXT,
    source_documents TEXT,
    resumed_from TEXT REFERENCES intake_sessions(id),
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_intake_session_project ON intake_sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_intake_session_status ON intake_sessions(session_status);

CREATE TABLE IF NOT EXISTS intake_conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES intake_sessions(id),
    turn_number INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('customer', 'analyst', 'system')),
    content TEXT NOT NULL,
    content_type TEXT DEFAULT 'text'
        CHECK(content_type IN ('text', 'clarification_request', 'gap_detection',
            'requirement_extracted', 'decomposition_preview', 'readiness_update',
            'document_upload', 'document_extraction', 'coa_preview',
            'boundary_warning', 'approval_request')),
    extracted_requirements TEXT,
    metadata TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_intake_conv_session ON intake_conversation(session_id);
CREATE INDEX IF NOT EXISTS idx_intake_conv_turn ON intake_conversation(session_id, turn_number);

CREATE TABLE IF NOT EXISTS intake_requirements (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES intake_sessions(id),
    project_id TEXT REFERENCES projects(id),
    source_turn INTEGER,
    raw_text TEXT NOT NULL,
    refined_text TEXT,
    requirement_type TEXT DEFAULT 'functional'
        CHECK(requirement_type IN ('functional', 'non_functional', 'interface',
            'security', 'performance', 'compliance', 'data', 'constraint',
            'operational', 'transitional')),
    priority TEXT DEFAULT 'medium'
        CHECK(priority IN ('critical', 'high', 'medium', 'low')),
    status TEXT DEFAULT 'draft'
        CHECK(status IN ('draft', 'clarified', 'validated', 'approved', 'rejected',
            'decomposed', 'deferred')),
    clarity_score REAL DEFAULT 0.0,
    completeness_score REAL DEFAULT 0.0,
    testability_score REAL DEFAULT 0.0,
    feasibility_score REAL DEFAULT 0.0,
    compliance_impact TEXT,
    gaps TEXT,
    ambiguities TEXT,
    acceptance_criteria TEXT,
    source_document TEXT,
    source_section TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_intake_req_session ON intake_requirements(session_id);
CREATE INDEX IF NOT EXISTS idx_intake_req_project ON intake_requirements(project_id);
CREATE INDEX IF NOT EXISTS idx_intake_req_status ON intake_requirements(status);
CREATE INDEX IF NOT EXISTS idx_intake_req_type ON intake_requirements(requirement_type);

CREATE TABLE IF NOT EXISTS safe_decomposition (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES intake_sessions(id),
    project_id TEXT REFERENCES projects(id),
    parent_id TEXT REFERENCES safe_decomposition(id),
    level TEXT NOT NULL
        CHECK(level IN ('epic', 'capability', 'feature', 'story', 'enabler')),
    title TEXT NOT NULL,
    description TEXT,
    acceptance_criteria TEXT,
    story_points INTEGER,
    t_shirt_size TEXT CHECK(t_shirt_size IN ('XS', 'S', 'M', 'L', 'XL', 'XXL')),
    pi_target TEXT,
    team TEXT,
    wsjf_score REAL,
    source_requirement_ids TEXT,
    nist_controls TEXT,
    ato_impact_tier TEXT CHECK(ato_impact_tier IN ('GREEN', 'YELLOW', 'ORANGE', 'RED')),
    status TEXT DEFAULT 'draft'
        CHECK(status IN ('draft', 'refined', 'approved', 'committed', 'in_progress', 'done', 'rejected')),
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_safe_decomp_session ON safe_decomposition(session_id);
CREATE INDEX IF NOT EXISTS idx_safe_decomp_parent ON safe_decomposition(parent_id);
CREATE INDEX IF NOT EXISTS idx_safe_decomp_level ON safe_decomposition(level);
CREATE INDEX IF NOT EXISTS idx_safe_decomp_project ON safe_decomposition(project_id);

CREATE TABLE IF NOT EXISTS intake_documents (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES intake_sessions(id),
    document_type TEXT NOT NULL
        CHECK(document_type IN ('sow', 'cdd', 'conops', 'srd', 'icd', 'ssp',
            'use_case', 'brd', 'urd', 'rfp', 'rfi', 'other')),
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    file_size_bytes INTEGER,
    mime_type TEXT,
    extraction_status TEXT DEFAULT 'pending'
        CHECK(extraction_status IN ('pending', 'extracting', 'extracted', 'failed')),
    extracted_sections TEXT,
    extracted_requirements_count INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI',
    uploaded_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_intake_doc_session ON intake_documents(session_id);

CREATE TABLE IF NOT EXISTS readiness_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES intake_sessions(id),
    turn_number INTEGER,
    overall_score REAL NOT NULL,
    completeness REAL NOT NULL,
    clarity REAL NOT NULL,
    feasibility REAL NOT NULL,
    compliance REAL NOT NULL,
    testability REAL NOT NULL,
    gap_count INTEGER DEFAULT 0,
    ambiguity_count INTEGER DEFAULT 0,
    requirement_count INTEGER DEFAULT 0,
    scored_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_readiness_session ON readiness_scores(session_id);

-- ============================================================
-- RICOAS: ATO BOUNDARY & SUPPLY CHAIN (Phase 20B)
-- ============================================================

CREATE TABLE IF NOT EXISTS ato_system_registry (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    system_name TEXT NOT NULL,
    system_acronym TEXT,
    ato_type TEXT CHECK(ato_type IN ('ato', 'iato', 'dato', 'cato')),
    ato_date TEXT,
    ato_expiry TEXT,
    authorizing_official TEXT,
    accreditation_boundary TEXT,
    ssp_document_id INTEGER REFERENCES ssp_documents(id),
    impact_level TEXT CHECK(impact_level IN ('IL2', 'IL4', 'IL5', 'IL6')),
    data_types TEXT,
    interconnections TEXT,
    baseline_controls TEXT,
    component_inventory TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, system_name)
);
CREATE INDEX IF NOT EXISTS idx_ato_registry_project ON ato_system_registry(project_id);

CREATE TABLE IF NOT EXISTS boundary_impact_assessments (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES intake_sessions(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    system_id TEXT NOT NULL REFERENCES ato_system_registry(id),
    requirement_id TEXT REFERENCES intake_requirements(id),
    safe_item_id TEXT REFERENCES safe_decomposition(id),
    impact_tier TEXT NOT NULL CHECK(impact_tier IN ('GREEN', 'YELLOW', 'ORANGE', 'RED')),
    impact_category TEXT NOT NULL
        CHECK(impact_category IN ('architecture', 'data_flow', 'authentication',
            'authorization', 'network', 'encryption', 'logging', 'boundary_change',
            'new_interconnection', 'data_type_change', 'component_addition')),
    impact_description TEXT NOT NULL,
    affected_controls TEXT,
    affected_components TEXT,
    ssp_sections_impacted TEXT,
    remediation_required TEXT,
    alternative_approach TEXT,
    risk_score REAL DEFAULT 0.0,
    assessed_by TEXT DEFAULT 'icdev-requirements-analyst',
    assessed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(requirement_id, system_id)
);
CREATE INDEX IF NOT EXISTS idx_bia_project ON boundary_impact_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_bia_tier ON boundary_impact_assessments(impact_tier);

CREATE TABLE IF NOT EXISTS supply_chain_vendors (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    vendor_name TEXT NOT NULL,
    -- 'defense_contractor' comes from migration 050_theater_supply_chain, which
    -- is shadowed by 050_sg_sio_assessments and so has never run (mvs-audit-03).
    -- PostgreSQL has the widened constraint via pg_consolidated.sql; SQLite gets
    -- it here rather than from the migration, because widening a CHECK on SQLite
    -- means rebuilding the table (create _new, copy, DROP, rename) and that is a
    -- destructive operation to fix a non-destructive problem.
    vendor_type TEXT CHECK(vendor_type IN ('cots', 'gots', 'oss', 'saas', 'paas', 'iaas', 'contractor', 'subcontractor', 'defense_contractor')),
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
CREATE INDEX IF NOT EXISTS idx_scv_project ON supply_chain_vendors(project_id);
CREATE INDEX IF NOT EXISTS idx_scv_risk ON supply_chain_vendors(scrm_risk_tier);

CREATE TABLE IF NOT EXISTS supply_chain_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
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
CREATE INDEX IF NOT EXISTS idx_scd_source ON supply_chain_dependencies(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_scd_target ON supply_chain_dependencies(target_type, target_id);

CREATE TABLE IF NOT EXISTS isa_agreements (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
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
    document_path TEXT,
    review_cadence_days INTEGER DEFAULT 365,
    next_review_date TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_isa_project ON isa_agreements(project_id);
CREATE INDEX IF NOT EXISTS idx_isa_status ON isa_agreements(status);
CREATE INDEX IF NOT EXISTS idx_isa_expiry ON isa_agreements(expiry_date);

CREATE TABLE IF NOT EXISTS scrm_assessments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    vendor_id TEXT REFERENCES supply_chain_vendors(id),
    package_name TEXT,
    assessment_type TEXT NOT NULL
        CHECK(assessment_type IN ('vendor', 'component', 'aggregate', 'supply_chain_event')),
    risk_category TEXT
        CHECK(risk_category IN ('tampering', 'counterfeit', 'malicious_insertion',
            'supply_disruption', 'data_exposure', 'foreign_control',
            'single_source', 'obsolescence')),
    risk_score REAL DEFAULT 0.0,
    likelihood TEXT CHECK(likelihood IN ('very_low', 'low', 'moderate', 'high', 'very_high')),
    impact TEXT CHECK(impact IN ('very_low', 'low', 'moderate', 'high', 'very_high')),
    mitigations TEXT,
    residual_risk TEXT CHECK(residual_risk IN ('low', 'moderate', 'high', 'critical')),
    nist_161_controls TEXT,
    assessed_by TEXT DEFAULT 'icdev-supply-chain-agent',
    assessed_at TEXT DEFAULT (datetime('now')),
    next_assessment TEXT
);
CREATE INDEX IF NOT EXISTS idx_scrm_project ON scrm_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_scrm_risk ON scrm_assessments(residual_risk);

CREATE TABLE IF NOT EXISTS cve_triage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
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
CREATE INDEX IF NOT EXISTS idx_cve_triage_project ON cve_triage(project_id);
CREATE INDEX IF NOT EXISTS idx_cve_triage_severity ON cve_triage(severity);

-- Passive CVE Watcher — append-only processing log (NIST AU, SI-4, CA-7)
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
CREATE INDEX IF NOT EXISTS idx_cpwl_audit ON cve_passive_watch_log(audit_trail_id);
CREATE INDEX IF NOT EXISTS idx_cpwl_cve ON cve_passive_watch_log(cve_id);
CREATE INDEX IF NOT EXISTS idx_cpwl_project ON cve_passive_watch_log(project_id);

-- ============================================================
-- RICOAS: SIMULATION & COAs (Phase 20C)
-- ============================================================

CREATE TABLE IF NOT EXISTS simulation_scenarios (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT REFERENCES intake_sessions(id),
    scenario_name TEXT NOT NULL,
    scenario_type TEXT NOT NULL
        CHECK(scenario_type IN ('what_if', 'coa_comparison', 'risk_monte_carlo',
            'schedule_impact', 'cost_impact', 'compliance_impact',
            'supply_chain_disruption', 'architecture_change', 'compound')),
    base_state TEXT NOT NULL,
    modifications TEXT NOT NULL,
    status TEXT DEFAULT 'pending'
        CHECK(status IN ('pending', 'running', 'completed', 'failed', 'archived')),
    classification TEXT DEFAULT 'CUI',
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sim_scenario_project ON simulation_scenarios(project_id);

CREATE TABLE IF NOT EXISTS simulation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id TEXT NOT NULL REFERENCES simulation_scenarios(id),
    dimension TEXT NOT NULL
        CHECK(dimension IN ('architecture', 'compliance', 'supply_chain',
            'schedule', 'cost', 'risk', 'resource_allocation', 'quality')),
    metric_name TEXT NOT NULL,
    baseline_value REAL,
    simulated_value REAL,
    delta REAL,
    delta_pct REAL,
    confidence REAL DEFAULT 0.0,
    impact_tier TEXT CHECK(impact_tier IN ('GREEN', 'YELLOW', 'ORANGE', 'RED')),
    details TEXT,
    visualizations TEXT,
    calculated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sim_result_scenario ON simulation_results(scenario_id);

CREATE TABLE IF NOT EXISTS monte_carlo_runs (
    id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL REFERENCES simulation_scenarios(id),
    iterations INTEGER NOT NULL DEFAULT 10000,
    dimension TEXT NOT NULL CHECK(dimension IN ('schedule', 'cost', 'risk')),
    distribution_type TEXT DEFAULT 'pert'
        CHECK(distribution_type IN ('pert', 'triangular', 'normal', 'uniform', 'beta')),
    input_parameters TEXT NOT NULL,
    p10_value REAL,
    p50_value REAL,
    p80_value REAL,
    p90_value REAL,
    mean_value REAL,
    std_deviation REAL,
    histogram_data TEXT,
    cdf_data TEXT,
    confidence_intervals TEXT,
    run_duration_ms INTEGER,
    completed_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mc_scenario ON monte_carlo_runs(scenario_id);

CREATE TABLE IF NOT EXISTS coa_definitions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES intake_sessions(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    coa_type TEXT NOT NULL CHECK(coa_type IN ('speed', 'balanced', 'comprehensive', 'alternative')),
    coa_name TEXT NOT NULL,
    description TEXT,
    architecture_summary TEXT,
    cost_estimate TEXT,
    risk_profile TEXT,
    timeline TEXT,
    compliance_impact TEXT,
    supply_chain_impact TEXT,
    boundary_tier TEXT CHECK(boundary_tier IN ('GREEN', 'YELLOW', 'ORANGE', 'RED')),
    safe_decomposition_id TEXT,
    simulation_scenario_id TEXT REFERENCES simulation_scenarios(id),
    mission_fit_pct REAL,
    status TEXT DEFAULT 'draft'
        CHECK(status IN ('draft', 'simulated', 'presented', 'selected', 'rejected', 'archived')),
    selected_by TEXT,
    selected_at TEXT,
    selection_rationale TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_coa_session ON coa_definitions(session_id);
CREATE INDEX IF NOT EXISTS idx_coa_project ON coa_definitions(project_id);
CREATE INDEX IF NOT EXISTS idx_coa_status ON coa_definitions(status);

CREATE TABLE IF NOT EXISTS coa_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES intake_sessions(id),
    coa_a_id TEXT NOT NULL REFERENCES coa_definitions(id),
    coa_b_id TEXT NOT NULL REFERENCES coa_definitions(id),
    dimension TEXT NOT NULL
        CHECK(dimension IN ('architecture', 'compliance', 'supply_chain',
            'schedule', 'cost', 'risk', 'overall')),
    coa_a_score REAL,
    coa_b_score REAL,
    winner TEXT CHECK(winner IN ('coa_a', 'coa_b', 'tie')),
    rationale TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_coa_comp_session ON coa_comparisons(session_id);

-- ============================================================
-- RICOAS: EXTERNAL INTEGRATION (Phase 20D)
-- ============================================================

CREATE TABLE IF NOT EXISTS integration_connections (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    system_type TEXT NOT NULL
        CHECK(system_type IN ('jira', 'servicenow', 'doors_ng', 'confluence', 'azure_devops')),
    instance_url TEXT NOT NULL,
    auth_method TEXT NOT NULL
        CHECK(auth_method IN ('api_key', 'oauth2', 'pat', 'basic', 'pki', 'saml')),
    auth_secret_ref TEXT NOT NULL,
    sync_direction TEXT DEFAULT 'bidirectional'
        CHECK(sync_direction IN ('push', 'pull', 'bidirectional')),
    sync_status TEXT DEFAULT 'configured'
        CHECK(sync_status IN ('configured', 'syncing', 'synced', 'error', 'disabled')),
    last_sync TEXT,
    sync_cadence_minutes INTEGER DEFAULT 60,
    field_mapping TEXT NOT NULL,
    filter_criteria TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, system_type, instance_url)
);
CREATE INDEX IF NOT EXISTS idx_integ_conn_project ON integration_connections(project_id);

CREATE TABLE IF NOT EXISTS integration_sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id TEXT NOT NULL REFERENCES integration_connections(id),
    sync_direction TEXT NOT NULL CHECK(sync_direction IN ('push', 'pull')),
    items_synced INTEGER DEFAULT 0,
    items_created INTEGER DEFAULT 0,
    items_updated INTEGER DEFAULT 0,
    items_failed INTEGER DEFAULT 0,
    error_details TEXT,
    sync_duration_ms INTEGER,
    synced_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_integ_sync_conn ON integration_sync_log(connection_id);

CREATE TABLE IF NOT EXISTS integration_id_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id TEXT NOT NULL REFERENCES integration_connections(id),
    icdev_type TEXT NOT NULL
        CHECK(icdev_type IN ('intake_requirement', 'safe_decomposition', 'coa_definition',
            'boundary_impact_assessment', 'intake_session')),
    icdev_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    external_type TEXT,
    external_url TEXT,
    sync_status TEXT DEFAULT 'synced'
        CHECK(sync_status IN ('synced', 'pending_push', 'pending_pull', 'conflict', 'error')),
    last_synced TEXT DEFAULT (datetime('now')),
    UNIQUE(connection_id, icdev_id, icdev_type)
);
CREATE INDEX IF NOT EXISTS idx_integ_map_icdev ON integration_id_map(icdev_type, icdev_id);

CREATE TABLE IF NOT EXISTS approval_workflows (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES intake_sessions(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    approval_type TEXT NOT NULL
        CHECK(approval_type IN ('requirements_package', 'coa_selection',
            'boundary_impact_acceptance', 'decomposition_approval',
            'pi_commitment')),
    status TEXT DEFAULT 'pending'
        CHECK(status IN ('pending', 'in_review', 'approved', 'rejected',
            'conditional', 'escalated')),
    submitted_by TEXT NOT NULL,
    submitted_at TEXT DEFAULT (datetime('now')),
    reviewers TEXT NOT NULL,
    current_reviewer TEXT,
    approval_chain TEXT,
    related_coa_id TEXT REFERENCES coa_definitions(id),
    conditions TEXT,
    decision_rationale TEXT,
    decided_at TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_approval_session ON approval_workflows(session_id);
CREATE INDEX IF NOT EXISTS idx_approval_status ON approval_workflows(status);

CREATE TABLE IF NOT EXISTS review_traceability (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT REFERENCES intake_sessions(id),
    requirement_id TEXT NOT NULL,
    requirement_type TEXT NOT NULL
        CHECK(requirement_type IN ('intake', 'doors', 'safe_item')),
    sysml_element_ids TEXT,
    code_module_ids TEXT,
    test_file_ids TEXT,
    compliance_control_ids TEXT,
    uat_test_ids TEXT,
    coverage_pct REAL DEFAULT 0.0,
    gaps TEXT,
    last_verified TEXT,
    verified_by TEXT DEFAULT 'icdev-requirements-analyst',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, requirement_id, requirement_type)
);
CREATE INDEX IF NOT EXISTS idx_review_trace_project ON review_traceability(project_id);
CREATE INDEX IF NOT EXISTS idx_review_trace_req ON review_traceability(requirement_id);

-- ============================================================
-- HOOK-BASED OBSERVABILITY (Phase 39)
-- ============================================================

-- Hook event storage (append-only)
CREATE TABLE IF NOT EXISTS hook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    hook_type TEXT NOT NULL CHECK(hook_type IN (
        'pre_tool_use', 'post_tool_use', 'notification', 'stop', 'subagent_stop'
    )),
    tool_name TEXT,
    project_id TEXT,
    payload TEXT,
    classification TEXT DEFAULT 'CUI',
    signature TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_hook_events_session ON hook_events(session_id);
CREATE INDEX IF NOT EXISTS idx_hook_events_type ON hook_events(hook_type);
CREATE INDEX IF NOT EXISTS idx_hook_events_created ON hook_events(created_at);

-- Agent execution log (append-only)
CREATE TABLE IF NOT EXISTS agent_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT UNIQUE NOT NULL,
    project_id TEXT,
    agent_type TEXT,
    model TEXT,
    prompt_hash TEXT,
    status TEXT CHECK(status IN ('started', 'completed', 'failed', 'retried', 'timeout')),
    retry_count INTEGER DEFAULT 0,
    duration_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    output_path TEXT,
    error_message TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_exec_id ON agent_executions(execution_id);
CREATE INDEX IF NOT EXISTS idx_agent_exec_status ON agent_executions(status);

-- ============================================================
-- NLQ COMPLIANCE QUERIES (Phase 40)
-- ============================================================

-- NLQ query history (append-only, for audit)
CREATE TABLE IF NOT EXISTS nlq_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_text TEXT NOT NULL,
    generated_sql TEXT,
    result_count INTEGER,
    execution_time_ms INTEGER,
    actor TEXT,
    classification TEXT DEFAULT 'CUI',
    status TEXT CHECK(status IN ('success', 'error', 'blocked')),
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nlq_queries_status ON nlq_queries(status);
CREATE INDEX IF NOT EXISTS idx_nlq_queries_created ON nlq_queries(created_at);

-- ============================================================
-- GIT WORKTREE PARALLEL CI/CD (Phase 41)
-- ============================================================

-- Worktree tracking
CREATE TABLE IF NOT EXISTS ci_worktrees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worktree_name TEXT UNIQUE NOT NULL,
    task_id TEXT,
    issue_number INTEGER,
    branch_name TEXT,
    target_directory TEXT,
    classification TEXT DEFAULT 'CUI',
    status TEXT CHECK(status IN ('active', 'completed', 'failed', 'cleaned')),
    agent_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_worktree_name ON ci_worktrees(worktree_name);
CREATE INDEX IF NOT EXISTS idx_worktree_status ON ci_worktrees(status);

-- GitLab task claims (prevent double-processing)
CREATE TABLE IF NOT EXISTS gitlab_task_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_iid INTEGER NOT NULL,
    issue_url TEXT,
    icdev_tag TEXT,
    worktree_name TEXT,
    status TEXT CHECK(status IN ('claimed', 'processing', 'completed', 'failed')),
    run_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_gitlab_claim_iid ON gitlab_task_claims(issue_iid);
CREATE INDEX IF NOT EXISTS idx_gitlab_claim_status ON gitlab_task_claims(status);

-- ============================================================
-- AGENT ORCHESTRATION (Opus 4.6 Multi-Agent)
-- ============================================================

-- Token usage tracking per agent/project/task
CREATE TABLE IF NOT EXISTS agent_token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    project_id TEXT,
    task_id TEXT,
    model_id TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    thinking_tokens INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    cost_estimate_usd REAL DEFAULT 0.0,
    -- nav-sec-09 attribution. token_tracker.py has always INSERTed these and
    -- ALTERs them in at runtime as a best-effort guarded by `except: pass`, so
    -- a fresh bootstrap that hit any error got a table its own writer could not
    -- use. Declaring them here makes the ALTER a no-op rather than the only
    -- thing standing between the schema and a broken INSERT.
    user_id TEXT DEFAULT NULL,
    api_key_source TEXT DEFAULT 'config',
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_token_usage_agent ON agent_token_usage(agent_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_project ON agent_token_usage(project_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_created ON agent_token_usage(created_at);

-- Per-agent monthly token budgets (Paperclip-inspired hard-stops)
CREATE TABLE IF NOT EXISTS agent_token_budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    month TEXT NOT NULL,
    budget_usd REAL NOT NULL DEFAULT 0.0,
    spent_usd REAL NOT NULL DEFAULT 0.0,
    warning_threshold REAL NOT NULL DEFAULT 0.8,
    hard_stop INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(agent_id, month)
);

-- Module-level budget tracking (generative_intelligence + predictive_analysis)
CREATE TABLE IF NOT EXISTS module_budget_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name TEXT NOT NULL CHECK(module_name IN ('generative_intelligence', 'predictive_analysis')),
    function_name TEXT,
    resource_type TEXT NOT NULL DEFAULT 'usd' CHECK(resource_type IN ('usd', 'tokens', 'operations')),
    amount REAL NOT NULL DEFAULT 0.0,
    tokens INTEGER NOT NULL DEFAULT 0,
    operations INTEGER NOT NULL DEFAULT 0,
    project_id TEXT,
    model_id TEXT,
    details_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_module_usage_module ON module_budget_usage(module_name);
CREATE INDEX IF NOT EXISTS idx_module_usage_created ON module_budget_usage(created_at);

CREATE TABLE IF NOT EXISTS module_budget_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name TEXT NOT NULL CHECK(module_name IN ('generative_intelligence', 'predictive_analysis')),
    month TEXT NOT NULL,
    budget_usd REAL NOT NULL DEFAULT 0.0,
    budget_tokens INTEGER NOT NULL DEFAULT 0,
    budget_operations INTEGER NOT NULL DEFAULT 0,
    spent_usd REAL NOT NULL DEFAULT 0.0,
    spent_tokens INTEGER NOT NULL DEFAULT 0,
    spent_operations INTEGER NOT NULL DEFAULT 0,
    warning_threshold REAL NOT NULL DEFAULT 0.8,
    hard_stop INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(module_name, month)
);

-- Atomic task checkout — lease-based single-assignee enforcement
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
CREATE INDEX IF NOT EXISTS idx_task_leases_agent ON agent_task_leases(agent_id);
CREATE INDEX IF NOT EXISTS idx_task_leases_status ON agent_task_leases(status);
CREATE INDEX IF NOT EXISTS idx_task_leases_expires ON agent_task_leases(expires_at);

-- Scout Daemon — daily autonomous self-improvement scanner
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
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scout_audit (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    pillar TEXT,
    details TEXT,
    success INTEGER,
    duration_ms INTEGER,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scout_audit_event ON scout_audit(event_type);
CREATE INDEX IF NOT EXISTS idx_scout_audit_created ON scout_audit(created_at);

-- Multi-agent workflow tracking
CREATE TABLE IF NOT EXISTS agent_workflows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    project_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'running', 'completed', 'failed', 'partially_completed', 'canceled'
    )),
    total_subtasks INTEGER DEFAULT 0,
    completed_subtasks INTEGER DEFAULT 0,
    failed_subtasks INTEGER DEFAULT 0,
    created_by TEXT DEFAULT 'orchestrator-agent',
    input_data TEXT,
    aggregated_result TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_workflow_project ON agent_workflows(project_id);
CREATE INDEX IF NOT EXISTS idx_workflow_status ON agent_workflows(status);

-- Subtasks within workflows
CREATE TABLE IF NOT EXISTS agent_subtasks (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES agent_workflows(id),
    a2a_task_id TEXT,
    agent_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'queued', 'working', 'completed', 'failed', 'canceled', 'blocked'
    )),
    depends_on TEXT,
    input_data TEXT,
    output_data TEXT,
    error_message TEXT,
    attempt_count INTEGER DEFAULT 0,
    assigned_at TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    duration_ms INTEGER,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_subtask_workflow ON agent_subtasks(workflow_id);
CREATE INDEX IF NOT EXISTS idx_subtask_agent ON agent_subtasks(agent_id);
CREATE INDEX IF NOT EXISTS idx_subtask_status ON agent_subtasks(status);

-- Agent mailbox (HMAC-signed inter-agent messaging)
CREATE TABLE IF NOT EXISTS agent_mailbox (
    id TEXT PRIMARY KEY,
    from_agent_id TEXT NOT NULL,
    to_agent_id TEXT NOT NULL,
    message_type TEXT NOT NULL CHECK(message_type IN (
        'request', 'response', 'notification', 'veto', 'escalation',
        'collaboration_invite', 'memory_share'
    )),
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    priority INTEGER DEFAULT 5 CHECK(priority BETWEEN 1 AND 10),
    in_reply_to TEXT,
    hmac_signature TEXT NOT NULL,
    read_at TIMESTAMP,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mailbox_to ON agent_mailbox(to_agent_id);
CREATE INDEX IF NOT EXISTS idx_mailbox_from ON agent_mailbox(from_agent_id);
CREATE INDEX IF NOT EXISTS idx_mailbox_type ON agent_mailbox(message_type);
CREATE INDEX IF NOT EXISTS idx_mailbox_unread ON agent_mailbox(to_agent_id, read_at);

-- Domain authority vetoes (append-only for audit)
CREATE TABLE IF NOT EXISTS agent_vetoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    authority_agent_id TEXT NOT NULL,
    vetoed_agent_id TEXT NOT NULL,
    task_id TEXT,
    workflow_id TEXT,
    project_id TEXT,
    topic TEXT NOT NULL,
    veto_type TEXT NOT NULL CHECK(veto_type IN ('hard', 'soft')),
    reason TEXT NOT NULL,
    evidence TEXT,
    status TEXT DEFAULT 'active' CHECK(status IN (
        'active', 'overridden', 'expired', 'withdrawn'
    )),
    overridden_by TEXT,
    override_justification TEXT,
    override_approval_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_veto_project ON agent_vetoes(project_id);
CREATE INDEX IF NOT EXISTS idx_veto_authority ON agent_vetoes(authority_agent_id);
CREATE INDEX IF NOT EXISTS idx_veto_status ON agent_vetoes(status);

-- Agent memory (project-scoped, per-agent + team-shared)
CREATE TABLE IF NOT EXISTS agent_memory (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    memory_type TEXT NOT NULL CHECK(memory_type IN (
        'fact', 'preference', 'collaboration', 'dispute', 'pattern',
        'context', 'lesson_learned', 'decision'
    )),
    content TEXT NOT NULL,
    importance INTEGER DEFAULT 5 CHECK(importance BETWEEN 1 AND 10),
    task_id TEXT,
    related_agent_ids TEXT,
    access_count INTEGER DEFAULT 0,
    last_accessed_at TIMESTAMP,
    expires_at TIMESTAMP,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_mem_agent ON agent_memory(agent_id, project_id);
CREATE INDEX IF NOT EXISTS idx_agent_mem_project ON agent_memory(project_id);
CREATE INDEX IF NOT EXISTS idx_agent_mem_type ON agent_memory(memory_type);
CREATE INDEX IF NOT EXISTS idx_agent_mem_importance ON agent_memory(importance DESC);

-- Collaboration history (who worked with whom)
CREATE TABLE IF NOT EXISTS agent_collaboration_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    agent_a_id TEXT NOT NULL,
    agent_b_id TEXT NOT NULL,
    collaboration_type TEXT NOT NULL CHECK(collaboration_type IN (
        'review', 'debate', 'consensus', 'veto', 'delegation', 'escalation'
    )),
    task_id TEXT,
    workflow_id TEXT,
    outcome TEXT CHECK(outcome IN (
        'agreement', 'disagreement', 'veto', 'escalation', 'timeout'
    )),
    lesson_learned TEXT,
    duration_ms INTEGER,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_collab_project ON agent_collaboration_history(project_id);
CREATE INDEX IF NOT EXISTS idx_collab_agents ON agent_collaboration_history(agent_a_id, agent_b_id);

-- ============================================================
-- AGENTIC FITNESS ASSESSMENTS (Phase 19)
-- ============================================================
CREATE TABLE IF NOT EXISTS agentic_fitness_assessments (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    component_name TEXT NOT NULL,
    spec_text TEXT,
    scores TEXT NOT NULL,
    overall_score REAL NOT NULL,
    recommendation TEXT NOT NULL,
    rationale TEXT,
    assessed_by TEXT DEFAULT 'architect-agent',
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fitness_project ON agentic_fitness_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_fitness_score ON agentic_fitness_assessments(overall_score);

-- ============================================================
-- CHILD APP REGISTRY (Phase 19 — Agentic Generation)
-- ============================================================
CREATE TABLE IF NOT EXISTS child_app_registry (
    id TEXT PRIMARY KEY,
    parent_project_id TEXT REFERENCES projects(id),
    child_name TEXT NOT NULL,
    child_type TEXT,
    child_path TEXT NOT NULL,
    project_path TEXT,
    blueprint_hash TEXT,
    blueprint_json TEXT,
    fitness_assessment_id TEXT REFERENCES agentic_fitness_assessments(id),
    capabilities TEXT NOT NULL,
    agent_count INTEGER DEFAULT 5,
    cloud_provider TEXT DEFAULT 'aws',
    target_cloud TEXT,
    callback_url TEXT,
    compliance_required INTEGER DEFAULT 0,
    status TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_child_app_parent ON child_app_registry(parent_project_id);
CREATE INDEX IF NOT EXISTS idx_child_app_name ON child_app_registry(child_name);

-- ============================================================
-- FIPS 199/200 SECURITY CATEGORIZATION (Phase 20)
-- ============================================================

-- FIPS 199 system categorizations
CREATE TABLE IF NOT EXISTS fips199_categorizations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    categorization_date TEXT DEFAULT (datetime('now')),
    categorizer TEXT DEFAULT 'icdev-compliance-engine',
    confidentiality_impact TEXT NOT NULL
        CHECK(confidentiality_impact IN ('Low', 'Moderate', 'High')),
    integrity_impact TEXT NOT NULL
        CHECK(integrity_impact IN ('Low', 'Moderate', 'High')),
    availability_impact TEXT NOT NULL
        CHECK(availability_impact IN ('Low', 'Moderate', 'High')),
    overall_categorization TEXT NOT NULL
        CHECK(overall_categorization IN ('Low', 'Moderate', 'High')),
    categorization_method TEXT DEFAULT 'information_type'
        CHECK(categorization_method IN ('information_type', 'manual', 'inherited', 'cnssi_1253')),
    justification TEXT,
    information_types_summary TEXT,
    cnssi_1253_applied INTEGER DEFAULT 0,
    cnssi_overlay_ids TEXT,
    baseline_selected TEXT
        CHECK(baseline_selected IN ('Low', 'Moderate', 'High')),
    approved_by TEXT,
    approved_at TEXT,
    status TEXT DEFAULT 'draft'
        CHECK(status IN ('draft', 'review', 'approved', 'superseded')),
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fips199_project ON fips199_categorizations(project_id);
CREATE INDEX IF NOT EXISTS idx_fips199_status ON fips199_categorizations(status);

-- Information types assigned to a project (N:1 to fips199_categorizations)
CREATE TABLE IF NOT EXISTS project_information_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    categorization_id INTEGER REFERENCES fips199_categorizations(id),
    information_type_id TEXT NOT NULL,
    information_type_name TEXT NOT NULL,
    information_type_category TEXT NOT NULL,
    provisional_confidentiality TEXT NOT NULL
        CHECK(provisional_confidentiality IN ('N/A', 'Low', 'Moderate', 'High')),
    provisional_integrity TEXT NOT NULL
        CHECK(provisional_integrity IN ('N/A', 'Low', 'Moderate', 'High')),
    provisional_availability TEXT NOT NULL
        CHECK(provisional_availability IN ('N/A', 'Low', 'Moderate', 'High')),
    adjusted_confidentiality TEXT,
    adjusted_integrity TEXT,
    adjusted_availability TEXT,
    adjustment_justification TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, information_type_id)
);

CREATE INDEX IF NOT EXISTS idx_proj_infotype_project ON project_information_types(project_id);
CREATE INDEX IF NOT EXISTS idx_proj_infotype_cat ON project_information_types(categorization_id);

-- FIPS 200 assessment results
CREATE TABLE IF NOT EXISTS fips200_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    baseline TEXT NOT NULL
        CHECK(baseline IN ('Low', 'Moderate', 'High')),
    requirement_area_id TEXT NOT NULL,
    requirement_area_name TEXT NOT NULL,
    family TEXT NOT NULL,
    total_required_controls INTEGER DEFAULT 0,
    mapped_controls INTEGER DEFAULT 0,
    implemented_controls INTEGER DEFAULT 0,
    planned_controls INTEGER DEFAULT 0,
    not_applicable_controls INTEGER DEFAULT 0,
    coverage_pct REAL DEFAULT 0.0,
    status TEXT DEFAULT 'not_assessed'
        CHECK(status IN ('not_assessed', 'satisfied', 'partially_satisfied',
                         'not_satisfied', 'not_applicable')),
    gap_controls TEXT,
    evidence_description TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, requirement_area_id)
);

CREATE INDEX IF NOT EXISTS idx_fips200_project ON fips200_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_fips200_status ON fips200_assessments(status);

-- ============================================================
-- MARKETPLACE — Federated FORGE Asset Registry (Phase 22)
-- ============================================================

-- Core asset registry (skills, goals, hardprompts, context, args, compliance extensions)
CREATE TABLE IF NOT EXISTS marketplace_assets (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    display_name TEXT,
    asset_type TEXT NOT NULL CHECK(asset_type IN ('skill', 'goal', 'hardprompt', 'context', 'args', 'compliance')),
    description TEXT NOT NULL,
    current_version TEXT NOT NULL,
    classification TEXT NOT NULL DEFAULT 'CUI // SP-CTI',
    impact_level TEXT NOT NULL DEFAULT 'IL4' CHECK(impact_level IN ('IL2', 'IL4', 'IL5', 'IL6')),
    publisher_tenant_id TEXT,
    publisher_org TEXT,
    publisher_user TEXT,
    catalog_tier TEXT NOT NULL DEFAULT 'tenant_local' CHECK(catalog_tier IN ('tenant_local', 'central_vetted')),
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'scanning', 'review', 'published', 'deprecated', 'revoked')),
    license TEXT DEFAULT 'USG-INTERNAL',
    tags TEXT,
    compliance_controls TEXT,
    supported_languages TEXT,
    min_icdev_version TEXT,
    download_count INTEGER DEFAULT 0,
    install_count INTEGER DEFAULT 0,
    avg_rating REAL DEFAULT 0.0,
    rating_count INTEGER DEFAULT 0,
    deprecated INTEGER DEFAULT 0,
    replacement_slug TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mkt_asset_slug ON marketplace_assets(slug);
CREATE INDEX IF NOT EXISTS idx_mkt_asset_type ON marketplace_assets(asset_type);
CREATE INDEX IF NOT EXISTS idx_mkt_asset_tenant ON marketplace_assets(publisher_tenant_id);
CREATE INDEX IF NOT EXISTS idx_mkt_asset_tier ON marketplace_assets(catalog_tier);
CREATE INDEX IF NOT EXISTS idx_mkt_asset_status ON marketplace_assets(status);
CREATE INDEX IF NOT EXISTS idx_mkt_asset_il ON marketplace_assets(impact_level);

-- Version history (immutable — published versions cannot be modified)
CREATE TABLE IF NOT EXISTS marketplace_versions (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES marketplace_assets(id),
    version TEXT NOT NULL,
    changelog TEXT,
    sha256_hash TEXT NOT NULL,
    signature TEXT,
    signed_by TEXT,
    sbom_id TEXT,
    file_path TEXT,
    file_size_bytes INTEGER DEFAULT 0,
    metadata TEXT,
    published_by TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'published', 'yanked')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(asset_id, version)
);
CREATE INDEX IF NOT EXISTS idx_mkt_version_asset ON marketplace_versions(asset_id);
CREATE INDEX IF NOT EXISTS idx_mkt_version_status ON marketplace_versions(status);

-- Human review queue for cross-tenant sharing (append-only decisions)
CREATE TABLE IF NOT EXISTS marketplace_reviews (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES marketplace_assets(id),
    version_id TEXT NOT NULL REFERENCES marketplace_versions(id),
    reviewer_id TEXT,
    reviewer_role TEXT CHECK(reviewer_role IN ('isso', 'security_officer', 'tenant_admin', 'platform_admin')),
    decision TEXT CHECK(decision IN ('approved', 'rejected', 'conditional', 'pending')),
    rationale TEXT,
    conditions TEXT,
    scan_results_reviewed INTEGER DEFAULT 0,
    code_reviewed INTEGER DEFAULT 0,
    compliance_reviewed INTEGER DEFAULT 0,
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mkt_review_asset ON marketplace_reviews(asset_id);
CREATE INDEX IF NOT EXISTS idx_mkt_review_decision ON marketplace_reviews(decision);
CREATE INDEX IF NOT EXISTS idx_mkt_review_reviewer ON marketplace_reviews(reviewer_id);

-- Per-tenant installation tracking
CREATE TABLE IF NOT EXISTS marketplace_installations (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES marketplace_assets(id),
    version_id TEXT NOT NULL REFERENCES marketplace_versions(id),
    tenant_id TEXT NOT NULL,
    project_id TEXT REFERENCES projects(id),
    installed_by TEXT,
    install_path TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled', 'uninstalled', 'update_available')),
    installed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uninstalled_at TIMESTAMP,
    UNIQUE(asset_id, tenant_id, project_id)
);
CREATE INDEX IF NOT EXISTS idx_mkt_install_asset ON marketplace_installations(asset_id);
CREATE INDEX IF NOT EXISTS idx_mkt_install_tenant ON marketplace_installations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_mkt_install_project ON marketplace_installations(project_id);
CREATE INDEX IF NOT EXISTS idx_mkt_install_status ON marketplace_installations(status);

-- Security scan results per version (append-only)
CREATE TABLE IF NOT EXISTS marketplace_scan_results (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES marketplace_assets(id),
    version_id TEXT NOT NULL REFERENCES marketplace_versions(id),
    gate_name TEXT NOT NULL CHECK(gate_name IN (
        'sast_scan', 'secret_detection', 'dependency_audit',
        'cui_marking_validation', 'sbom_generation',
        'supply_chain_provenance', 'digital_signature'
    )),
    status TEXT NOT NULL CHECK(status IN ('pass', 'fail', 'warning', 'skipped', 'error')),
    findings_count INTEGER DEFAULT 0,
    critical_count INTEGER DEFAULT 0,
    high_count INTEGER DEFAULT 0,
    medium_count INTEGER DEFAULT 0,
    low_count INTEGER DEFAULT 0,
    details TEXT,
    scanned_by TEXT DEFAULT 'icdev-marketplace-scanner',
    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mkt_scan_asset ON marketplace_scan_results(asset_id);
CREATE INDEX IF NOT EXISTS idx_mkt_scan_version ON marketplace_scan_results(version_id);
CREATE INDEX IF NOT EXISTS idx_mkt_scan_gate ON marketplace_scan_results(gate_name);
CREATE INDEX IF NOT EXISTS idx_mkt_scan_status ON marketplace_scan_results(status);

-- Community ratings (one rating per tenant per asset)
CREATE TABLE IF NOT EXISTS marketplace_ratings (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES marketplace_assets(id),
    tenant_id TEXT NOT NULL,
    rated_by TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
    comment TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(asset_id, tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_mkt_rating_asset ON marketplace_ratings(asset_id);
CREATE INDEX IF NOT EXISTS idx_mkt_rating_tenant ON marketplace_ratings(tenant_id);

-- Vector embeddings for semantic search (Ollama nomic-embed-text, air-gapped)
CREATE TABLE IF NOT EXISTS marketplace_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES marketplace_assets(id),
    content_hash TEXT NOT NULL,
    embedding BLOB NOT NULL,
    embedding_model TEXT DEFAULT 'nomic-embed-text',
    embedding_dimensions INTEGER DEFAULT 768,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(asset_id)
);
CREATE INDEX IF NOT EXISTS idx_mkt_embed_asset ON marketplace_embeddings(asset_id);

-- Asset dependency graph (adjacency list per D27)
CREATE TABLE IF NOT EXISTS marketplace_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES marketplace_assets(id),
    depends_on_slug TEXT NOT NULL,
    version_constraint TEXT NOT NULL DEFAULT '>=0.0.0',
    dependency_type TEXT DEFAULT 'required' CHECK(dependency_type IN ('required', 'optional', 'peer')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(asset_id, depends_on_slug)
);
CREATE INDEX IF NOT EXISTS idx_mkt_dep_asset ON marketplace_dependencies(asset_id);
CREATE INDEX IF NOT EXISTS idx_mkt_dep_target ON marketplace_dependencies(depends_on_slug);

-- ============================================================
-- OPENCLAW SKILL BRIDGE (Phase 69)
-- ============================================================

-- Quarantine-first import tracker for external OpenClaw skills
-- Zero-trust: all imports start at trust_score=0.30 (untrusted)
-- No registration/renewal required — free community assets
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
    scan_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(scan_status IN ('pending', 'scanning', 'passed', 'failed')),
    gate_results TEXT,
    review_required INTEGER DEFAULT 0,
    review_id TEXT,
    trust_score REAL DEFAULT 0.30,
    status TEXT NOT NULL DEFAULT 'quarantined'
        CHECK(status IN ('quarantined', 'scanning', 'review_pending',
                         'promoted', 'rejected', 'expired')),
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
CREATE INDEX IF NOT EXISTS idx_oc_import_status ON openclaw_imports(status);
CREATE INDEX IF NOT EXISTS idx_oc_import_tenant ON openclaw_imports(tenant_id);
CREATE INDEX IF NOT EXISTS idx_oc_import_slug ON openclaw_imports(openclaw_slug);
CREATE INDEX IF NOT EXISTS idx_oc_import_hash ON openclaw_imports(sha256_hash);

-- Export audit trail (append-only, NIST AU)
CREATE TABLE IF NOT EXISTS openclaw_exports (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    output_path TEXT,
    exported_by TEXT NOT NULL,
    review_id TEXT,
    review_status TEXT DEFAULT 'pending'
        CHECK(review_status IN ('pending', 'approved', 'rejected')),
    stripping_log TEXT,
    sha256_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'approved', 'exported', 'rejected')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oc_export_asset ON openclaw_exports(asset_id);
CREATE INDEX IF NOT EXISTS idx_oc_export_status ON openclaw_exports(status);

-- ============================================================
-- UNIVERSAL COMPLIANCE PLATFORM (Phase 23)
-- ============================================================

-- Data classification categories assigned to projects
CREATE TABLE IF NOT EXISTS data_classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    data_category TEXT NOT NULL,
    source TEXT DEFAULT 'manual' CHECK(source IN ('manual', 'auto_detected', 'inherited', 'policy')),
    confirmed INTEGER DEFAULT 0,
    confirmed_by TEXT,
    confirmed_at TIMESTAMP,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, data_category)
);
CREATE INDEX IF NOT EXISTS idx_dataclass_project ON data_classifications(project_id);
CREATE INDEX IF NOT EXISTS idx_dataclass_category ON data_classifications(data_category);

-- Framework applicability per project (which frameworks apply)
CREATE TABLE IF NOT EXISTS framework_applicability (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    framework_id TEXT NOT NULL,
    source TEXT DEFAULT 'manual' CHECK(source IN ('manual', 'auto_detected', 'policy', 'data_category')),
    detection_rule TEXT,
    confidence REAL DEFAULT 1.0,
    confirmed INTEGER DEFAULT 0,
    confirmed_by TEXT,
    confirmed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, framework_id)
);
CREATE INDEX IF NOT EXISTS idx_fwapply_project ON framework_applicability(project_id);
CREATE INDEX IF NOT EXISTS idx_fwapply_framework ON framework_applicability(framework_id);

-- Compliance detection log (advisory auto-detection history)
CREATE TABLE IF NOT EXISTS compliance_detection_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    detection_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    rules_evaluated INTEGER DEFAULT 0,
    frameworks_detected TEXT,
    data_categories_found TEXT,
    data_categories TEXT,
    detected_frameworks TEXT,
    recommended_frameworks TEXT,
    required_frameworks TEXT,
    rules_matched TEXT,
    applied INTEGER DEFAULT 0,
    confirmed INTEGER DEFAULT 0,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_detect_project ON compliance_detection_log(project_id);

-- Crosswalk bridges between framework hubs (ADR D111)
CREATE TABLE IF NOT EXISTS crosswalk_bridges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_framework TEXT NOT NULL,
    source_control_id TEXT NOT NULL,
    target_framework TEXT NOT NULL,
    target_control_ids TEXT NOT NULL,
    mapping_type TEXT DEFAULT 'equivalent' CHECK(mapping_type IN ('equivalent', 'partial', 'superset', 'subset')),
    bridge_file TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_framework, source_control_id, target_framework)
);
CREATE INDEX IF NOT EXISTS idx_bridge_source ON crosswalk_bridges(source_framework, source_control_id);
CREATE INDEX IF NOT EXISTS idx_bridge_target ON crosswalk_bridges(target_framework);

-- Framework catalog versions (track catalog updates independently — ADR D112)
CREATE TABLE IF NOT EXISTS framework_catalog_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    framework_id TEXT NOT NULL,
    catalog_file TEXT NOT NULL,
    version TEXT NOT NULL,
    control_count INTEGER DEFAULT 0,
    content_hash TEXT,
    loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(framework_id, version)
);
CREATE INDEX IF NOT EXISTS idx_catver_framework ON framework_catalog_versions(framework_id);

-- CJIS Security Policy assessments
CREATE TABLE IF NOT EXISTS cjis_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    status TEXT DEFAULT 'not_assessed' CHECK(status IN ('not_assessed', 'satisfied', 'partially_satisfied', 'not_satisfied', 'not_applicable')),
    evidence TEXT,
    automation_result TEXT,
    nist_crosswalk TEXT,
    assessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, requirement_id)
);
CREATE INDEX IF NOT EXISTS idx_cjis_project ON cjis_assessments(project_id);

-- HIPAA Security Rule assessments
CREATE TABLE IF NOT EXISTS hipaa_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    status TEXT DEFAULT 'not_assessed' CHECK(status IN ('not_assessed', 'satisfied', 'partially_satisfied', 'not_satisfied', 'not_applicable')),
    evidence TEXT,
    automation_result TEXT,
    nist_crosswalk TEXT,
    assessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, requirement_id)
);
CREATE INDEX IF NOT EXISTS idx_hipaa_project ON hipaa_assessments(project_id);

-- HITRUST CSF v11 assessments
CREATE TABLE IF NOT EXISTS hitrust_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    status TEXT DEFAULT 'not_assessed' CHECK(status IN ('not_assessed', 'satisfied', 'partially_satisfied', 'not_satisfied', 'not_applicable')),
    evidence TEXT,
    automation_result TEXT,
    nist_crosswalk TEXT,
    assessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, requirement_id)
);
CREATE INDEX IF NOT EXISTS idx_hitrust_project ON hitrust_assessments(project_id);

-- SOC 2 Type II assessments
CREATE TABLE IF NOT EXISTS soc2_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    status TEXT DEFAULT 'not_assessed' CHECK(status IN ('not_assessed', 'satisfied', 'partially_satisfied', 'not_satisfied', 'not_applicable')),
    evidence TEXT,
    automation_result TEXT,
    nist_crosswalk TEXT,
    assessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, requirement_id)
);
CREATE INDEX IF NOT EXISTS idx_soc2_project ON soc2_assessments(project_id);

-- PCI DSS v4.0 assessments
CREATE TABLE IF NOT EXISTS pci_dss_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    status TEXT DEFAULT 'not_assessed' CHECK(status IN ('not_assessed', 'satisfied', 'partially_satisfied', 'not_satisfied', 'not_applicable')),
    evidence TEXT,
    automation_result TEXT,
    nist_crosswalk TEXT,
    assessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, requirement_id)
);
CREATE INDEX IF NOT EXISTS idx_pcidss_project ON pci_dss_assessments(project_id);

-- ISO/IEC 27001:2022 assessments
CREATE TABLE IF NOT EXISTS iso27001_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    status TEXT DEFAULT 'not_assessed' CHECK(status IN ('not_assessed', 'satisfied', 'partially_satisfied', 'not_satisfied', 'not_applicable')),
    evidence TEXT,
    automation_result TEXT,
    nist_crosswalk TEXT,
    assessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, requirement_id)
);
CREATE INDEX IF NOT EXISTS idx_iso27001_project ON iso27001_assessments(project_id);

-- ============================================================
-- DEVSECOPS PROFILES (Phase 24)
-- ============================================================
CREATE TABLE IF NOT EXISTS devsecops_profiles (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    maturity_level TEXT CHECK(maturity_level IN (
        'level_1_initial', 'level_2_managed', 'level_3_defined',
        'level_4_measured', 'level_5_optimized'
    )),
    active_stages TEXT,
    stage_configs TEXT,
    detected_at TEXT,
    confirmed_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id)
);

-- ============================================================
-- ZTA MATURITY SCORES (Phase 24-25)
-- ============================================================
CREATE TABLE IF NOT EXISTS zta_maturity_scores (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    pillar TEXT NOT NULL CHECK(pillar IN (
        'user_identity', 'device', 'network', 'application_workload',
        'data', 'visibility_analytics', 'automation_orchestration', 'overall'
    )),
    score REAL CHECK(score >= 0.0 AND score <= 1.0),
    maturity_level TEXT CHECK(maturity_level IN ('traditional', 'advanced', 'optimal', 'unmeasured')),
    evidence TEXT,
    assessed_by TEXT DEFAULT 'icdev-devsecops-agent',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_zta_maturity_project ON zta_maturity_scores(project_id);

-- ============================================================
-- ZTA POSTURE EVIDENCE (Phase 25 — feeds into cATO)
-- ============================================================
CREATE TABLE IF NOT EXISTS zta_posture_evidence (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    evidence_type TEXT NOT NULL,
    evidence_data TEXT,
    status TEXT CHECK(status IN ('current', 'stale', 'expired', 'not_collected')),
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_zta_evidence_project ON zta_posture_evidence(project_id);

-- ============================================================
-- NIST 800-207 ASSESSMENTS (Phase 25 — BaseAssessor pattern)
-- ============================================================
CREATE TABLE IF NOT EXISTS nist_800_207_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT,
    assessor TEXT DEFAULT 'icdev-devsecops-agent',
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    family TEXT,
    status TEXT DEFAULT 'not_assessed' CHECK(status IN (
        'not_assessed', 'satisfied', 'partially_satisfied',
        'not_satisfied', 'not_applicable', 'risk_accepted'
    )),
    evidence_description TEXT,
    nist_800_53_crosswalk TEXT,
    automation_result TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, requirement_id)
);
CREATE INDEX IF NOT EXISTS idx_nist_800_207_project ON nist_800_207_assessments(project_id);

-- ============================================================
-- DEVSECOPS PIPELINE AUDIT (Phase 24 — append-only)
-- ============================================================
CREATE TABLE IF NOT EXISTS devsecops_pipeline_audit (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    pipeline_run_id TEXT,
    stage TEXT NOT NULL,
    tool TEXT NOT NULL,
    status TEXT CHECK(status IN ('passed', 'failed', 'skipped', 'warning')),
    findings_count INTEGER DEFAULT 0,
    findings_data TEXT,
    duration_seconds REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_devsecops_audit_project ON devsecops_pipeline_audit(project_id);

-- =====================================================================
-- Phase 26: MOSA (Modular Open Systems Approach)
-- =====================================================================

-- MOSA compliance assessments (BaseAssessor pattern)
CREATE TABLE IF NOT EXISTS mosa_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT,
    assessor TEXT DEFAULT 'icdev-compliance-agent',
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    family TEXT,
    status TEXT CHECK(status IN ('not_assessed','satisfied','partially_satisfied','not_satisfied','not_applicable','risk_accepted')),
    evidence_description TEXT,
    nist_800_53_crosswalk TEXT,
    automation_result TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, requirement_id)
);
CREATE INDEX IF NOT EXISTS idx_mosa_assessments_project ON mosa_assessments(project_id);

-- Interface Control Documents
CREATE TABLE IF NOT EXISTS icd_documents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    interface_id TEXT NOT NULL,
    interface_name TEXT NOT NULL,
    version TEXT DEFAULT '1.0.0',
    source_system TEXT,
    target_system TEXT,
    protocol TEXT,
    data_format TEXT,
    content TEXT,
    file_path TEXT,
    classification TEXT DEFAULT 'CUI',
    status TEXT CHECK(status IN ('draft','review','approved','deprecated')) DEFAULT 'draft',
    approval_status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,
    approved_at TIMESTAMP,
    approved_by TEXT,
    UNIQUE(project_id, interface_id, version)
);
CREATE INDEX IF NOT EXISTS idx_icd_documents_project ON icd_documents(project_id);

-- Technical Standard Profiles
CREATE TABLE IF NOT EXISTS tsp_documents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    version TEXT DEFAULT '1.0',
    standards TEXT,
    deviations TEXT,
    content TEXT,
    file_path TEXT,
    classification TEXT DEFAULT 'CUI',
    status TEXT CHECK(status IN ('draft','review','approved','deprecated')) DEFAULT 'draft',
    approval_status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,
    approved_at TIMESTAMP,
    approved_by TEXT,
    UNIQUE(project_id, version)
);
CREATE INDEX IF NOT EXISTS idx_tsp_documents_project ON tsp_documents(project_id);

-- MOSA modularity metrics (time-series, D131)
CREATE TABLE IF NOT EXISTS mosa_modularity_metrics (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    assessment_date TEXT,
    module_count INTEGER,
    interface_count INTEGER,
    coupling_score REAL,
    cohesion_score REAL,
    interface_coverage_pct REAL,
    circular_deps INTEGER DEFAULT 0,
    approved_icd_count INTEGER DEFAULT 0,
    total_icd_required INTEGER DEFAULT 0,
    tsp_current INTEGER DEFAULT 0,
    overall_modularity_score REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mosa_metrics_project ON mosa_modularity_metrics(project_id);

-- ── CI/CD Pipeline Runs (Phase 1 — D132, D133) ────────────────────────────
CREATE TABLE IF NOT EXISTS ci_pipeline_runs (
    id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    workflow TEXT NOT NULL,
    status TEXT CHECK(status IN ('queued', 'running', 'completed', 'failed', 'recovering')),
    trigger_source TEXT,
    event_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pipeline_session ON ci_pipeline_runs(session_key, status);
CREATE INDEX IF NOT EXISTS idx_pipeline_run ON ci_pipeline_runs(run_id);

-- ── CI/CD Event Queue — lane-aware processing (Phase 1 — D133) ────────────
CREATE TABLE IF NOT EXISTS ci_event_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    event_id TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    status TEXT CHECK(status IN ('queued', 'processing', 'processed', 'dropped')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_queue_session ON ci_event_queue(session_key, status);

-- ── CI/CD Conversations — conversational feedback loop (Phase 3 — D135) ───
CREATE TABLE IF NOT EXISTS ci_conversations (
    id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    issue_number INTEGER,
    channel_id TEXT,
    thread_ts TEXT,
    status TEXT CHECK(status IN ('active', 'paused', 'completed', 'abandoned')),
    total_turns INTEGER DEFAULT 0,
    last_agent_action TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_conv_session ON ci_conversations(session_key, status);

-- ── CI/CD Conversation Turns — turn-by-turn history (Phase 3 — D135) ──────
CREATE TABLE IF NOT EXISTS ci_conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES ci_conversations(id),
    turn_number INTEGER NOT NULL,
    role TEXT CHECK(role IN ('developer', 'agent', 'system')),
    content TEXT NOT NULL,
    content_type TEXT CHECK(content_type IN (
        'text', 'command', 'code_change', 'test_result',
        'approval', 'rejection', 'status_update', 'error'
    )),
    action_taken TEXT,
    comment_id TEXT,
    metadata TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON ci_conversation_turns(session_id, turn_number);

-- ============================================================
-- REMOTE COMMAND GATEWAY (Phase 28)
-- ============================================================

-- Bound identities: channel user <-> ICDEV™ user
CREATE TABLE IF NOT EXISTS remote_user_bindings (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    channel_user_id TEXT NOT NULL,
    icdev_user_id TEXT,
    tenant_id TEXT,
    binding_status TEXT DEFAULT 'pending' CHECK(binding_status IN ('pending', 'active', 'revoked')),
    bound_at TEXT,
    revoked_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(channel, channel_user_id)
);
CREATE INDEX IF NOT EXISTS idx_bindings_channel ON remote_user_bindings(channel, channel_user_id);
CREATE INDEX IF NOT EXISTS idx_bindings_user ON remote_user_bindings(icdev_user_id);

-- Command execution log (append-only, NIST AU)
CREATE TABLE IF NOT EXISTS remote_command_log (
    id TEXT PRIMARY KEY,
    binding_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    raw_command TEXT NOT NULL,
    parsed_tool TEXT,
    parsed_args TEXT,
    gate_results TEXT,
    execution_status TEXT CHECK(execution_status IN ('accepted', 'rejected', 'completed', 'failed')),
    response_classification TEXT,
    response_filtered INTEGER DEFAULT 0,
    error_message TEXT,
    execution_time_ms INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (binding_id) REFERENCES remote_user_bindings(id)
);
CREATE INDEX IF NOT EXISTS idx_cmdlog_binding ON remote_command_log(binding_id);
CREATE INDEX IF NOT EXISTS idx_cmdlog_channel ON remote_command_log(channel);
CREATE INDEX IF NOT EXISTS idx_cmdlog_status ON remote_command_log(execution_status);

-- Command allowlist (which commands are available per channel)
CREATE TABLE IF NOT EXISTS remote_command_allowlist (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    command_pattern TEXT NOT NULL,
    max_il TEXT DEFAULT 'IL4',
    requires_confirmation INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_allowlist_channel ON remote_command_allowlist(channel);

-- Spec-kit Pattern 3: Project constitutions (D158)
CREATE TABLE IF NOT EXISTS project_constitutions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    principle_text TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    priority INTEGER DEFAULT 1,
    is_active INTEGER DEFAULT 1,
    created_by TEXT DEFAULT 'system',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_constitutions_project ON project_constitutions(project_id);

-- Spec-kit Pattern 6: Spec registry (D160)
CREATE TABLE IF NOT EXISTS spec_registry (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    spec_path TEXT NOT NULL,
    spec_dir TEXT,
    issue_number TEXT,
    run_id TEXT,
    title TEXT,
    quality_score REAL,
    consistency_score REAL,
    constitution_pass INTEGER,
    last_checked_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_spec_registry_project ON spec_registry(project_id);

-- ============================================================
-- DEV PROFILES (Phase 34 — D183-D188)
-- ============================================================

-- Versioned dev profiles — immutable rows per D183 (no UPDATE, insert new version)
CREATE TABLE IF NOT EXISTS dev_profiles (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK(scope IN ('platform','tenant','program','project','user')),
    scope_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    profile_md TEXT,
    profile_yaml TEXT NOT NULL,
    dimensions TEXT,
    template TEXT,
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

-- Dimension locks — role-based governance (D184)
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

-- Auto-detection results — advisory only per D185
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

-- Phase 29: Heartbeat daemon check results (D141)
CREATE TABLE IF NOT EXISTS heartbeat_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_type TEXT NOT NULL,
    last_run TEXT NOT NULL,
    next_run TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'ok', 'warning', 'critical', 'error', 'healthy')),
    result_summary TEXT,
    items_found INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    details TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_hb_check_type ON heartbeat_checks(check_type);
CREATE INDEX IF NOT EXISTS idx_hb_status ON heartbeat_checks(status);
CREATE INDEX IF NOT EXISTS idx_hb_next_run ON heartbeat_checks(next_run);

-- Push-Based Metrics Sidecar: per-container/process metrics with push buffer
CREATE TABLE IF NOT EXISTS container_metrics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    container_id     TEXT    NOT NULL,
    container_name   TEXT,
    host             TEXT    NOT NULL,
    backend          TEXT    NOT NULL DEFAULT 'psutil',
    cpu_percent      REAL,
    memory_percent   REAL,
    memory_rss_mb    REAL,
    memory_limit_mb  REAL,
    disk_read_mb     REAL,
    disk_write_mb    REAL,
    net_rx_mb        REAL,
    net_tx_mb        REAL,
    status           TEXT,
    pushed           INTEGER DEFAULT 0,
    collected_at     TEXT    DEFAULT (datetime('now')),
    pushed_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_cm_container ON container_metrics(container_id);
CREATE INDEX IF NOT EXISTS idx_cm_pushed    ON container_metrics(pushed);
CREATE INDEX IF NOT EXISTS idx_cm_collected ON container_metrics(collected_at);

-- Phase 29: Auto-resolution alert processing log (D143-D145, append-only)
CREATE TABLE IF NOT EXISTS auto_resolution_log (
    id TEXT PRIMARY KEY,
    alert_source TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    alert_payload TEXT NOT NULL,
    project_id TEXT REFERENCES projects(id),
    confidence REAL DEFAULT 0.0,
    decision TEXT NOT NULL
        CHECK(decision IN ('auto_fix', 'suggest', 'escalate')),
    resolution_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(resolution_status IN ('pending', 'analyzing', 'fixing', 'testing',
            'pr_created', 'completed', 'failed', 'escalated', 'suggested')),
    branch_name TEXT,
    pr_url TEXT,
    test_passed BOOLEAN,
    details TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_auto_res_source ON auto_resolution_log(alert_source);
CREATE INDEX IF NOT EXISTS idx_auto_res_status ON auto_resolution_log(resolution_status);
CREATE INDEX IF NOT EXISTS idx_auto_res_project ON auto_resolution_log(project_id);
CREATE INDEX IF NOT EXISTS idx_auto_res_created ON auto_resolution_log(created_at);

-- ============================================================
-- DASHBOARD AUTHENTICATION (Phase 30 — D169-D178)
-- ============================================================

-- Dashboard users (admin-managed)
CREATE TABLE IF NOT EXISTS dashboard_users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    -- Keep in sync with tools/dashboard/auth.py::VALID_DASHBOARD_ROLES.
    -- bd/capture_mgr/contract_mgr/reviewer (prop-fix-08, RBAC_MATRIX) and
    -- migration_engineer/component_admin/auditor/ciso (migration 139,
    -- tools/govlift/rbac.py::GOVLIFT_ROLES) were both added to their
    -- respective Python role lists but never reached this CHECK constraint
    -- until now -- those roles could never actually be assigned to a user
    -- (dashboard-users-role-check-constraint).
    role TEXT NOT NULL DEFAULT 'developer'
        CHECK(role IN ('admin', 'pm', 'developer', 'isso', 'co', 'cor',
                        'migration_engineer', 'component_admin', 'auditor', 'ciso',
                        'bd', 'capture_mgr', 'contract_mgr', 'reviewer')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'suspended')),
    created_by TEXT,
    tenant_id TEXT,
    -- Bell-LaPadula MAC subject attributes (prop-sec-02)
    clearance_level TEXT NOT NULL DEFAULT 'CUI',
    compartments TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Dashboard API keys (per-user, SHA-256 hashed)
CREATE TABLE IF NOT EXISTS dashboard_api_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES dashboard_users(id),
    key_hash TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    label TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'revoked')),
    last_used_at TIMESTAMP,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    revoked_at TIMESTAMP,
    revoked_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_dash_apikey_hash ON dashboard_api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_dash_apikey_user ON dashboard_api_keys(user_id);

-- Dashboard auth audit log (append-only, D6 compliant)
CREATE TABLE IF NOT EXISTS dashboard_auth_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    event_type TEXT NOT NULL
        CHECK(event_type IN (
            'login_success', 'login_failed', 'logout',
            'key_created', 'key_revoked',
            'user_created', 'user_suspended', 'user_reactivated',
            'session_expired', 'permission_denied'
        )),
    ip_address TEXT,
    user_agent TEXT,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dash_auth_log_user ON dashboard_auth_log(user_id);
CREATE INDEX IF NOT EXISTS idx_dash_auth_log_created ON dashboard_auth_log(created_at);

-- BYOK: User/department LLM API keys (Fernet AES-256 encrypted, D175)
CREATE TABLE IF NOT EXISTS dashboard_user_llm_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES dashboard_users(id),
    provider TEXT NOT NULL,
    encrypted_key TEXT NOT NULL,
    key_label TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'revoked')),
    department TEXT,
    is_department_key INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dash_llm_keys_user ON dashboard_user_llm_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_dash_llm_keys_provider ON dashboard_user_llm_keys(provider);

-- ============================================================
-- INNOVATION ENGINE (Phase 35 — D199-D208)
-- ============================================================

-- Innovation signals — discovered opportunities (append-only, D206)
CREATE TABLE IF NOT EXISTS innovation_signals (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    body TEXT,
    url TEXT,
    metadata TEXT,
    community_score REAL DEFAULT 0.0,
    composite_score REAL,
    content_hash TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK(status IN ('new', 'scored', 'triaged', 'approved', 'suggested',
                         'blocked', 'logged', 'solution_generated', 'published')),
    category TEXT,
    score REAL,
    raw_data TEXT,
    -- The live table carries `raw_score` / `keywords`, which migration 329 adds to
    -- databases that already exist. Declaring them here too is what keeps a FRESH
    -- database the same shape: without it the scout daemon's INSERT — corrected in
    -- swp-scan-01 to name the columns the live schema really has — would match the
    -- migrated instance and fail on a newly initialised one.
    raw_score REAL,
    keywords TEXT,
    innovation_score REAL,
    score_breakdown TEXT,
    implementation_status TEXT,
    triage_result TEXT
        CHECK(triage_result IS NULL OR triage_result IN ('approved', 'suggested', 'blocked', 'logged')),
    gotcha_layer TEXT
        CHECK(gotcha_layer IS NULL OR gotcha_layer IN ('goal', 'tool', 'arg', 'context', 'hardprompt')),
    boundary_tier TEXT
        CHECK(boundary_tier IS NULL OR boundary_tier IN ('GREEN', 'YELLOW', 'ORANGE', 'RED')),
    classification TEXT DEFAULT 'CUI',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_innovation_signals_status ON innovation_signals(status);
CREATE INDEX IF NOT EXISTS idx_innovation_signals_source ON innovation_signals(source);
CREATE INDEX IF NOT EXISTS idx_innovation_signals_score ON innovation_signals(innovation_score);
CREATE INDEX IF NOT EXISTS idx_innovation_signals_hash ON innovation_signals(content_hash);
CREATE INDEX IF NOT EXISTS idx_innovation_signals_discovered ON innovation_signals(discovered_at);
CREATE INDEX IF NOT EXISTS idx_innovation_signals_category ON innovation_signals(category);

-- Innovation triage log — triage decisions per signal (append-only, D206)
CREATE TABLE IF NOT EXISTS innovation_triage_log (
    id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL REFERENCES innovation_signals(id),
    stage INTEGER NOT NULL CHECK(stage BETWEEN 1 AND 5),
    stage_name TEXT NOT NULL
        CHECK(stage_name IN ('classify_signal', 'gotcha_fit_check', 'boundary_impact',
                              'compliance_precheck', 'duplicate_license_check')),
    result TEXT NOT NULL CHECK(result IN ('pass', 'block', 'warn')),
    details TEXT,
    triaged_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_innovation_triage_signal ON innovation_triage_log(signal_id);
CREATE INDEX IF NOT EXISTS idx_innovation_triage_result ON innovation_triage_log(result);

-- Innovation solutions — generated solution specs
CREATE TABLE IF NOT EXISTS innovation_solutions (
    id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL REFERENCES innovation_signals(id),
    spec_content TEXT NOT NULL,
    gotcha_layer TEXT NOT NULL
        CHECK(gotcha_layer IN ('goal', 'tool', 'arg', 'context', 'hardprompt')),
    asset_type TEXT NOT NULL
        CHECK(asset_type IN ('skill', 'goal', 'tool', 'context', 'hardprompt',
                              'arg', 'compliance_extension')),
    estimated_effort TEXT NOT NULL CHECK(estimated_effort IN ('S', 'M', 'L', 'XL')),
    status TEXT NOT NULL DEFAULT 'generated'
        CHECK(status IN ('generated', 'building', 'built', 'published', 'failed', 'rejected')),
    spec_quality_score REAL,
    build_output TEXT,
    marketplace_asset_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_innovation_solutions_signal ON innovation_solutions(signal_id);
CREATE INDEX IF NOT EXISTS idx_innovation_solutions_status ON innovation_solutions(status);
CREATE INDEX IF NOT EXISTS idx_innovation_solutions_layer ON innovation_solutions(gotcha_layer);

-- Innovation trends — detected cross-signal patterns (D207)
CREATE TABLE IF NOT EXISTS innovation_trends (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    signal_ids TEXT NOT NULL,
    signal_count INTEGER NOT NULL DEFAULT 0,
    keyword_fingerprint TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    velocity REAL DEFAULT 0.0,
    acceleration REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'emerging'
        CHECK(status IN ('emerging', 'active', 'declining', 'stale')),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_innovation_trends_status ON innovation_trends(status);
CREATE INDEX IF NOT EXISTS idx_innovation_trends_category ON innovation_trends(category);
CREATE INDEX IF NOT EXISTS idx_innovation_trends_velocity ON innovation_trends(velocity);

-- Innovation competitor scans — competitive intelligence results
CREATE TABLE IF NOT EXISTS innovation_competitor_scans (
    id TEXT PRIMARY KEY,
    competitor_name TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    releases_found INTEGER DEFAULT 0,
    features_found INTEGER DEFAULT 0,
    gaps_identified INTEGER DEFAULT 0,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_innovation_competitor_name ON innovation_competitor_scans(competitor_name);
CREATE INDEX IF NOT EXISTS idx_innovation_competitor_date ON innovation_competitor_scans(scan_date);

-- Innovation standards updates — standards body change tracking
CREATE TABLE IF NOT EXISTS innovation_standards_updates (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL
        CHECK(body IN ('nist', 'cisa', 'dod', 'fedramp', 'iso')),
    title TEXT NOT NULL,
    publication_type TEXT,
    url TEXT,
    abstract TEXT,
    published_date TEXT,
    impact_assessment TEXT,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK(status IN ('new', 'assessed', 'applied', 'not_applicable')),
    content_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_innovation_standards_body ON innovation_standards_updates(body);
CREATE INDEX IF NOT EXISTS idx_innovation_standards_status ON innovation_standards_updates(status);
CREATE INDEX IF NOT EXISTS idx_innovation_standards_hash ON innovation_standards_updates(content_hash);

-- Innovation feedback — feedback loop metrics for calibration
CREATE TABLE IF NOT EXISTS innovation_feedback (
    id TEXT PRIMARY KEY,
    signal_id TEXT REFERENCES innovation_signals(id),
    solution_id TEXT REFERENCES innovation_solutions(id),
    feedback_type TEXT NOT NULL
        CHECK(feedback_type IN ('marketplace_install', 'marketplace_rating',
                                 'self_heal_hit', 'gate_failure_reduction',
                                 'feature_request_addressed', 'manual_review')),
    feedback_value REAL,
    feedback_details TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_innovation_feedback_signal ON innovation_feedback(signal_id);
CREATE INDEX IF NOT EXISTS idx_innovation_feedback_type ON innovation_feedback(feedback_type);

-- Innovation signal — normalized innovation engine outputs for ACF consumption (append-only)
CREATE TABLE IF NOT EXISTS innovation_signal (
    id TEXT PRIMARY KEY,
    concept_id TEXT,
    signal_type TEXT DEFAULT 'opportunity'
        CHECK(signal_type IN ('opportunity', 'trend', 'threat', 'technology', 'regulatory', 'other')),
    source_ref TEXT,
    score REAL DEFAULT 0.0,
    rank INTEGER,
    content_hash TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_innovation_signal_concept ON innovation_signal(concept_id);
CREATE INDEX IF NOT EXISTS idx_innovation_signal_score ON innovation_signal(score);
CREATE INDEX IF NOT EXISTS idx_innovation_signal_hash ON innovation_signal(content_hash);
CREATE INDEX IF NOT EXISTS idx_innovation_signal_created ON innovation_signal(created_at);

-- ============================================================
-- PHASE 37: AI SECURITY (MITRE ATLAS, OWASP LLM, NIST AI RMF, ISO 42001)
-- ============================================================

-- Prompt injection detection log (append-only, NIST AU)
CREATE TABLE IF NOT EXISTS prompt_injection_log (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    detected INTEGER NOT NULL DEFAULT 0,
    confidence REAL DEFAULT 0.0,
    action TEXT CHECK(action IN ('allow', 'warn', 'flag', 'block')),
    finding_count INTEGER,
    findings TEXT,
    findings_json TEXT,
    project_id TEXT,
    user_id TEXT,
    scanned_at TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pi_log_source ON prompt_injection_log(source);
CREATE INDEX IF NOT EXISTS idx_pi_log_action ON prompt_injection_log(action);
CREATE INDEX IF NOT EXISTS idx_pi_log_project ON prompt_injection_log(project_id);

-- AI telemetry — LLM interaction tracking (append-only, D218)
CREATE TABLE IF NOT EXISTS ai_telemetry (
    id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    response_hash TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    thinking_tokens INTEGER DEFAULT 0,
    -- cch-tel-01: prompt-cache accounting. NOT NULL DEFAULT 0 so that "the
    -- provider served no cached tokens" is a recorded 0 and never a NULL that
    -- reads the same as "nobody looked". Existing databases get these from
    -- migration 20260816135136_ai_telemetry_cache_tokens; this DDL only ever
    -- runs for a database that does not have the table yet.
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    cost_usd REAL,
    agent_id TEXT,
    user_id TEXT,
    project_id TEXT,
    function TEXT,
    api_key_source TEXT,
    injection_scan_result TEXT,
    classification TEXT DEFAULT 'CUI',
    logged_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_telemetry_model ON ai_telemetry(model_id);
CREATE INDEX IF NOT EXISTS idx_ai_telemetry_project ON ai_telemetry(project_id);
CREATE INDEX IF NOT EXISTS idx_ai_telemetry_created ON ai_telemetry(created_at);

-- AI Bill of Materials
CREATE TABLE IF NOT EXISTS ai_bom (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    component_type TEXT,
    component_name TEXT,
    provider TEXT NOT NULL,
    version TEXT,
    purpose TEXT,
    license TEXT,
    risk_level TEXT,
    risk_classification TEXT,
    data_categories TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_bom_project ON ai_bom(project_id);

-- ATLAS assessments (BaseAssessor pattern, D116)
CREATE TABLE IF NOT EXISTS atlas_assessments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    framework_version TEXT DEFAULT 'v5.4.0',
    overall_score REAL,
    total_requirements INTEGER DEFAULT 0,
    satisfied INTEGER DEFAULT 0,
    partial INTEGER DEFAULT 0,
    not_satisfied INTEGER DEFAULT 0,
    not_applicable INTEGER DEFAULT 0,
    results_json TEXT,
    assessed_at TEXT NOT NULL DEFAULT (datetime('now')),
    assessed_by TEXT DEFAULT 'automated',
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_atlas_assessments_project ON atlas_assessments(project_id);

-- ATLAS red team results (D219 — opt-in adversarial testing)
CREATE TABLE IF NOT EXISTS atlas_red_team_results (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    technique_id TEXT NOT NULL,
    technique TEXT,
    technique_name TEXT,
    test_name TEXT NOT NULL,
    result TEXT CHECK(result IN ('pass', 'fail', 'partial', 'error')),
    passed INTEGER,
    tests_run INTEGER,
    tests_passed INTEGER,
    severity TEXT CHECK(severity IN ('critical', 'high', 'medium', 'low', 'info')),
    details TEXT,
    findings_json TEXT,
    evidence TEXT,
    remediation TEXT,
    scanned_at TEXT,
    classification TEXT DEFAULT 'CUI',
    tested_at TEXT NOT NULL DEFAULT (datetime('now')),
    tested_by TEXT DEFAULT 'automated'
);
CREATE INDEX IF NOT EXISTS idx_atlas_rt_project ON atlas_red_team_results(project_id);
CREATE INDEX IF NOT EXISTS idx_atlas_rt_technique ON atlas_red_team_results(technique_id);

-- OWASP LLM Top 10 assessments
CREATE TABLE IF NOT EXISTS owasp_llm_assessments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    framework_version TEXT DEFAULT 'v2025',
    overall_score REAL,
    total_requirements INTEGER DEFAULT 0,
    satisfied INTEGER DEFAULT 0,
    partial INTEGER DEFAULT 0,
    not_satisfied INTEGER DEFAULT 0,
    not_applicable INTEGER DEFAULT 0,
    results_json TEXT,
    assessed_at TEXT NOT NULL DEFAULT (datetime('now')),
    assessed_by TEXT DEFAULT 'automated',
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_owasp_llm_project ON owasp_llm_assessments(project_id);

-- NIST AI RMF assessments
CREATE TABLE IF NOT EXISTS nist_ai_rmf_assessments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    framework_version TEXT DEFAULT '1.0',
    overall_score REAL,
    total_requirements INTEGER DEFAULT 0,
    satisfied INTEGER DEFAULT 0,
    partial INTEGER DEFAULT 0,
    not_satisfied INTEGER DEFAULT 0,
    not_applicable INTEGER DEFAULT 0,
    results_json TEXT,
    assessed_at TEXT NOT NULL DEFAULT (datetime('now')),
    assessed_by TEXT DEFAULT 'automated',
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_nist_ai_rmf_project ON nist_ai_rmf_assessments(project_id);

-- ISO/IEC 42001 assessments
CREATE TABLE IF NOT EXISTS iso42001_assessments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    framework_version TEXT DEFAULT '2023',
    overall_score REAL,
    total_requirements INTEGER DEFAULT 0,
    satisfied INTEGER DEFAULT 0,
    partial INTEGER DEFAULT 0,
    not_satisfied INTEGER DEFAULT 0,
    not_applicable INTEGER DEFAULT 0,
    results_json TEXT,
    assessed_at TEXT NOT NULL DEFAULT (datetime('now')),
    assessed_by TEXT DEFAULT 'automated',
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_iso42001_project ON iso42001_assessments(project_id);

-- ============================================================
-- PHASE 36: EVOLUTIONARY INTELLIGENCE (Parent-Child Lifecycle)
-- ============================================================

-- Child capabilities registry
CREATE TABLE IF NOT EXISTS child_capabilities (
    id TEXT PRIMARY KEY,
    child_id TEXT NOT NULL,
    capability_name TEXT NOT NULL,
    version TEXT DEFAULT '1.0.0',
    status TEXT CHECK(status IN ('active', 'deprecated', 'testing', 'pending')) DEFAULT 'active',
    source TEXT CHECK(source IN ('inherited', 'learned', 'propagated', 'manual')) DEFAULT 'inherited',
    learned_at TEXT DEFAULT (datetime('now')),
    UNIQUE(child_id, capability_name)
);
CREATE INDEX IF NOT EXISTS idx_child_caps_child ON child_capabilities(child_id);

-- Child telemetry (pull-based health data, D210)
CREATE TABLE IF NOT EXISTS child_telemetry (
    id TEXT PRIMARY KEY,
    child_id TEXT NOT NULL,
    health_status TEXT CHECK(health_status IN ('healthy', 'degraded', 'unhealthy', 'offline')) DEFAULT 'healthy',
    metric_type TEXT,
    metric_data TEXT,
    genome_version TEXT,
    uptime_hours REAL DEFAULT 0.0,
    error_rate REAL DEFAULT 0.0,
    response_time_ms REAL,
    compliance_scores_json TEXT,
    learned_behaviors_json TEXT,
    raw_response TEXT,
    endpoint_url TEXT,
    classification TEXT DEFAULT 'CUI',
    collected_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_child_telemetry_child ON child_telemetry(child_id);

-- Child learned behaviors (D213)
CREATE TABLE IF NOT EXISTS child_learned_behaviors (
    id TEXT PRIMARY KEY,
    child_id TEXT NOT NULL,
    behavior_type TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence_json TEXT,
    confidence REAL DEFAULT 0.0,
    evaluated INTEGER DEFAULT 0,
    absorbed INTEGER DEFAULT 0,
    trust_level TEXT DEFAULT 'child'
        CHECK(trust_level IN ('system', 'user', 'external', 'child')),
    injection_scan_result TEXT DEFAULT NULL,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_child_behaviors_child ON child_learned_behaviors(child_id);
CREATE INDEX IF NOT EXISTS idx_child_behaviors_eval ON child_learned_behaviors(evaluated);

-- Capability genome versions (D209 — semver + SHA-256)
CREATE TABLE IF NOT EXISTS genome_versions (
    id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    genome_data TEXT NOT NULL,
    change_type TEXT CHECK(change_type IN ('major', 'minor', 'patch', 'rollback')) DEFAULT 'minor',
    change_summary TEXT,
    parent_version TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_genome_versions_version ON genome_versions(version);
CREATE INDEX IF NOT EXISTS idx_genome_versions_hash ON genome_versions(content_hash);

-- Capability evaluations (6-dimension scoring, REQ-36-020)
CREATE TABLE IF NOT EXISTS capability_evaluations (
    id TEXT PRIMARY KEY,
    capability_id TEXT,
    capability_name TEXT NOT NULL,
    score REAL NOT NULL,
    dimensions_json TEXT NOT NULL,
    outcome TEXT CHECK(outcome IN ('auto_queue', 'recommend', 'log', 'archive')) NOT NULL,
    rationale TEXT,
    evaluator TEXT,
    source_type TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cap_evals_outcome ON capability_evaluations(outcome);

-- Staging environments (D211 — git worktree isolation)
CREATE TABLE IF NOT EXISTS staging_environments (
    id TEXT PRIMARY KEY,
    capability_id TEXT NOT NULL,
    genome_version TEXT,
    worktree_path TEXT,
    branch_name TEXT,
    status TEXT CHECK(status IN ('created', 'testing', 'passed', 'failed', 'destroyed')) DEFAULT 'created',
    test_results_json TEXT,
    compliance_before_json TEXT,
    compliance_after_json TEXT,
    compliance_preserved INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT,
    destroyed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_staging_status ON staging_environments(status);

-- Propagation log (D214 — append-only HITL deployment)
CREATE TABLE IF NOT EXISTS propagation_log (
    id TEXT PRIMARY KEY,
    capability_id TEXT NOT NULL,
    capability_name TEXT,
    source_type TEXT,
    source_child_id TEXT,
    target_child_id TEXT,
    target_children_json TEXT,
    status TEXT CHECK(status IN ('prepared', 'approved', 'executing', 'completed', 'failed', 'rolled_back')) DEFAULT 'prepared',
    propagation_status TEXT,
    genome_version TEXT,
    genome_version_before TEXT,
    genome_version_after TEXT,
    rollback_plan TEXT,
    prepared_by TEXT,
    initiated_by TEXT,
    initiated_at TEXT,
    approved_by TEXT,
    approved_at TEXT,
    executed_by TEXT,
    executed_at TEXT,
    completed_at TEXT,
    rollback_reason TEXT,
    rolled_back_at TEXT,
    rolled_back_by TEXT,
    execution_results_json TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_propagation_status ON propagation_log(status);
CREATE INDEX IF NOT EXISTS idx_propagation_cap ON propagation_log(capability_id);

-- ============================================================
-- PHASE 38: CLOUD-AGNOSTIC (Multi-Cloud Provider Status)
-- ============================================================

-- Cloud provider health status
CREATE TABLE IF NOT EXISTS cloud_provider_status (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    service TEXT NOT NULL,
    status TEXT CHECK(status IN ('healthy', 'degraded', 'unhealthy', 'unavailable')) DEFAULT 'healthy',
    latency_ms INTEGER,
    details TEXT,
    checked_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cloud_status_provider ON cloud_provider_status(provider);
CREATE INDEX IF NOT EXISTS idx_cloud_status_service ON cloud_provider_status(service);

-- ============================================================
-- CLOUD TENANT CSP CONFIG — per-tenant CSP overrides (D225, D60)
-- ============================================================
CREATE TABLE IF NOT EXISTS cloud_tenant_csp_config (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    service TEXT NOT NULL
        CHECK(service IN ('secrets', 'storage', 'kms', 'monitoring', 'iam', 'registry', 'global')),
    provider TEXT NOT NULL
        CHECK(provider IN ('aws', 'azure', 'gcp', 'oci', 'ibm', 'local')),
    config_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(tenant_id, service)
);

CREATE INDEX IF NOT EXISTS idx_cloud_tenant_config_tenant ON cloud_tenant_csp_config(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cloud_tenant_config_service ON cloud_tenant_csp_config(service);

-- ============================================================
-- CSP REGION CERTIFICATIONS — compliance certification registry (D233)
-- ============================================================
CREATE TABLE IF NOT EXISTS csp_region_certifications (
    id TEXT PRIMARY KEY,
    csp TEXT NOT NULL CHECK(csp IN ('aws', 'azure', 'gcp', 'oci', 'ibm')),
    region TEXT NOT NULL,
    certification TEXT NOT NULL,
    certification_level TEXT DEFAULT '',
    impact_levels TEXT DEFAULT '[]',
    verified_at TEXT,
    expires_at TEXT,
    source_url TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(csp, region, certification)
);

CREATE INDEX IF NOT EXISTS idx_csp_certs_csp ON csp_region_certifications(csp);
CREATE INDEX IF NOT EXISTS idx_csp_certs_region ON csp_region_certifications(region);
CREATE INDEX IF NOT EXISTS idx_csp_certs_cert ON csp_region_certifications(certification);

-- ============================================================
-- CROSS-LANGUAGE TRANSLATION (Phase 43)
-- ============================================================

-- Translation jobs — one row per pipeline invocation (D251)
CREATE TABLE IF NOT EXISTS translation_jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    source_language TEXT NOT NULL,
    target_language TEXT NOT NULL,
    source_path TEXT NOT NULL,
    output_path TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK(status IN (
        'pending','extracting','type_checking','translating',
        'assembling','validating','repairing','completed','failed','partial'
    )),
    total_units INTEGER DEFAULT 0,
    translated_units INTEGER DEFAULT 0,
    mocked_units INTEGER DEFAULT 0,
    failed_units INTEGER DEFAULT 0,
    source_loc INTEGER DEFAULT 0,
    target_loc INTEGER DEFAULT 0,
    llm_model TEXT,
    llm_provider TEXT,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    estimated_cost_usd REAL DEFAULT 0.0,
    candidates_per_unit INTEGER DEFAULT 3,
    api_surface_match REAL,
    type_coverage REAL,
    round_trip_similarity REAL,
    complexity_increase_pct REAL,
    compliance_coverage_pct REAL,
    validation_passed INTEGER DEFAULT 0,
    gate_result TEXT CHECK(gate_result IN ('passed','failed','warning',NULL)),
    error_message TEXT,
    dry_run INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    created_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_translation_job_project ON translation_jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_translation_job_status ON translation_jobs(status);
CREATE INDEX IF NOT EXISTS idx_translation_job_langs ON translation_jobs(source_language, target_language);

-- Translation units — individual code units (function/class/interface/enum)
CREATE TABLE IF NOT EXISTS translation_units (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES translation_jobs(id),
    name TEXT NOT NULL,
    unit_name TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('function','class','interface','enum','struct','trait','module')),
    unit_kind TEXT,
    file_path TEXT,
    source_file TEXT,
    line_start INTEGER,
    line_end INTEGER,
    source_code TEXT,
    translated_code TEXT,
    source_hash TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN (
        'pending','translating','translated','mocked','failed','skipped'
    )),
    idioms TEXT,
    source_complexity INTEGER DEFAULT 1,
    target_complexity INTEGER,
    retry_count INTEGER DEFAULT 0,
    repair_attempts INTEGER DEFAULT 0,
    candidate_count INTEGER DEFAULT 0,
    candidate_selected INTEGER,
    selected_candidate INTEGER,
    error_message TEXT,
    translation_order INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    translated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_translation_unit_job ON translation_units(job_id);
CREATE INDEX IF NOT EXISTS idx_translation_unit_status ON translation_units(status);

-- Translation dependency mappings — per-job dependency resolutions
CREATE TABLE IF NOT EXISTS translation_dependency_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES translation_jobs(id),
    source_import TEXT NOT NULL,
    target_import TEXT,
    mapping_source TEXT DEFAULT 'unmapped' CHECK(mapping_source IN (
        'table','llm_suggested','manual','unmapped','stdlib'
    )),
    confidence REAL DEFAULT 0.0,
    domain TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_translation_dep_job ON translation_dependency_mappings(job_id);
CREATE INDEX IF NOT EXISTS idx_translation_dep_source ON translation_dependency_mappings(mapping_source);

-- Translation validations — per-job validation results by check type
CREATE TABLE IF NOT EXISTS translation_validations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES translation_jobs(id),
    check_type TEXT NOT NULL CHECK(check_type IN (
        'syntax','lint','round_trip','api_surface',
        'type_coverage','complexity','compliance','feature_mapping'
    )),
    passed INTEGER DEFAULT 0,
    score REAL,
    issue_count INTEGER DEFAULT 0,
    findings TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_translation_val_job ON translation_validations(job_id);
CREATE INDEX IF NOT EXISTS idx_translation_val_check ON translation_validations(check_type);

-- ============================================================
-- Phase 44: Multi-Stream Parallel Chat (D257-D260)
-- ============================================================
CREATE TABLE IF NOT EXISTS chat_contexts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT,
    title TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','completed','error','archived')),
    intake_session_id TEXT,
    project_id TEXT,
    agent_model TEXT DEFAULT 'sonnet',
    system_prompt TEXT,
    context_config TEXT,
    dirty_version INTEGER DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    last_activity_at TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_ctx_user ON chat_contexts(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_ctx_tenant ON chat_contexts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chat_ctx_status ON chat_contexts(status);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id TEXT NOT NULL REFERENCES chat_contexts(id),
    turn_number INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','system','intervention')),
    content TEXT NOT NULL,
    content_type TEXT DEFAULT 'text' CHECK(content_type IN ('text','tool_result','error','intervention','summary','code_block','markdown','phase_transition','citation','action_card')),
    metadata TEXT,
    is_compressed INTEGER DEFAULT 0,
    compression_tier TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_msg_ctx ON chat_messages(context_id);
CREATE INDEX IF NOT EXISTS idx_chat_msg_turn ON chat_messages(context_id, turn_number);

CREATE TABLE IF NOT EXISTS chat_tasks (
    id TEXT PRIMARY KEY,
    context_id TEXT NOT NULL REFERENCES chat_contexts(id),
    task_type TEXT NOT NULL CHECK(task_type IN ('message','intervention','tool_call','summary')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','processing','completed','failed','cancelled')),
    input_text TEXT,
    output_text TEXT,
    error_message TEXT,
    checkpoint TEXT,
    duration_ms INTEGER,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_task_ctx ON chat_tasks(context_id);
CREATE INDEX IF NOT EXISTS idx_chat_task_status ON chat_tasks(status);

CREATE TABLE IF NOT EXISTS chat_corrections (
    id SERIAL PRIMARY KEY,
    context_id TEXT NOT NULL,
    correction_text TEXT NOT NULL,
    turn_number INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_corrections_ctx ON chat_corrections(context_id, created_at DESC);

-- ============================================================
-- Phase 69: Codebase Assistant (D-CA-1 to D-CA-10)
-- ============================================================

-- Codebase file index for assistant widget
CREATE TABLE IF NOT EXISTS codebase_index (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    file_type TEXT NOT NULL CHECK(file_type IN ('python','template','goal','config','docs','args')),
    module TEXT,
    symbols TEXT,
    last_indexed_at TEXT DEFAULT (datetime('now')),
    chunk_count INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_codebase_module ON codebase_index(module);
CREATE INDEX IF NOT EXISTS idx_codebase_hash ON codebase_index(file_hash);

-- Q&A cache for popular codebase questions (D-CA-6)
CREATE TABLE IF NOT EXISTS codebase_qa_cache (
    id TEXT PRIMARY KEY,
    question_hash TEXT NOT NULL,
    question_text TEXT NOT NULL,
    question TEXT,
    answer_text TEXT NOT NULL,
    answer TEXT,
    source_citations TEXT,
    citations TEXT DEFAULT '[]',
    scope TEXT,
    hit_count INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    last_hit_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_qa_cache_hash ON codebase_qa_cache(question_hash);

-- ============================================================
-- Phase 44: Active Extension Hooks (D261-D264)
-- ============================================================
CREATE TABLE IF NOT EXISTS extension_registry (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hook_point TEXT NOT NULL,
    priority INTEGER DEFAULT 500,
    file_path TEXT,
    scope TEXT DEFAULT 'default' CHECK(scope IN ('default','tenant','project')),
    scope_id TEXT,
    allow_modification INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    description TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ext_reg_hook ON extension_registry(hook_point);
CREATE INDEX IF NOT EXISTS idx_ext_reg_scope ON extension_registry(scope, scope_id);

-- Phase 44: Extension execution log (D261-D264, append-only)
CREATE TABLE IF NOT EXISTS extension_execution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    extension_id TEXT REFERENCES extension_registry(id),
    hook_point TEXT NOT NULL,
    context_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('success','error','skipped','timeout')),
    duration_ms INTEGER,
    error_message TEXT,
    modified_data INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ext_exec_ext ON extension_execution_log(extension_id);
CREATE INDEX IF NOT EXISTS idx_ext_exec_hook ON extension_execution_log(hook_point);

-- ============================================================
-- Phase 44: Memory System Core Tables
-- ============================================================
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
    source TEXT DEFAULT 'manual',
    decay_weight REAL DEFAULT 1.0,
    classification TEXT DEFAULT 'CUI',
    compartment TEXT DEFAULT '',
    tags TEXT,
    -- The live column is `topics` — see db/schema/pg_consolidated.sql. This SQLite
    -- init path still only declared the legacy `tags`, so a fresh SQLite database
    -- lacked the column every memory writer actually names (swp-scan-01).
    -- Keep `);` out of DDL comments: regex schema readers capture the table body
    -- non-greedily up to the first one and would truncate the column list here.
    topics TEXT,
    metadata TEXT
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

-- Phase 44: AI-Driven Memory Consolidation (D276, append-only)
-- ============================================================
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

-- Phase 44: Auto-capture buffer (D181) — migrated from memory.db
-- ============================================================
CREATE TABLE IF NOT EXISTS memory_buffer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    type TEXT DEFAULT 'event',
    importance INTEGER DEFAULT 3,
    source TEXT NOT NULL DEFAULT 'hook'
        CHECK(source IN ('hook', 'manual', 'thinking', 'auto')),
    user_id TEXT,
    tenant_id TEXT,
    session_id TEXT,
    tool_name TEXT,
    metadata TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_buffer_created ON memory_buffer(created_at);
CREATE INDEX IF NOT EXISTS idx_buffer_source ON memory_buffer(source);
CREATE INDEX IF NOT EXISTS idx_buffer_user ON memory_buffer(user_id);

-- ============================================================
-- Activity / Task Tracking — migrated from activity.db
-- ============================================================
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'pending'
        CHECK(status IN ('pending', 'in_progress', 'completed', 'cancelled', 'blocked')),
    priority TEXT DEFAULT 'medium'
        CHECK(priority IN ('low', 'medium', 'high', 'critical')),
    project_id TEXT,
    agent_id TEXT,
    session_id TEXT,
    parent_task_id INTEGER REFERENCES tasks(id),
    metadata TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);

-- ============================================================
-- Migration 018: Activity task tracking (consolidated from activity.db)
-- ============================================================
CREATE TABLE IF NOT EXISTS activity_tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    description  TEXT,
    status       TEXT DEFAULT 'pending'
        CHECK(status IN ('pending','in_progress','completed','cancelled')),
    priority     INTEGER DEFAULT 5,
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_activity_tasks_status ON activity_tasks(status);

-- ============================================================
-- Phase 45: OWASP Agentic AI Security (D257-D264)
-- ============================================================

-- Gap 2: Tool Chain Validation — append-only event log (D258)
CREATE TABLE IF NOT EXISTS tool_chain_events (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_sequence_json TEXT NOT NULL,
    rule_matched TEXT,
    severity TEXT DEFAULT 'info' CHECK(severity IN ('info','low','medium','high','critical')),
    action TEXT DEFAULT 'allow' CHECK(action IN ('allow','warn','flag','block')),
    context_json TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tce_agent ON tool_chain_events(agent_id);
CREATE INDEX IF NOT EXISTS idx_tce_session ON tool_chain_events(session_id);
CREATE INDEX IF NOT EXISTS idx_tce_severity ON tool_chain_events(severity);
CREATE INDEX IF NOT EXISTS idx_tce_created ON tool_chain_events(created_at);

-- Gap 5: Agent Trust Scoring — append-only score history (D260)
CREATE TABLE IF NOT EXISTS agent_trust_scores (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    project_id TEXT,
    trust_score REAL NOT NULL,
    previous_score REAL,
    score_delta REAL,
    factor_json TEXT NOT NULL,
    trigger_event TEXT NOT NULL,
    trigger_event_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ats_agent ON agent_trust_scores(agent_id);
CREATE INDEX IF NOT EXISTS idx_ats_project ON agent_trust_scores(project_id);
CREATE INDEX IF NOT EXISTS idx_ats_created ON agent_trust_scores(created_at);

-- Gap 3: Agent Output Violations — append-only violation log (D259)
CREATE TABLE IF NOT EXISTS agent_output_violations (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    agent_id TEXT NOT NULL,
    tool_name TEXT,
    violation_type TEXT NOT NULL,
    severity TEXT DEFAULT 'medium' CHECK(severity IN ('low','medium','high','critical')),
    details_json TEXT,
    output_hash TEXT,
    action_taken TEXT DEFAULT 'logged' CHECK(action_taken IN ('logged','warned','flagged','blocked')),
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_aov_agent ON agent_output_violations(agent_id);
CREATE INDEX IF NOT EXISTS idx_aov_project ON agent_output_violations(project_id);
CREATE INDEX IF NOT EXISTS idx_aov_severity ON agent_output_violations(severity);
CREATE INDEX IF NOT EXISTS idx_aov_created ON agent_output_violations(created_at);

-- ============================================================
-- Phase 46: Observability, Traceability & Explainable AI (D280-D290)
-- ============================================================

-- D280: OTel-compatible span storage (append-only, D6)
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
CREATE INDEX IF NOT EXISTS idx_otel_trace ON otel_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_otel_parent ON otel_spans(parent_span_id);
CREATE INDEX IF NOT EXISTS idx_otel_name ON otel_spans(name);
CREATE INDEX IF NOT EXISTS idx_otel_agent ON otel_spans(agent_id);
CREATE INDEX IF NOT EXISTS idx_otel_project ON otel_spans(project_id);
CREATE INDEX IF NOT EXISTS idx_otel_start ON otel_spans(start_time);
CREATE INDEX IF NOT EXISTS idx_otel_created ON otel_spans(created_at);

-- D287: PROV-AGENT provenance — entities (append-only, D6)
CREATE TABLE IF NOT EXISTS prov_entities (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    label TEXT,
    content_hash TEXT,
    content TEXT,
    attributes TEXT,
    trace_id TEXT,
    span_id TEXT,
    agent_id TEXT,
    project_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_prov_ent_type ON prov_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_prov_ent_trace ON prov_entities(trace_id);
CREATE INDEX IF NOT EXISTS idx_prov_ent_project ON prov_entities(project_id);
CREATE INDEX IF NOT EXISTS idx_prov_ent_created ON prov_entities(created_at);

-- D287: PROV-AGENT provenance — activities (append-only, D6)
CREATE TABLE IF NOT EXISTS prov_activities (
    id TEXT PRIMARY KEY,
    activity_type TEXT NOT NULL,
    label TEXT,
    start_time TEXT,
    end_time TEXT,
    attributes TEXT,
    trace_id TEXT,
    span_id TEXT,
    agent_id TEXT,
    project_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_prov_act_type ON prov_activities(activity_type);
CREATE INDEX IF NOT EXISTS idx_prov_act_trace ON prov_activities(trace_id);
CREATE INDEX IF NOT EXISTS idx_prov_act_project ON prov_activities(project_id);
CREATE INDEX IF NOT EXISTS idx_prov_act_created ON prov_activities(created_at);

-- D287: PROV-AGENT provenance — relations (append-only, D6)
CREATE TABLE IF NOT EXISTS prov_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    relation_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    object_id TEXT NOT NULL,
    attributes TEXT,
    trace_id TEXT,
    project_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_prov_rel_type ON prov_relations(relation_type);
CREATE INDEX IF NOT EXISTS idx_prov_rel_subject ON prov_relations(subject_id);
CREATE INDEX IF NOT EXISTS idx_prov_rel_object ON prov_relations(object_id);
CREATE INDEX IF NOT EXISTS idx_prov_rel_trace ON prov_relations(trace_id);
CREATE INDEX IF NOT EXISTS idx_prov_rel_project ON prov_relations(project_id);

-- D288: AgentSHAP tool attribution (append-only, D6)
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
CREATE INDEX IF NOT EXISTS idx_shap_trace ON shap_attributions(trace_id);
CREATE INDEX IF NOT EXISTS idx_shap_tool ON shap_attributions(tool_name);
CREATE INDEX IF NOT EXISTS idx_shap_project ON shap_attributions(project_id);
CREATE INDEX IF NOT EXISTS idx_shap_analyzed ON shap_attributions(analyzed_at);

-- D289: XAI compliance assessments (append-only, D6)
CREATE TABLE IF NOT EXISTS xai_assessments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    assessment_date TEXT NOT NULL,
    overall_status TEXT NOT NULL DEFAULT 'not_assessed',
    overall_score REAL DEFAULT 0.0,
    checks_json TEXT,
    findings_json TEXT,
    recommendations_json TEXT,
    framework_crosswalk TEXT,
    assessor_version TEXT,
    agent_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_xai_project ON xai_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_xai_date ON xai_assessments(assessment_date);
CREATE INDEX IF NOT EXISTS idx_xai_status ON xai_assessments(overall_status);
CREATE INDEX IF NOT EXISTS idx_xai_created ON xai_assessments(created_at);

-- ── Production Readiness Audit (D291-D295) ──────────────────────────────
-- Append-only audit trail for production readiness checks.
CREATE TABLE IF NOT EXISTS production_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    overall_pass INTEGER NOT NULL,
    total_checks INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    failed INTEGER NOT NULL,
    warned INTEGER NOT NULL,
    skipped INTEGER NOT NULL,
    blockers TEXT,
    warnings TEXT,
    categories_run TEXT,
    report_json TEXT,
    duration_ms INTEGER,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_prod_audit_created ON production_audits(created_at);

-- Phase 47 — Production Remediation (D296-D300, append-only)
CREATE TABLE IF NOT EXISTS remediation_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_audit_id INTEGER,
    check_id TEXT NOT NULL,
    check_name TEXT NOT NULL,
    category TEXT NOT NULL,
    confidence REAL NOT NULL,
    tier TEXT NOT NULL,
    status TEXT NOT NULL,
    fix_strategy TEXT NOT NULL,
    fix_command TEXT,
    message TEXT,
    details TEXT,
    duration_ms INTEGER DEFAULT 0,
    verification_check_id TEXT,
    verification_status TEXT,
    verification_message TEXT,
    dry_run INTEGER DEFAULT 0,
    report_json TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_remediation_check ON remediation_audit_log(check_id);
CREATE INDEX IF NOT EXISTS idx_remediation_status ON remediation_audit_log(status);
CREATE INDEX IF NOT EXISTS idx_remediation_tier ON remediation_audit_log(tier);
CREATE INDEX IF NOT EXISTS idx_remediation_created ON remediation_audit_log(created_at);

-- ── OSCAL Ecosystem Validation Log (D306) ────────────────────────────────
-- Append-only log of all OSCAL validation attempts (structural, pydantic, Metaschema).
CREATE TABLE IF NOT EXISTS oscal_validation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    artifact_type TEXT,
    validator TEXT NOT NULL,
    valid INTEGER NOT NULL,
    error_count INTEGER DEFAULT 0,
    errors TEXT,
    duration_ms INTEGER DEFAULT 0,
    project_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oscal_val_file ON oscal_validation_log(file_path);
CREATE INDEX IF NOT EXISTS idx_oscal_val_validator ON oscal_validation_log(validator);
CREATE INDEX IF NOT EXISTS idx_oscal_val_project ON oscal_validation_log(project_id);
CREATE INDEX IF NOT EXISTS idx_oscal_val_created ON oscal_validation_log(created_at);

-- ============================================================
-- AI TRANSPARENCY & ACCOUNTABILITY (Phase 48, D307-D315)
-- ============================================================

-- ── OMB M-25-21 Assessments (BaseAssessor standard schema) ──
CREATE TABLE IF NOT EXISTS omb_m25_21_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_assessed'
        CHECK(status IN ('satisfied', 'partially_satisfied', 'not_satisfied', 'not_assessed', 'not_applicable')),
    evidence TEXT,
    notes TEXT,
    nist_800_53_crosswalk TEXT,
    crosswalk_status TEXT,
    assessed_by TEXT DEFAULT 'icdev-compliance-engine',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_omb2521_project ON omb_m25_21_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_omb2521_requirement ON omb_m25_21_assessments(requirement_id);

-- ── OMB M-26-04 Assessments (BaseAssessor standard schema) ──
CREATE TABLE IF NOT EXISTS omb_m26_04_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_assessed'
        CHECK(status IN ('satisfied', 'partially_satisfied', 'not_satisfied', 'not_assessed', 'not_applicable')),
    evidence TEXT,
    notes TEXT,
    nist_800_53_crosswalk TEXT,
    crosswalk_status TEXT,
    assessed_by TEXT DEFAULT 'icdev-compliance-engine',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_omb2604_project ON omb_m26_04_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_omb2604_requirement ON omb_m26_04_assessments(requirement_id);

-- ── NIST AI 600-1 Assessments (BaseAssessor standard schema) ──
CREATE TABLE IF NOT EXISTS nist_ai_600_1_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_assessed'
        CHECK(status IN ('satisfied', 'partially_satisfied', 'not_satisfied', 'not_assessed', 'not_applicable')),
    evidence TEXT,
    notes TEXT,
    nist_800_53_crosswalk TEXT,
    crosswalk_status TEXT,
    assessed_by TEXT DEFAULT 'icdev-compliance-engine',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai6001_project ON nist_ai_600_1_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_ai6001_requirement ON nist_ai_600_1_assessments(requirement_id);

-- ── GAO AI Assessments (BaseAssessor standard schema) ──
CREATE TABLE IF NOT EXISTS gao_ai_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_assessed'
        CHECK(status IN ('satisfied', 'partially_satisfied', 'not_satisfied', 'not_assessed', 'not_applicable')),
    evidence TEXT,
    notes TEXT,
    nist_800_53_crosswalk TEXT,
    crosswalk_status TEXT,
    assessed_by TEXT DEFAULT 'icdev-compliance-engine',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gaoai_project ON gao_ai_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_gaoai_requirement ON gao_ai_assessments(requirement_id);

-- ── Model Cards (OMB M-26-04, Google Model Cards format) ──
CREATE TABLE IF NOT EXISTS model_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    card_data TEXT NOT NULL,
    card_hash TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, model_name, version)
);
CREATE INDEX IF NOT EXISTS idx_model_cards_project ON model_cards(project_id);
CREATE INDEX IF NOT EXISTS idx_model_cards_model ON model_cards(model_name);

-- ── System Cards (ICDEV™ system-level AI documentation) ──
CREATE TABLE IF NOT EXISTS system_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    card_data TEXT NOT NULL,
    card_hash TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_system_cards_project ON system_cards(project_id);

-- ── Confabulation Checks (NIST AI 600-1 GAI.1, append-only) ──
CREATE TABLE IF NOT EXISTS confabulation_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    check_type TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    result TEXT NOT NULL,
    risk_score REAL DEFAULT 0.0,
    findings_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_confab_project ON confabulation_checks(project_id);
CREATE INDEX IF NOT EXISTS idx_confab_created ON confabulation_checks(created_at);

-- ── AI Use Case Inventory (OMB M-25-21 public inventory) ──
CREATE TABLE IF NOT EXISTS ai_use_case_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    purpose TEXT,
    risk_level TEXT DEFAULT 'minimal_risk'
        CHECK(risk_level IN ('minimal_risk', 'high_impact', 'safety_impacting')),
    classification TEXT DEFAULT 'CUI',
    deployment_status TEXT DEFAULT 'development',
    responsible_official TEXT,
    oversight_role TEXT,
    appeal_mechanism TEXT,
    last_assessed TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, name)
);
CREATE INDEX IF NOT EXISTS idx_ai_inventory_project ON ai_use_case_inventory(project_id);

-- ── Fairness Assessments (OMB M-26-04 bias/fairness evidence, append-only) ──
CREATE TABLE IF NOT EXISTS fairness_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    status TEXT DEFAULT 'not_assessed',
    evidence TEXT,
    score REAL DEFAULT 0.0,
    assessed_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    UNIQUE(project_id, dimension)
);
CREATE INDEX IF NOT EXISTS idx_fairness_project ON fairness_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_fairness_assessed ON fairness_assessments(assessed_at);

-- ============================================================
-- AI ACCOUNTABILITY (Phase 49, D316-D321)
-- ============================================================

-- ── AI Oversight Plans (M25-OVR-1, GAO accountability, append-only) ──
CREATE TABLE IF NOT EXISTS ai_oversight_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    plan_name TEXT NOT NULL,
    plan_data TEXT,
    description TEXT DEFAULT '',
    approval_status TEXT DEFAULT 'draft'
        CHECK(approval_status IN ('draft', 'submitted', 'approved', 'rejected')),
    approved_by TEXT,
    created_by TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_oversight_project ON ai_oversight_plans(project_id);

-- ── AI Accountability Appeals (M25-OVR-3, M26-REV-2, FAIR-7, append-only) ──
CREATE TABLE IF NOT EXISTS ai_accountability_appeals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    appellant TEXT NOT NULL,
    ai_system TEXT NOT NULL,
    decision_contested TEXT,
    grievance TEXT DEFAULT '',
    appeal_status TEXT DEFAULT 'submitted'
        CHECK(appeal_status IN ('submitted', 'under_review', 'resolved', 'dismissed')),
    status TEXT DEFAULT 'submitted',
    resolution TEXT,
    resolved_by TEXT,
    filed_at TEXT DEFAULT (datetime('now')),
    resolved_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_appeals_project ON ai_accountability_appeals(project_id);
CREATE INDEX IF NOT EXISTS idx_ai_appeals_status ON ai_accountability_appeals(appeal_status);

-- ── AI CAIO Registry (M25-OVR-4, Chief AI Officer tracking) ──
CREATE TABLE IF NOT EXISTS ai_caio_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    official_name TEXT,
    official_role TEXT DEFAULT 'CAIO',
    name TEXT,
    role TEXT DEFAULT 'CAIO',
    organization TEXT,
    designation_date TEXT,
    appointment_date TEXT DEFAULT (datetime('now')),
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_caio_project ON ai_caio_registry(project_id);

-- ── AI Incident Log (M25-RISK-4, GAO-MON-3, append-only) ──
CREATE TABLE IF NOT EXISTS ai_incident_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    incident_type TEXT NOT NULL
        CHECK(incident_type IN ('confabulation', 'bias_detected', 'unauthorized_access',
              'model_drift', 'data_breach', 'safety_violation', 'appeal_escalation', 'other')),
    ai_system TEXT,
    severity TEXT DEFAULT 'medium'
        CHECK(severity IN ('critical', 'high', 'medium', 'low')),
    description TEXT NOT NULL,
    corrective_action TEXT,
    status TEXT DEFAULT 'open'
        CHECK(status IN ('open', 'investigating', 'mitigated', 'resolved', 'closed')),
    reported_by TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_incident_project ON ai_incident_log(project_id);
CREATE INDEX IF NOT EXISTS idx_ai_incident_status ON ai_incident_log(status);
CREATE INDEX IF NOT EXISTS idx_ai_incident_severity ON ai_incident_log(severity);

-- ── AI Reassessment Schedule (M25-INV-3, GAO-MON-4) ──
CREATE TABLE IF NOT EXISTS ai_reassessment_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    ai_system TEXT NOT NULL,
    frequency TEXT NOT NULL DEFAULT 'annual'
        CHECK(frequency IN ('quarterly', 'semi_annual', 'annual', 'biennial')),
    next_due TEXT,
    last_completed TEXT,
    last_assessed TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, ai_system)
);
CREATE INDEX IF NOT EXISTS idx_ai_reassess_project ON ai_reassessment_schedule(project_id);
CREATE INDEX IF NOT EXISTS idx_ai_reassess_due ON ai_reassessment_schedule(next_due);

-- ── AI Ethics Reviews (GAO-GOV-2, GAO-GOV-3, M26-REV-3, FAIR-1, append-only) ──
CREATE TABLE IF NOT EXISTS ai_ethics_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    review_type TEXT NOT NULL
        CHECK(review_type IN ('bias_testing_policy', 'impact_assessment', 'ethics_framework',
              'legal_compliance', 'pre_deployment', 'annual_review', 'other')),
    ai_system TEXT,
    summary TEXT DEFAULT '',
    findings TEXT,
    recommendation TEXT DEFAULT '',
    opt_out_policy INTEGER DEFAULT 0,
    legal_compliance_matrix INTEGER DEFAULT 0,
    pre_deployment_review INTEGER DEFAULT 0,
    reviewer TEXT,
    status TEXT DEFAULT 'submitted',
    submitted_at TEXT DEFAULT (datetime('now')),
    reviewed_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_ethics_project ON ai_ethics_reviews(project_id);
CREATE INDEX IF NOT EXISTS idx_ai_ethics_type ON ai_ethics_reviews(review_type);

-- ============================================================
-- CODE INTELLIGENCE (Phase 52 — D331-D337)
-- ============================================================

-- ── Code Quality Metrics (append-only time-series, D332) ──
CREATE TABLE IF NOT EXISTS code_quality_metrics (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    file_path TEXT NOT NULL,
    function_name TEXT,
    class_name TEXT,
    language TEXT NOT NULL,
    cyclomatic_complexity INTEGER DEFAULT 0,
    cognitive_complexity INTEGER DEFAULT 0,
    loc INTEGER DEFAULT 0,
    loc_code INTEGER DEFAULT 0,
    loc_comment INTEGER DEFAULT 0,
    parameter_count INTEGER DEFAULT 0,
    nesting_depth INTEGER DEFAULT 0,
    import_count INTEGER DEFAULT 0,
    class_count INTEGER DEFAULT 0,
    function_count INTEGER DEFAULT 0,
    total_functions INTEGER,
    avg_cyclomatic REAL,
    avg_cognitive REAL,
    avg_nesting REAL,
    avg_params REAL,
    avg_loc REAL,
    smells_json TEXT DEFAULT '[]',
    smells TEXT,
    smell_count INTEGER DEFAULT 0,
    maintainability_score REAL DEFAULT 0.0,
    content_hash TEXT,
    scan_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cqm_project ON code_quality_metrics(project_id);
CREATE INDEX IF NOT EXISTS idx_cqm_scan ON code_quality_metrics(scan_id);
CREATE INDEX IF NOT EXISTS idx_cqm_file ON code_quality_metrics(file_path);

-- ── Runtime Feedback (append-only test-to-source correlation, D332/D334) ──
CREATE TABLE IF NOT EXISTS runtime_feedback (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    source_file TEXT NOT NULL,
    source_function TEXT,
    test_file TEXT,
    test_function TEXT,
    test_passed INTEGER,
    test_duration_ms REAL,
    error_type TEXT,
    error_message TEXT,
    coverage_pct REAL,
    run_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_rf_project ON runtime_feedback(project_id);
CREATE INDEX IF NOT EXISTS idx_rf_run ON runtime_feedback(run_id);
CREATE INDEX IF NOT EXISTS idx_rf_source_fn ON runtime_feedback(source_function);

-- Phase 53: OWASP ASI01-ASI10 Assessments (D339) — BaseAssessor per-requirement schema
CREATE TABLE IF NOT EXISTS owasp_asi_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    family TEXT,
    status TEXT DEFAULT 'not_assessed' CHECK(status IN ('satisfied','partially_satisfied','not_satisfied','not_applicable','risk_accepted','not_assessed')),
    evidence_description TEXT,
    evidence_path TEXT,
    automation_result TEXT,
    notes TEXT,
    nist_800_53_crosswalk TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, requirement_id)
);
CREATE INDEX IF NOT EXISTS idx_owasp_asi_project ON owasp_asi_assessments(project_id);

-- Phase 57: EU AI Act Assessments (D349) — BaseAssessor per-requirement schema
CREATE TABLE IF NOT EXISTS eu_ai_act_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    family TEXT,
    status TEXT DEFAULT 'not_assessed' CHECK(status IN ('satisfied','partially_satisfied','not_satisfied','not_applicable','risk_accepted','not_assessed')),
    evidence_description TEXT,
    evidence_path TEXT,
    automation_result TEXT,
    notes TEXT,
    nist_800_53_crosswalk TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, requirement_id)
);
CREATE INDEX IF NOT EXISTS idx_eu_ai_act_project ON eu_ai_act_assessments(project_id);

-- ============================================================
-- CREATIVE ENGINE: Customer-Centric Feature Discovery (D351-D360)
-- ============================================================

-- Creative competitors — auto-discovered and manually confirmed competitor profiles
-- NOTE: This table allows UPDATE for status transitions (discovered -> confirmed -> archived) (D357)
CREATE TABLE IF NOT EXISTS creative_competitors (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT,
    source TEXT NOT NULL CHECK(source IN ('g2','capterra','trustradius','producthunt','manual')),
    source_url TEXT,
    rating REAL,
    review_count INTEGER DEFAULT 0,
    features TEXT DEFAULT '[]',
    pricing_tier TEXT,
    status TEXT NOT NULL DEFAULT 'discovered'
        CHECK(status IN ('discovered','confirmed','archived')),
    metadata TEXT DEFAULT '{}',
    discovered_at TEXT NOT NULL,
    confirmed_at TEXT,
    confirmed_by TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_creative_comp_domain ON creative_competitors(domain);
CREATE INDEX IF NOT EXISTS idx_creative_comp_status ON creative_competitors(status);

-- Creative signals — raw signals from review sites, forums, GitHub issues (append-only, D6)
CREATE TABLE IF NOT EXISTS creative_signals (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL CHECK(source IN ('g2','capterra','trustradius','reddit','github',
                                          'producthunt','govcon_blog','linkedin','stackoverflow')),
    source_type TEXT NOT NULL CHECK(source_type IN ('review','forum_post','issue','comment','launch','scan_error')),
    competitor_id TEXT REFERENCES creative_competitors(id),
    title TEXT NOT NULL,
    body TEXT,
    url TEXT,
    author TEXT,
    rating REAL,
    upvotes INTEGER DEFAULT 0,
    sentiment TEXT CHECK(sentiment IS NULL OR sentiment IN ('positive','negative','neutral','mixed')),
    content_hash TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    discovered_at TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_csig_source ON creative_signals(source);
CREATE INDEX IF NOT EXISTS idx_csig_competitor ON creative_signals(competitor_id);
CREATE INDEX IF NOT EXISTS idx_csig_hash ON creative_signals(content_hash);
CREATE INDEX IF NOT EXISTS idx_csig_discovered ON creative_signals(discovered_at);

-- Creative pain points — extracted and clustered pain points (append-only, D6)
CREATE TABLE IF NOT EXISTS creative_pain_points (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL CHECK(category IN ('ux','performance','integration','pricing','compliance',
        'security','reporting','customization','support','scalability','documentation',
        'onboarding','api','automation','other')),
    frequency INTEGER NOT NULL DEFAULT 1,
    signal_ids TEXT NOT NULL DEFAULT '[]',
    competitor_ids TEXT DEFAULT '[]',
    keyword_fingerprint TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    severity TEXT DEFAULT 'medium' CHECK(severity IN ('critical','high','medium','low')),
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','scored','spec_generated','addressed')),
    composite_score REAL,
    score_breakdown TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_cpp_category ON creative_pain_points(category);
CREATE INDEX IF NOT EXISTS idx_cpp_score ON creative_pain_points(composite_score);
CREATE INDEX IF NOT EXISTS idx_cpp_fingerprint ON creative_pain_points(keyword_fingerprint);

-- Creative feature gaps — features customers want that competitors lack (append-only, D6)
CREATE TABLE IF NOT EXISTS creative_feature_gaps (
    id TEXT PRIMARY KEY,
    pain_point_id TEXT REFERENCES creative_pain_points(id),
    feature_name TEXT NOT NULL,
    description TEXT NOT NULL,
    requested_by_count INTEGER DEFAULT 0,
    competitor_coverage TEXT DEFAULT '{}',
    gap_score REAL DEFAULT 0.0,
    market_demand REAL DEFAULT 0.0,
    signal_ids TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'identified'
        CHECK(status IN ('identified','validated','spec_generated','addressed','rejected')),
    metadata TEXT DEFAULT '{}',
    discovered_at TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_cfg_pain ON creative_feature_gaps(pain_point_id);
CREATE INDEX IF NOT EXISTS idx_cfg_gap ON creative_feature_gaps(gap_score);

-- Creative specs — generated feature specifications (append-only, D6)
CREATE TABLE IF NOT EXISTS creative_specs (
    id TEXT PRIMARY KEY,
    feature_gap_id TEXT REFERENCES creative_feature_gaps(id),
    pain_point_id TEXT REFERENCES creative_pain_points(id),
    title TEXT NOT NULL,
    spec_content TEXT NOT NULL,
    composite_score REAL NOT NULL,
    justification TEXT NOT NULL,
    estimated_effort TEXT NOT NULL CHECK(estimated_effort IN ('S','M','L','XL')),
    target_persona TEXT,
    competitive_advantage TEXT,
    status TEXT NOT NULL DEFAULT 'generated'
        CHECK(status IN ('generated','reviewed','approved','building','rejected')),
    reviewer TEXT,
    reviewed_at TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_cspec_score ON creative_specs(composite_score);
CREATE INDEX IF NOT EXISTS idx_cspec_status ON creative_specs(status);

-- Creative trends — trending pain points over time (append-only, D6)
CREATE TABLE IF NOT EXISTS creative_trends (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    pain_point_ids TEXT NOT NULL DEFAULT '[]',
    signal_count INTEGER NOT NULL DEFAULT 0,
    keyword_fingerprint TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    velocity REAL DEFAULT 0.0,
    acceleration REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'emerging'
        CHECK(status IN ('emerging','active','declining','stale')),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_ctrend_status ON creative_trends(status);
CREATE INDEX IF NOT EXISTS idx_ctrend_velocity ON creative_trends(velocity);

-- Creative gap — normalized creative engine inputs for ACF consumption (append-only)
CREATE TABLE IF NOT EXISTS creative_gap (
    id TEXT PRIMARY KEY,
    concept_id TEXT,
    source_ref TEXT,
    gap_type TEXT DEFAULT 'feature_gap'
        CHECK(gap_type IN ('feature_gap', 'ux_gap', 'integration_gap', 'performance_gap',
                           'security_gap', 'compliance_gap', 'documentation_gap', 'other')),
    score REAL DEFAULT 0.0,
    rank INTEGER,
    content_hash TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_creative_gap_concept ON creative_gap(concept_id);
CREATE INDEX IF NOT EXISTS idx_creative_gap_score ON creative_gap(score);
CREATE INDEX IF NOT EXISTS idx_creative_gap_hash ON creative_gap(content_hash);
CREATE INDEX IF NOT EXISTS idx_creative_gap_created ON creative_gap(created_at);

-- ============================================================
-- INDUSTRY RESEARCH ENGINE (Phase 63 — D-RES-1 through D-RES-13)
-- ============================================================

-- Research sessions — active research sessions with lifecycle state
-- NOTE: This table allows UPDATE for status transitions (D-RES-5)
CREATE TABLE IF NOT EXISTS research_sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    vertical_id TEXT NOT NULL,
    vertical_name TEXT NOT NULL,
    description TEXT,
    focus_areas TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'created'
        CHECK(status IN ('created','scoping','scanning','synthesizing',
                         'dossier_ready','reviewed','child_app_triggered','archived')),
    pipeline_stage TEXT DEFAULT 'SCOPE'
        CHECK(pipeline_stage IN ('SCOPE','LANDSCAPE','REGULATE','COMMUNITY',
                                  'ACADEMIC','BUILD_BUY','SYNTHESIZE','FORECAST','DOSSIER')),
    signal_count INTEGER DEFAULT 0,
    challenge_count INTEGER DEFAULT 0,
    dossier_id TEXT,
    config_overrides TEXT DEFAULT '{}',
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rsess_vertical ON research_sessions(vertical_id);
CREATE INDEX IF NOT EXISTS idx_rsess_status ON research_sessions(status);
CREATE INDEX IF NOT EXISTS idx_rsess_stage ON research_sessions(pipeline_stage);

-- Research verticals — industry vertical definitions loaded from JSON configs
-- NOTE: This table allows UPDATE for activation status changes (D-RES-5)
CREATE TABLE IF NOT EXISTS research_verticals (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    config_path TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    regulatory_bodies TEXT DEFAULT '[]',
    academic_categories TEXT DEFAULT '[]',
    community_sources TEXT DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1,
    session_count INTEGER DEFAULT 0,
    loaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rvert_slug ON research_verticals(slug);
CREATE INDEX IF NOT EXISTS idx_rvert_active ON research_verticals(active);

-- Research signals — discovered signals from all 8 data streams (append-only, D6/D-RES-5)
CREATE TABLE IF NOT EXISTS research_signals (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    source TEXT NOT NULL CHECK(source IN ('community_forum','review_site','academic_paper',
                                          'regulatory_body','open_source','saas_commercial',
                                          'news_blog','patent','video','manual')),
    source_type TEXT NOT NULL CHECK(source_type IN ('reddit','stackexchange','discord','forum',
                                                     'g2','capterra','trustpilot','domain_review',
                                                     'arxiv','ieee','acm','scholar',
                                                     'federal_register','regulations_gov','body_rss',
                                                     'github','awesome_list','package_registry',
                                                     'product_page','producthunt',
                                                     'news_article','analyst_report','blog',
                                                     'google_patent','uspto',
                                                     'youtube_search','youtube_manual','youtube_channel',
                                                     'manual_entry','scan_error')),
    title TEXT NOT NULL,
    body TEXT,
    url TEXT,
    author TEXT,
    upvotes INTEGER DEFAULT 0,
    citations INTEGER DEFAULT 0,
    sentiment TEXT CHECK(sentiment IS NULL OR sentiment IN ('positive','negative','neutral','mixed')),
    content_hash TEXT NOT NULL,
    keywords TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    discovered_at TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rsig_session ON research_signals(session_id);
CREATE INDEX IF NOT EXISTS idx_rsig_source ON research_signals(source);
CREATE INDEX IF NOT EXISTS idx_rsig_hash ON research_signals(content_hash);
CREATE INDEX IF NOT EXISTS idx_rsig_discovered ON research_signals(discovered_at);

-- Research challenges — clustered and scored challenges (append-only, D6/D-RES-5)
CREATE TABLE IF NOT EXISTS research_challenges (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    title TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL CHECK(category IN ('infrastructure','compliance','security','ux',
                                               'performance','integration','data','cost',
                                               'scalability','automation','governance','other')),
    signal_ids TEXT NOT NULL DEFAULT '[]',
    signal_count INTEGER NOT NULL DEFAULT 1,
    keyword_fingerprint TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    composite_score REAL,
    score_breakdown TEXT DEFAULT '{}',
    market_demand REAL DEFAULT 0.0,
    regulatory_pressure REAL DEFAULT 0.0,
    technical_complexity REAL DEFAULT 0.0,
    competitive_saturation REAL DEFAULT 0.0,
    icdev_readiness REAL DEFAULT 0.0,
    compliance_alignment REAL DEFAULT 0.0,
    severity TEXT DEFAULT 'notable'
        CHECK(severity IN ('critical','notable','appendix')),
    status TEXT NOT NULL DEFAULT 'new'
        CHECK(status IN ('new','scored','mapped','dossier_included')),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rchal_session ON research_challenges(session_id);
CREATE INDEX IF NOT EXISTS idx_rchal_score ON research_challenges(composite_score);
CREATE INDEX IF NOT EXISTS idx_rchal_category ON research_challenges(category);
CREATE INDEX IF NOT EXISTS idx_rchal_fingerprint ON research_challenges(keyword_fingerprint);

-- Research regulatory map — regulation-to-ICDEV™ crosswalk mappings (append-only, D6/D-RES-5)
CREATE TABLE IF NOT EXISTS research_regulatory_map (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    challenge_id TEXT REFERENCES research_challenges(id),
    regulatory_body TEXT NOT NULL,
    regulation_name TEXT NOT NULL,
    regulation_id TEXT,
    regulation_url TEXT,
    enforcement_actions INTEGER DEFAULT 0,
    deadline TEXT,
    nist_controls TEXT DEFAULT '[]',
    icdev_frameworks TEXT DEFAULT '[]',
    crosswalk_coverage REAL DEFAULT 0.0,
    gap_analysis TEXT DEFAULT '{}',
    metadata TEXT DEFAULT '{}',
    mapped_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rregmap_session ON research_regulatory_map(session_id);
CREATE INDEX IF NOT EXISTS idx_rregmap_challenge ON research_regulatory_map(challenge_id);
CREATE INDEX IF NOT EXISTS idx_rregmap_body ON research_regulatory_map(regulatory_body);

-- Research build/buy — build/buy/partner decision matrix (append-only, D6/D-RES-5)
CREATE TABLE IF NOT EXISTS research_build_buy (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    challenge_id TEXT NOT NULL REFERENCES research_challenges(id),
    recommendation TEXT NOT NULL CHECK(recommendation IN ('build','buy','partner','hybrid')),
    build_score REAL DEFAULT 0.0,
    buy_score REAL DEFAULT 0.0,
    partner_score REAL DEFAULT 0.0,
    build_rationale TEXT,
    buy_rationale TEXT,
    partner_rationale TEXT,
    existing_solutions TEXT DEFAULT '[]',
    icdev_capability_coverage REAL DEFAULT 0.0,
    estimated_effort TEXT CHECK(estimated_effort IS NULL OR estimated_effort IN ('S','M','L','XL')),
    estimated_cost_tier TEXT CHECK(estimated_cost_tier IS NULL OR estimated_cost_tier IN ('low','medium','high','very_high')),
    risk_level TEXT DEFAULT 'medium' CHECK(risk_level IN ('low','medium','high','critical')),
    score_breakdown TEXT DEFAULT '{}',
    metadata TEXT DEFAULT '{}',
    analyzed_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rbb_session ON research_build_buy(session_id);
CREATE INDEX IF NOT EXISTS idx_rbb_challenge ON research_build_buy(challenge_id);
CREATE INDEX IF NOT EXISTS idx_rbb_recommendation ON research_build_buy(recommendation);

-- Research dossiers — generated research dossiers (append-only, D6/D-RES-5)
CREATE TABLE IF NOT EXISTS research_dossiers (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    vertical_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    executive_summary TEXT,
    signal_count INTEGER DEFAULT 0,
    challenge_count INTEGER DEFAULT 0,
    critical_challenges INTEGER DEFAULT 0,
    notable_challenges INTEGER DEFAULT 0,
    regulatory_mappings INTEGER DEFAULT 0,
    build_buy_analyses INTEGER DEFAULT 0,
    capability_coverage REAL DEFAULT 0.0,
    overall_opportunity_score REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'generated'
        CHECK(status IN ('generated','reviewed','approved','rejected','child_app_triggered')),
    reviewer TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    fitness_assessment_id TEXT,
    metadata TEXT DEFAULT '{}',
    generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rdoss_session ON research_dossiers(session_id);
CREATE INDEX IF NOT EXISTS idx_rdoss_status ON research_dossiers(status);
CREATE INDEX IF NOT EXISTS idx_rdoss_score ON research_dossiers(overall_opportunity_score);

-- Research trends — cross-session trend clusters (append-only, D6/D-RES-5)
CREATE TABLE IF NOT EXISTS research_trends (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    vertical_ids TEXT NOT NULL DEFAULT '[]',
    session_ids TEXT NOT NULL DEFAULT '[]',
    challenge_ids TEXT NOT NULL DEFAULT '[]',
    keyword_fingerprint TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    signal_count INTEGER NOT NULL DEFAULT 0,
    velocity REAL DEFAULT 0.0,
    acceleration REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'emerging'
        CHECK(status IN ('emerging','active','declining','stale')),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rtrend_status ON research_trends(status);
CREATE INDEX IF NOT EXISTS idx_rtrend_velocity ON research_trends(velocity);
CREATE INDEX IF NOT EXISTS idx_rtrend_fingerprint ON research_trends(keyword_fingerprint);

-- Research capability map — challenge-to-ICDEV™ capability mappings (append-only, D6/D-RES-5)
CREATE TABLE IF NOT EXISTS research_capability_map (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    challenge_id TEXT NOT NULL REFERENCES research_challenges(id),
    capability_id TEXT NOT NULL,
    capability_name TEXT NOT NULL,
    coverage_score REAL DEFAULT 0.0,
    keyword_overlap TEXT DEFAULT '[]',
    gap_description TEXT,
    enhancement_needed INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    mapped_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rcapmap_session ON research_capability_map(session_id);
CREATE INDEX IF NOT EXISTS idx_rcapmap_challenge ON research_capability_map(challenge_id);
CREATE INDEX IF NOT EXISTS idx_rcapmap_capability ON research_capability_map(capability_id);

-- Research forecasts — AI-generated predictions with surprise scoring (append-only, D6/D-RES-20)
CREATE TABLE IF NOT EXISTS research_forecasts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    trend_id TEXT REFERENCES research_trends(id),
    title TEXT NOT NULL,
    description TEXT,
    prediction_type TEXT NOT NULL CHECK(prediction_type IN (
        'trend_trajectory','greenfield','convergence','disruption','regulatory_shift')),
    confidence REAL NOT NULL DEFAULT 0.5,
    surprise_score REAL NOT NULL DEFAULT 0.5,
    composite_rank REAL NOT NULL DEFAULT 0.25,
    time_horizon TEXT NOT NULL DEFAULT '6mo' CHECK(time_horizon IN ('3mo','6mo','1yr','3yr')),
    supporting_evidence TEXT DEFAULT '[]',
    cross_engine_sources TEXT DEFAULT '[]',
    llm_model TEXT,
    llm_raw_response TEXT,
    outcome TEXT CHECK(outcome IS NULL OR outcome IN (
        'confirmed','partially_confirmed','not_confirmed','too_early')),
    outcome_date TEXT,
    outcome_notes TEXT,
    metadata TEXT DEFAULT '{}',
    generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rfor_session ON research_forecasts(session_id);
CREATE INDEX IF NOT EXISTS idx_rfor_trend ON research_forecasts(trend_id);
CREATE INDEX IF NOT EXISTS idx_rfor_type ON research_forecasts(prediction_type);
CREATE INDEX IF NOT EXISTS idx_rfor_composite ON research_forecasts(composite_rank);
CREATE INDEX IF NOT EXISTS idx_rfor_generated ON research_forecasts(generated_at);

-- ============================================================
-- PROPOSAL LIFECYCLE — GovCon Proposal Writing Tracker
-- ============================================================

-- Root entity: one per RFP/RFI opportunity
CREATE TABLE IF NOT EXISTS proposal_opportunities (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    solicitation_number TEXT NOT NULL,
    title TEXT NOT NULL,
    agency TEXT NOT NULL,
    sub_agency TEXT,
    due_date TEXT NOT NULL,
    due_time TEXT DEFAULT '17:00',
    set_aside_type TEXT CHECK(set_aside_type IN (
        'full_open', 'small_business', '8a', 'hubzone', 'sdvosb',
        'wosb', 'edwosb', 'sole_source', 'other')),
    naics_code TEXT,
    estimated_value_low REAL,
    estimated_value_high REAL,
    proposal_type TEXT NOT NULL CHECK(proposal_type IN (
        'FFP', 'T_AND_M', 'CPFF', 'CPIF', 'IDIQ_TO', 'BPA_CALL', 'other')),
    status TEXT NOT NULL DEFAULT 'intake' CHECK(status IN (
        'intake', 'bid_no_bid', 'go', 'writing', 'review',
        'final', 'submitted', 'won', 'lost', 'no_bid', 'cancelled')),
    bid_decision TEXT CHECK(bid_decision IN ('go', 'no_go', 'pending')),
    bid_decision_date TEXT,
    bid_decision_rationale TEXT,
    rfp_document_path TEXT,
    rfp_url TEXT,
    capture_manager TEXT,
    proposal_manager TEXT,
    domain TEXT DEFAULT 'general' CHECK(domain IN (
        'devsecops', 'ai_ml', 'ato_rmf', 'cloud', 'security',
        'compliance', 'agile', 'data', 'management', 'general')),
    classification TEXT DEFAULT 'CUI',
    compartments TEXT NOT NULL DEFAULT '[]',
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    licensing_model TEXT,
    sam_gov_opportunity_id TEXT,
    questions_due_date TEXT,
    amendment_count INTEGER DEFAULT 0,
    question_count INTEGER DEFAULT 0,
    contract_id TEXT,
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_opp_status ON proposal_opportunities(status);
CREATE INDEX IF NOT EXISTS idx_prop_opp_due ON proposal_opportunities(due_date);
CREATE INDEX IF NOT EXISTS idx_prop_opp_project ON proposal_opportunities(project_id);
CREATE INDEX IF NOT EXISTS idx_prop_opp_solicitation ON proposal_opportunities(solicitation_number);

-- Proposal structure: volumes (Technical, Management, Past Performance, Cost)
CREATE TABLE IF NOT EXISTS proposal_volumes (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES proposal_opportunities(id),
    volume_number INTEGER NOT NULL,
    volume_type TEXT CHECK(volume_type IS NULL OR volume_type IN (
        'technical', 'management', 'past_performance', 'cost', 'staffing')),
    title TEXT NOT NULL,
    description TEXT,
    page_limit INTEGER,
    word_limit INTEGER,
    sort_order INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'not_started' CHECK(status IN (
        'not_started', 'in_progress', 'review', 'final')),
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_vol_opp ON proposal_volumes(opportunity_id);

-- Work units: sections assigned to writers with 14-step status workflow
CREATE TABLE IF NOT EXISTS proposal_sections (
    id TEXT PRIMARY KEY,
    volume_id TEXT NOT NULL REFERENCES proposal_volumes(id),
    opportunity_id TEXT NOT NULL REFERENCES proposal_opportunities(id),
    parent_section_id TEXT REFERENCES proposal_sections(id),
    section_number TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    writer TEXT,
    writer_email TEXT,
    reviewer TEXT,
    page_limit INTEGER,
    word_limit INTEGER,
    current_word_count INTEGER DEFAULT 0,
    current_page_count INTEGER DEFAULT 0,
    priority TEXT DEFAULT 'standard' CHECK(priority IN (
        'critical_path', 'high', 'standard', 'supporting')),
    status TEXT NOT NULL DEFAULT 'not_started' CHECK(status IN (
        'not_started', 'outlining', 'drafting',
        'internal_review', 'pink_team_ready', 'pink_team_review',
        'rework_pink', 'red_team_ready', 'red_team_review',
        'rework_red', 'gold_team_ready', 'gold_team_review',
        'white_glove', 'final', 'submitted')),
    due_date TEXT,
    content_path TEXT,
    notes TEXT,
    sort_order INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_sec_vol ON proposal_sections(volume_id);
CREATE INDEX IF NOT EXISTS idx_prop_sec_opp ON proposal_sections(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_prop_sec_writer ON proposal_sections(writer);
CREATE INDEX IF NOT EXISTS idx_prop_sec_status ON proposal_sections(status);
CREATE INDEX IF NOT EXISTS idx_prop_sec_parent ON proposal_sections(parent_section_id);

-- Section dependency graph (adjacency list, D27 pattern)
CREATE TABLE IF NOT EXISTS proposal_section_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id TEXT NOT NULL REFERENCES proposal_sections(id),
    depends_on_section_id TEXT NOT NULL REFERENCES proposal_sections(id),
    dependency_type TEXT DEFAULT 'content' CHECK(dependency_type IN (
        'content', 'data', 'approval', 'pricing')),
    required_status TEXT DEFAULT 'drafting',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_dep_section ON proposal_section_dependencies(section_id);
CREATE INDEX IF NOT EXISTS idx_prop_dep_depends ON proposal_section_dependencies(depends_on_section_id);

-- L/M/N compliance matrix: links RFP requirements to proposal sections
CREATE TABLE IF NOT EXISTS proposal_compliance_matrix (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES proposal_opportunities(id),
    section_ref TEXT NOT NULL,
    volume_ref TEXT,
    requirement_text TEXT NOT NULL,
    requirement_type TEXT DEFAULT 'L' CHECK(requirement_type IN ('L', 'M', 'N', 'other')),
    compliance_status TEXT DEFAULT 'not_addressed' CHECK(compliance_status IN (
        'compliant', 'partial', 'non_compliant', 'not_applicable', 'not_addressed')),
    proposal_section_id TEXT REFERENCES proposal_sections(id),
    response_summary TEXT,
    notes TEXT,
    sort_order INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_cm_opp ON proposal_compliance_matrix(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_prop_cm_status ON proposal_compliance_matrix(compliance_status);
CREATE INDEX IF NOT EXISTS idx_prop_cm_section ON proposal_compliance_matrix(proposal_section_id);
CREATE INDEX IF NOT EXISTS idx_prop_cm_type ON proposal_compliance_matrix(requirement_type);

-- Color team review events (append-only — NIST AU)
CREATE TABLE IF NOT EXISTS proposal_reviews (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES proposal_opportunities(id),
    review_type TEXT NOT NULL CHECK(review_type IN (
        'pink_team', 'red_team', 'gold_team', 'white_team', 'white_glove', 'internal')),
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN (
        'scheduled', 'in_progress', 'completed', 'cancelled')),
    scheduled_date TEXT,
    started_at TEXT,
    completed_at TEXT,
    lead_reviewer TEXT,
    participants TEXT,
    summary TEXT,
    overall_rating TEXT CHECK(overall_rating IN (
        'pass', 'pass_with_findings', 'major_rework', 'fail')),
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_rev_opp ON proposal_reviews(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_prop_rev_type ON proposal_reviews(review_type);
CREATE INDEX IF NOT EXISTS idx_prop_rev_status ON proposal_reviews(status);

-- Review findings per color team (append-only — NIST AU)
CREATE TABLE IF NOT EXISTS proposal_review_findings (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES proposal_reviews(id),
    section_id TEXT REFERENCES proposal_sections(id),
    finding_type TEXT NOT NULL CHECK(finding_type IN (
        'compliance_gap', 'content_weakness', 'competitive_risk',
        'formatting', 'pricing_concern', 'technical_error',
        'missing_content', 'invalid_citation', 'other')),
    severity TEXT NOT NULL DEFAULT 'medium' CHECK(severity IN (
        'critical', 'major', 'minor', 'observation')),
    description TEXT NOT NULL,
    recommendation TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN (
        'open', 'in_progress', 'resolved', 'deferred', 'wont_fix')),
    assigned_to TEXT,
    resolved_at TEXT,
    resolution_notes TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_find_review ON proposal_review_findings(review_id);
CREATE INDEX IF NOT EXISTS idx_prop_find_section ON proposal_review_findings(section_id);
CREATE INDEX IF NOT EXISTS idx_prop_find_status ON proposal_review_findings(status);
CREATE INDEX IF NOT EXISTS idx_prop_find_severity ON proposal_review_findings(severity);

-- Status change audit trail (append-only — NIST AU)
CREATE TABLE IF NOT EXISTS proposal_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK(entity_type IN (
        'opportunity', 'volume', 'section', 'review', 'finding', 'compliance_item', 'question', 'amendment')),
    entity_id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_by TEXT,
    reason TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_hist_entity ON proposal_status_history(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_prop_hist_created ON proposal_status_history(created_at);

-- HITL reviewer assignment + hand-off table (prop-rev-09)
-- Tracks assignment lifecycle: assign → accept/reject → in_progress → complete/reassign
CREATE TABLE IF NOT EXISTS proposal_reviewer_assignments (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES proposal_reviews(id),
    reviewer TEXT NOT NULL,
    assigned_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'accepted', 'rejected', 'in_progress', 'completed', 'reassigned')),
    notes TEXT,
    assigned_at TEXT DEFAULT (datetime('now')),
    accepted_at TEXT,
    rejected_at TEXT,
    rejection_reason TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_asgn_review ON proposal_reviewer_assignments(review_id);
CREATE INDEX IF NOT EXISTS idx_prop_asgn_reviewer ON proposal_reviewer_assignments(reviewer);
CREATE INDEX IF NOT EXISTS idx_prop_asgn_status ON proposal_reviewer_assignments(status);

-- =========================================================================
-- GovCon Intelligence (Phase 59, D361-D373)
-- =========================================================================

-- SAM.gov opportunity cache (allows UPDATE for status sync)
CREATE TABLE IF NOT EXISTS sam_gov_opportunities (
    id TEXT PRIMARY KEY,
    solicitation_number TEXT,
    title TEXT NOT NULL,
    agency TEXT NOT NULL,
    agency_hierarchy TEXT,
    naics_code TEXT,
    classification_code TEXT,
    notice_type TEXT NOT NULL,
    posted_date TEXT,
    response_deadline TEXT,
    description TEXT,
    point_of_contact TEXT,
    set_aside_type TEXT,
    place_of_performance TEXT,
    attachment_urls TEXT DEFAULT '[]',
    active TEXT DEFAULT 'true',
    proposal_opportunity_id TEXT REFERENCES proposal_opportunities(id),
    content_hash TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    first_seen TEXT NOT NULL,
    last_synced TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_sam_naics ON sam_gov_opportunities(naics_code);
CREATE INDEX IF NOT EXISTS idx_sam_type ON sam_gov_opportunities(notice_type);
CREATE INDEX IF NOT EXISTS idx_sam_deadline ON sam_gov_opportunities(response_deadline);
CREATE INDEX IF NOT EXISTS idx_sam_hash ON sam_gov_opportunities(content_hash);
CREATE INDEX IF NOT EXISTS idx_sam_agency ON sam_gov_opportunities(agency);

-- SAM.gov API quota tracking (D370 — daily call counter)
CREATE TABLE IF NOT EXISTS sam_gov_api_quota (
    date TEXT PRIMARY KEY,
    requests_made INTEGER NOT NULL DEFAULT 0,
    daily_limit INTEGER NOT NULL DEFAULT 10000,
    buffer_remaining INTEGER NOT NULL DEFAULT 50,
    last_429_at TEXT,
    last_429_reset TEXT,
    last_429_body TEXT,
    updated_at TEXT NOT NULL
);

-- SAM.gov quota events audit trail (append-only, NIST AU)
CREATE TABLE IF NOT EXISTS sam_gov_quota_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    date TEXT NOT NULL,
    requests_made INTEGER,
    daily_limit INTEGER,
    details TEXT,
    created_at TEXT NOT NULL
);

-- Extracted "shall" statements from RFPs (append-only, D6/D362)
CREATE TABLE IF NOT EXISTS rfp_shall_statements (
    id TEXT PRIMARY KEY,
    sam_opportunity_id TEXT REFERENCES sam_gov_opportunities(id),
    proposal_opportunity_id TEXT REFERENCES proposal_opportunities(id),
    statement_text TEXT NOT NULL,
    statement_type TEXT NOT NULL DEFAULT 'shall'
        CHECK(statement_type IN ('shall', 'must', 'will', 'required', 'other')),
    domain_category TEXT
        CHECK(domain_category IS NULL OR domain_category IN
            ('devsecops', 'ai_ml', 'ato_rmf', 'cloud', 'security',
             'compliance', 'agile', 'data', 'management', 'other')),
    keywords TEXT DEFAULT '[]',
    keyword_fingerprint TEXT,
    source_section TEXT,
    content_hash TEXT NOT NULL,
    extracted_at TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_shall_sam ON rfp_shall_statements(sam_opportunity_id);
CREATE INDEX IF NOT EXISTS idx_shall_prop ON rfp_shall_statements(proposal_opportunity_id);
CREATE INDEX IF NOT EXISTS idx_shall_domain ON rfp_shall_statements(domain_category);
CREATE INDEX IF NOT EXISTS idx_shall_hash ON rfp_shall_statements(content_hash);

-- Clustered requirement patterns across RFPs (append-only trends, D6/D364/D371)
CREATE TABLE IF NOT EXISTS rfp_requirement_patterns (
    id TEXT PRIMARY KEY,
    pattern_name TEXT NOT NULL,
    description TEXT NOT NULL,
    domain_category TEXT NOT NULL
        CHECK(domain_category IN
            ('devsecops', 'ai_ml', 'ato_rmf', 'cloud', 'security',
             'compliance', 'agile', 'data', 'management', 'other')),
    frequency INTEGER NOT NULL DEFAULT 1,
    shall_statement_ids TEXT NOT NULL DEFAULT '[]',
    sam_opportunity_ids TEXT NOT NULL DEFAULT '[]',
    keyword_fingerprint TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    representative_text TEXT NOT NULL,
    capability_coverage REAL DEFAULT 0.0,
    icdev_capability_ids TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'new'
        CHECK(status IN ('new', 'mapped', 'gap_identified', 'addressed')),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_rfp_pattern_domain ON rfp_requirement_patterns(domain_category);
CREATE INDEX IF NOT EXISTS idx_rfp_pattern_freq ON rfp_requirement_patterns(frequency);
CREATE INDEX IF NOT EXISTS idx_rfp_pattern_coverage ON rfp_requirement_patterns(capability_coverage);
CREATE INDEX IF NOT EXISTS idx_rfp_pattern_fingerprint ON rfp_requirement_patterns(keyword_fingerprint);
CREATE INDEX IF NOT EXISTS idx_rfp_pattern_status ON rfp_requirement_patterns(status);

-- ICDEV™ capability-to-requirement bridge (append-only, D6/D363)
CREATE TABLE IF NOT EXISTS icdev_capability_map (
    id TEXT PRIMARY KEY,
    pattern_id TEXT NOT NULL REFERENCES rfp_requirement_patterns(id),
    capability_id TEXT NOT NULL,
    capability_name TEXT NOT NULL,
    coverage_score REAL NOT NULL DEFAULT 0.0,
    matched_keywords TEXT DEFAULT '[]',
    mapped_at TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_capmap_pattern ON icdev_capability_map(pattern_id);
CREATE INDEX IF NOT EXISTS idx_capmap_capability ON icdev_capability_map(capability_id);

-- AI-generated proposal section drafts (append-only, D6/D373)
CREATE TABLE IF NOT EXISTS proposal_section_drafts (
    id TEXT PRIMARY KEY,
    section_id TEXT REFERENCES proposal_sections(id),
    opportunity_id TEXT NOT NULL REFERENCES proposal_opportunities(id),
    shall_statement_id TEXT REFERENCES rfp_shall_statements(id),
    capability_ids TEXT DEFAULT '[]',
    knowledge_block_ids TEXT DEFAULT '[]',
    draft_content TEXT NOT NULL,
    draft_method TEXT,
    confidence_score REAL DEFAULT 0.0,
    confidence REAL DEFAULT 0.0,
    domain_category TEXT,
    generation_model TEXT,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft', 'reviewed', 'approved', 'rejected')),
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    reviewer_notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_draft_section ON proposal_section_drafts(section_id);
CREATE INDEX IF NOT EXISTS idx_draft_opp ON proposal_section_drafts(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_draft_status ON proposal_section_drafts(status);

-- Reusable proposal content blocks (allows UPDATE for refinement, D368)
CREATE TABLE IF NOT EXISTS proposal_knowledge_base (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT NOT NULL
        CHECK(category IN ('capability_description', 'approach', 'staffing',
                           'tools_used', 'past_performance', 'risk_mitigation',
                           'transition_plan', 'quality_assurance',
                           'management_approach', 'product_overview',
                           'integrated_solution', 'customer_value',
                           'differentiator', 'other')),
    domain TEXT NOT NULL
        CHECK(domain IN ('devsecops', 'ai_ml', 'ato_rmf', 'cloud', 'security',
                         'compliance', 'agile', 'data', 'management', 'general')),
    naics_codes TEXT DEFAULT '[]',
    volume_type TEXT
        CHECK(volume_type IS NULL OR volume_type IN
            ('technical', 'management', 'past_performance', 'cost', 'staffing')),
    keywords TEXT NOT NULL DEFAULT '[]',
    usage_count INTEGER DEFAULT 0,
    win_rate REAL,
    last_used_at TEXT,
    created_by TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'archived', 'draft')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_kb_category ON proposal_knowledge_base(category);
CREATE INDEX IF NOT EXISTS idx_kb_domain ON proposal_knowledge_base(domain);
CREATE INDEX IF NOT EXISTS idx_kb_status ON proposal_knowledge_base(status);

-- GovCon award tracking from SAM.gov (append-only, D6/D367)
CREATE TABLE IF NOT EXISTS govcon_awards (
    id TEXT PRIMARY KEY,
    sam_opportunity_id TEXT REFERENCES sam_gov_opportunities(id),
    solicitation_number TEXT,
    title TEXT NOT NULL,
    agency TEXT NOT NULL,
    naics_code TEXT,
    awardee_name TEXT NOT NULL,
    awardee_duns TEXT,
    awardee_uei TEXT,
    contract_number TEXT,
    award_amount REAL,
    award_date TEXT,
    period_of_performance_start TEXT,
    period_of_performance_end TEXT,
    set_aside_type TEXT,
    competitor_id TEXT REFERENCES creative_competitors(id),
    content_hash TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    discovered_at TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_award_awardee ON govcon_awards(awardee_name);
CREATE INDEX IF NOT EXISTS idx_award_naics ON govcon_awards(naics_code);
CREATE INDEX IF NOT EXISTS idx_award_date ON govcon_awards(award_date);
CREATE INDEX IF NOT EXISTS idx_award_hash ON govcon_awards(content_hash);
CREATE INDEX IF NOT EXISTS idx_award_sam ON govcon_awards(sam_opportunity_id);

-- ── Customer Delivery Tracking (D374) ────────────────────────────────
-- Tracks which ICDEV™ components a winning customer receives on-prem.
-- Append-only: once a delivery is created, it cannot be modified.
-- delivery_tier maps to deployment_profiles.yaml customer_* profiles.

CREATE TABLE IF NOT EXISTS customer_deliveries (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    customer_agency TEXT,
    contract_number TEXT,
    delivery_tier TEXT NOT NULL CHECK(delivery_tier IN ('core', 'standard', 'enterprise', 'custom')),
    deployment_profile TEXT NOT NULL,
    modules_json TEXT NOT NULL,
    compliance_frameworks_json TEXT DEFAULT '[]',
    platform TEXT DEFAULT 'k8s',
    impact_level TEXT DEFAULT 'IL4',
    cui_enabled INTEGER DEFAULT 1,
    license_key_hash TEXT,
    effective_date TEXT NOT NULL,
    expires_at TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'provisioning', 'delivered', 'active', 'expired', 'revoked')),
    delivered_by TEXT,
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cust_delivery_opp ON customer_deliveries(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_cust_delivery_customer ON customer_deliveries(customer_name);
CREATE INDEX IF NOT EXISTS idx_cust_delivery_status ON customer_deliveries(status);
CREATE INDEX IF NOT EXISTS idx_cust_delivery_tier ON customer_deliveries(delivery_tier);

-- =========================================================================
-- Contract Performance Management Portal — Phase 60 (D-CPMP-1 through D-CPMP-10)
-- Post-award contract lifecycle: EVM, CPARS, CDRL, subcontractors, COR portal
-- =========================================================================

-- ── Phase A: Foundation ─────────────────────────────────────────────

-- Core contract entity, linked from proposal_opportunities when won
-- Allows UPDATE for status, health, and value changes
CREATE TABLE IF NOT EXISTS cpmp_contracts (
    id TEXT PRIMARY KEY,
    contract_number TEXT NOT NULL,
    title TEXT NOT NULL,
    agency TEXT NOT NULL,
    agency_hierarchy TEXT,
    contracting_officer TEXT,
    co_email TEXT,
    cor_name TEXT,
    cor_email TEXT,
    cor_phone TEXT,
    contract_type TEXT NOT NULL CHECK(contract_type IN (
        'FFP', 'T&M', 'CPFF', 'CPIF', 'IDIQ', 'BPA', 'BOA')),
    idiq_contract_id TEXT REFERENCES cpmp_contracts(id),
    task_order_number TEXT,
    naics_code TEXT,
    total_value REAL DEFAULT 0.0,
    funded_value REAL DEFAULT 0.0,
    ceiling_value REAL,
    billed_value REAL DEFAULT 0.0,
    pop_start TEXT,
    pop_end TEXT,
    pop_base_end TEXT,
    option_years INTEGER DEFAULT 0,
    status TEXT DEFAULT 'draft' CHECK(status IN (
        'draft', 'active', 'option_pending', 'complete', 'closed', 'terminated')),
    health TEXT DEFAULT 'green' CHECK(health IN ('green', 'yellow', 'red')),
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
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_cpmp_contract_number ON cpmp_contracts(contract_number);
CREATE INDEX IF NOT EXISTS idx_cpmp_contract_agency ON cpmp_contracts(agency);
CREATE INDEX IF NOT EXISTS idx_cpmp_contract_status ON cpmp_contracts(status);
CREATE INDEX IF NOT EXISTS idx_cpmp_contract_health ON cpmp_contracts(health);
CREATE INDEX IF NOT EXISTS idx_cpmp_contract_opp ON cpmp_contracts(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_contract_idiq ON cpmp_contracts(idiq_contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_contract_cor ON cpmp_contracts(cor_email);

-- Contract Line Items with funding tracking
CREATE TABLE IF NOT EXISTS cpmp_clins (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    clin_number TEXT NOT NULL,
    description TEXT,
    clin_type TEXT NOT NULL CHECK(clin_type IN (
        'labor', 'materials', 'travel', 'odc', 'subcontract', 'fixed_price')),
    total_value REAL DEFAULT 0.0,
    funded_value REAL DEFAULT 0.0,
    billed_value REAL DEFAULT 0.0,
    pop_start TEXT,
    pop_end TEXT,
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'fully_funded', 'expended', 'deobligated')),
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_cpmp_clin_contract ON cpmp_clins(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_clin_number ON cpmp_clins(clin_number);

-- Work Breakdown Structure (hierarchical, for EVM)
CREATE TABLE IF NOT EXISTS cpmp_wbs (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    parent_id TEXT REFERENCES cpmp_wbs(id),
    wbs_number TEXT NOT NULL,
    title TEXT NOT NULL,
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
    status TEXT DEFAULT 'not_started' CHECK(status IN (
        'not_started', 'in_progress', 'complete', 'on_hold', 'cancelled')),
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_cpmp_wbs_contract ON cpmp_wbs(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_wbs_parent ON cpmp_wbs(parent_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_wbs_number ON cpmp_wbs(wbs_number);

-- CDRLs and deliverables with status pipeline
CREATE TABLE IF NOT EXISTS cpmp_deliverables (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    clin_id TEXT REFERENCES cpmp_clins(id),
    wbs_id TEXT REFERENCES cpmp_wbs(id),
    cdrl_number TEXT,
    did_number TEXT,
    title TEXT NOT NULL,
    description TEXT,
    deliverable_type TEXT NOT NULL CHECK(deliverable_type IN (
        'cdrl', 'report', 'software', 'documentation', 'test_result', 'plan', 'data', 'other')),
    frequency TEXT CHECK(frequency IN (
        'one_time', 'weekly', 'biweekly', 'monthly', 'quarterly', 'semi_annual', 'annual', 'as_needed', 'event_driven')),
    due_date TEXT,
    submitted_date TEXT,
    accepted_date TEXT,
    rejected_date TEXT,
    rejection_reason TEXT,
    status TEXT DEFAULT 'not_started' CHECK(status IN (
        'not_started', 'in_progress', 'draft_complete', 'internal_review',
        'submitted', 'government_review', 'accepted', 'rejected', 'resubmitted', 'overdue')),
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
CREATE INDEX IF NOT EXISTS idx_cpmp_deliv_contract ON cpmp_deliverables(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_deliv_status ON cpmp_deliverables(status);
CREATE INDEX IF NOT EXISTS idx_cpmp_deliv_due ON cpmp_deliverables(due_date);
CREATE INDEX IF NOT EXISTS idx_cpmp_deliv_type ON cpmp_deliverables(deliverable_type);
CREATE INDEX IF NOT EXISTS idx_cpmp_deliv_cdrl ON cpmp_deliverables(cdrl_number);

-- Append-only status change log (NIST AU-2, D6)
CREATE TABLE IF NOT EXISTS cpmp_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK(entity_type IN (
        'contract', 'clin', 'wbs', 'deliverable', 'subcontractor',
        'evm_baseline', 'cpars_assessment', 'negative_event', 'contract_mod')),
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
CREATE INDEX IF NOT EXISTS idx_cpmp_hist_entity ON cpmp_status_history(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_hist_created ON cpmp_status_history(created_at);

-- ── Phase B: Intelligence ───────────────────────────────────────────

-- Monthly EVM snapshots per WBS element (append-only time-series, D6)
CREATE TABLE IF NOT EXISTS cpmp_evm_periods (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    wbs_id TEXT REFERENCES cpmp_wbs(id),
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
    source TEXT DEFAULT 'manual' CHECK(source IN ('manual', 'calculated', 'imported')),
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_cpmp_evm_contract ON cpmp_evm_periods(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_evm_wbs ON cpmp_evm_periods(wbs_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_evm_period ON cpmp_evm_periods(period_date);

-- Subcontractor tracking with FAR 52.219-9 compliance
CREATE TABLE IF NOT EXISTS cpmp_subcontractors (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    company_name TEXT NOT NULL,
    cage_code TEXT,
    uei TEXT,
    business_size TEXT CHECK(business_size IN (
        'large', 'small', 'sdb', 'wosb', 'hubzone', 'sdvosb', '8a')),
    business_type TEXT,
    subcontract_type TEXT CHECK(subcontract_type IN ('labor', 'materials', 'services', 'other')),
    subcontract_value REAL DEFAULT 0.0,
    billed_value REAL DEFAULT 0.0,
    performance_rating TEXT CHECK(performance_rating IN (
        'exceptional', 'very_good', 'satisfactory', 'marginal', 'unsatisfactory')),
    flow_down_complete INTEGER DEFAULT 0,
    flowdown_verified INTEGER DEFAULT 0,
    cybersecurity_compliant INTEGER DEFAULT 0,
    cmmc_level INTEGER,
    isr_ssr_current INTEGER DEFAULT 0,
    contact_name TEXT,
    contact_email TEXT,
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'terminated', 'pending')),
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_cpmp_sub_contract ON cpmp_subcontractors(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_sub_name ON cpmp_subcontractors(company_name);
CREATE INDEX IF NOT EXISTS idx_cpmp_sub_size ON cpmp_subcontractors(business_size);

-- CPARS assessment per evaluation period
CREATE TABLE IF NOT EXISTS cpmp_cpars_assessments (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    quality_rating REAL,
    schedule_rating REAL,
    cost_rating REAL,
    management_rating REAL,
    small_business_rating REAL,
    overall_rating TEXT CHECK(overall_rating IN (
        'exceptional', 'very_good', 'satisfactory', 'marginal', 'unsatisfactory')),
    overall_score REAL,
    predicted_overall TEXT,
    predicted_score REAL,
    narrative TEXT,
    government_narrative TEXT,
    negative_event_count INTEGER DEFAULT 0,
    corrective_actions_completed INTEGER DEFAULT 0,
    status TEXT DEFAULT 'draft' CHECK(status IN (
        'draft', 'submitted', 'government_review', 'contested', 'final')),
    submitted_date TEXT,
    finalized_date TEXT,
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_cpmp_cpars_contract ON cpmp_cpars_assessments(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_cpars_status ON cpmp_cpars_assessments(status);
CREATE INDEX IF NOT EXISTS idx_cpmp_cpars_period ON cpmp_cpars_assessments(period_end);

-- NDAA negative-event tracking (append-only, D6, D-CPMP-7)
CREATE TABLE IF NOT EXISTS cpmp_negative_events (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    event_type TEXT NOT NULL CHECK(event_type IN (
        'delinquent_delivery', 'cost_overrun', 'quality_rejection',
        'cybersecurity_breach', 'flowdown_failure', 'safety_violation',
        'compliance_violation', 'cure_notice', 'show_cause',
        'stop_work', 'termination_default', 'fraud_waste_abuse')),
    severity TEXT NOT NULL CHECK(severity IN ('low', 'medium', 'high', 'critical')),
    description TEXT NOT NULL,
    evidence TEXT,
    deliverable_id TEXT REFERENCES cpmp_deliverables(id),
    subcontractor_id TEXT REFERENCES cpmp_subcontractors(id),
    corrective_action TEXT,
    corrective_action_status TEXT DEFAULT 'open' CHECK(corrective_action_status IN (
        'open', 'in_progress', 'completed', 'verified')),
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
CREATE INDEX IF NOT EXISTS idx_cpmp_neg_contract ON cpmp_negative_events(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_neg_type ON cpmp_negative_events(event_type);
CREATE INDEX IF NOT EXISTS idx_cpmp_neg_severity ON cpmp_negative_events(severity);
CREATE INDEX IF NOT EXISTS idx_cpmp_neg_status ON cpmp_negative_events(corrective_action_status);

-- FAR 52.219-9 Small Business Subcontracting Plan (ISR/SSR)
CREATE TABLE IF NOT EXISTS cpmp_small_business_plan (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    reporting_period TEXT NOT NULL,
    report_type TEXT NOT NULL CHECK(report_type IN ('isr', 'ssr')),
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
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft', 'submitted', 'accepted', 'rejected')),
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_cpmp_sb_contract ON cpmp_small_business_plan(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_sb_period ON cpmp_small_business_plan(reporting_period);
CREATE INDEX IF NOT EXISTS idx_cpmp_sb_type ON cpmp_small_business_plan(report_type);

-- ── Phase C: Automation ─────────────────────────────────────────────

-- CDRL auto-generation audit trail (append-only, D6)
CREATE TABLE IF NOT EXISTS cpmp_cdrl_generations (
    id TEXT PRIMARY KEY,
    deliverable_id TEXT NOT NULL REFERENCES cpmp_deliverables(id),
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    cdrl_type TEXT NOT NULL,
    generation_tool TEXT NOT NULL,
    tool_args TEXT DEFAULT '{}',
    output_path TEXT,
    output_hash TEXT,
    file_size_bytes INTEGER,
    status TEXT DEFAULT 'generated' CHECK(status IN (
        'generated', 'reviewed', 'approved', 'submitted', 'failed')),
    error_message TEXT,
    generated_by TEXT,
    reviewed_by TEXT,
    approved_by TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_cpmp_cdrl_gen_deliv ON cpmp_cdrl_generations(deliverable_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_cdrl_gen_contract ON cpmp_cdrl_generations(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_cdrl_gen_status ON cpmp_cdrl_generations(status);

-- SAM.gov Contract Awards API cache
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
    linked_contract_id TEXT REFERENCES cpmp_contracts(id),
    content_hash TEXT NOT NULL,
    raw_json TEXT,
    metadata TEXT DEFAULT '{}',
    discovered_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_cpmp_sam_award_id ON cpmp_sam_contract_awards(sam_award_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_sam_piid ON cpmp_sam_contract_awards(piid);
CREATE INDEX IF NOT EXISTS idx_cpmp_sam_awardee ON cpmp_sam_contract_awards(awardee_name);
CREATE INDEX IF NOT EXISTS idx_cpmp_sam_linked ON cpmp_sam_contract_awards(linked_contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_sam_hash ON cpmp_sam_contract_awards(content_hash);

-- COR portal access audit trail (append-only, NIST AU-2)
CREATE TABLE IF NOT EXISTS cpmp_cor_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    user_email TEXT,
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    action TEXT NOT NULL CHECK(action IN (
        'view_contract', 'view_deliverables', 'view_evm',
        'view_cpars', 'view_subcontractors', 'export_report')),
    ip_address TEXT,
    user_agent TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_cpmp_cor_user ON cpmp_cor_access_log(user_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_cor_contract ON cpmp_cor_access_log(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_cor_action ON cpmp_cor_access_log(action);
CREATE INDEX IF NOT EXISTS idx_cpmp_cor_created ON cpmp_cor_access_log(created_at);

-- ── Phase D: Integrated Master Schedule (IMS, prop-pm-01) ──────────────

-- Milestones linked to WBS elements and EVM periods for schedule-to-EVM traceability
CREATE TABLE IF NOT EXISTS cpmp_milestones (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    wbs_id TEXT REFERENCES cpmp_wbs(id),
    title TEXT NOT NULL,
    description TEXT,
    baseline_date TEXT,
    forecast_date TEXT,
    actual_date TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN (
        'pending', 'in_progress', 'complete', 'missed', 'on_hold')),
    evm_period_id TEXT REFERENCES cpmp_evm_periods(id),
    responsible_person TEXT,
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_cpmp_ms_contract ON cpmp_milestones(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_ms_wbs ON cpmp_milestones(wbs_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_ms_status ON cpmp_milestones(status);
CREATE INDEX IF NOT EXISTS idx_cpmp_ms_baseline ON cpmp_milestones(baseline_date);

-- Milestone dependency graph (FS/SS/FF/SF) for critical path visualization
CREATE TABLE IF NOT EXISTS cpmp_milestone_deps (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    predecessor_id TEXT NOT NULL REFERENCES cpmp_milestones(id),
    successor_id TEXT NOT NULL REFERENCES cpmp_milestones(id),
    lag_days INTEGER DEFAULT 0,
    dep_type TEXT DEFAULT 'finish_to_start' CHECK(dep_type IN (
        'finish_to_start', 'start_to_start', 'finish_to_finish', 'start_to_finish')),
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT,
    UNIQUE(predecessor_id, successor_id)
);
CREATE INDEX IF NOT EXISTS idx_cpmp_msdep_contract ON cpmp_milestone_deps(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_msdep_pred ON cpmp_milestone_deps(predecessor_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_msdep_succ ON cpmp_milestone_deps(successor_id);

-- Contract modification request/approval workflow (prop-ctr-01)
CREATE TABLE IF NOT EXISTS cpmp_contract_mods (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES cpmp_contracts(id),
    mod_number INTEGER NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('admin','funding','scope','pop')),
    description TEXT NOT NULL DEFAULT '',
    value_delta REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'requested' CHECK(status IN ('requested','in_review','approved','rejected','executed')),
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_cpmp_contract_mods_number ON cpmp_contract_mods(contract_id, mod_number);

-- =========================================================================
-- Questions to Government (Phase 59, D-QTG-1 through D-QTG-5)
-- =========================================================================

-- Questions to Government: auto-generated + manual questions
CREATE TABLE IF NOT EXISTS proposal_questions (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES proposal_opportunities(id),
    question_number INTEGER,
    question_text TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN (
        'scope', 'evaluation_criteria', 'technical_requirements',
        'contract_terms', 'compliance_security', 'small_business')),
    priority TEXT NOT NULL DEFAULT 'medium' CHECK(priority IN ('high', 'medium', 'low')),
    source TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('auto', 'manual')),
    rfp_section_ref TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN (
        'draft', 'approved', 'submitted', 'answered')),
    ambiguity_trigger TEXT,
    content_hash TEXT,
    created_by TEXT,
    approved_by TEXT,
    approved_at TEXT,
    submitted_at TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_q_opp ON proposal_questions(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_prop_q_status ON proposal_questions(status);
CREATE INDEX IF NOT EXISTS idx_prop_q_category ON proposal_questions(category);
CREATE INDEX IF NOT EXISTS idx_prop_q_priority ON proposal_questions(priority);

-- RFP amendments/revisions (allows UPDATE for diff_data, D-QTG-3)
CREATE TABLE IF NOT EXISTS proposal_amendments (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES proposal_opportunities(id),
    version_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    amendment_date TEXT,
    source_type TEXT NOT NULL DEFAULT 'file' CHECK(source_type IN ('file', 'text')),
    file_path TEXT,
    amendment_text TEXT,
    diff_summary TEXT,
    diff_data TEXT DEFAULT '{}',
    changes_detected INTEGER DEFAULT 0,
    uploaded_by TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_amend_opp ON proposal_amendments(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_prop_amend_version ON proposal_amendments(opportunity_id, version_number);

-- Government Q&A responses (append-only, D6/NIST AU-2)
CREATE TABLE IF NOT EXISTS proposal_question_responses (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES proposal_questions(id),
    opportunity_id TEXT NOT NULL REFERENCES proposal_opportunities(id),
    amendment_id TEXT REFERENCES proposal_amendments(id),
    response_text TEXT NOT NULL,
    response_date TEXT,
    impacts_requirements INTEGER DEFAULT 0,
    impact_notes TEXT,
    recorded_by TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_qr_question ON proposal_question_responses(question_id);
CREATE INDEX IF NOT EXISTS idx_prop_qr_opp ON proposal_question_responses(opportunity_id);

-- =========================================================================
-- Proposals Module Enhancement — Capture / Review / Compliance tables
-- prop-cap-01: capture fields on proposal_opportunities (ALTER TABLE)
-- prop-cap-02: proposal_competitors
-- prop-cap-03: proposal_teaming_partners
-- prop-rev-01: review/finding extra fields (ALTER TABLE)
-- prop-rev-02: proposal_versions
-- prop-cmp-01: proposal_shred_items
-- prop-cmp-02: changed_requirement_ids on proposal_amendments (ALTER TABLE)
-- =========================================================================

-- prop-cap-01: capture pipeline fields (idempotent ALTER TABLEs handled in Python init via PROPOSALS_ALTER_SQL)

-- prop-cap-02: competitor intelligence
CREATE TABLE IF NOT EXISTS proposal_competitors (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES proposal_opportunities(id),
    company_name TEXT NOT NULL,
    incumbent INTEGER DEFAULT 0,
    strengths TEXT,
    weaknesses TEXT,
    estimated_price NUMERIC,
    win_probability_pct INTEGER,
    notes TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_comp_opp ON proposal_competitors(opportunity_id);

-- prop-cap-03: teaming partners
CREATE TABLE IF NOT EXISTS proposal_teaming_partners (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES proposal_opportunities(id),
    company_name TEXT NOT NULL,
    role TEXT CHECK(role IN ('prime','sub','key_sub','mentor_protege')),
    naics TEXT,
    cage_code TEXT,
    capabilities TEXT,
    workshare_pct NUMERIC,
    notes TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_team_opp ON proposal_teaming_partners(opportunity_id);

-- prop-rev-01: executive summary + finding closure fields (handled via PROPOSALS_ALTER_SQL)

-- prop-rev-02: version snapshots
CREATE TABLE IF NOT EXISTS proposal_versions (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES proposal_opportunities(id),
    version_number INTEGER NOT NULL,
    label TEXT,
    snapshot_json TEXT,
    created_by TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_ver_opp ON proposal_versions(opportunity_id);

-- prop-cmp-01: shred matrix
CREATE TABLE IF NOT EXISTS proposal_shred_items (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES proposal_opportunities(id),
    statement_text TEXT NOT NULL,
    statement_type TEXT CHECK(statement_type IN ('shall','must','will','should')),
    rfp_section TEXT,
    rfp_page TEXT,
    section_id TEXT REFERENCES proposal_sections(id),
    writer TEXT,
    status TEXT DEFAULT 'unassigned' CHECK(status IN ('unassigned','assigned','drafted','complete')),
    notes TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_shred_opp ON proposal_shred_items(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_prop_shred_status ON proposal_shred_items(status);

-- prop-cmp-02: amendment impact tracking (handled via PROPOSALS_ALTER_SQL)

-- =========================================================================
-- ANVIL Critique Phase (Phase 61 — Feature 3)
-- =========================================================================

-- Critique sessions: one per ANVIL critique invocation (append-only except status updates)
CREATE TABLE IF NOT EXISTS anvil_critique_sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    workflow_id TEXT,
    phase_input_hash TEXT NOT NULL,
    status TEXT DEFAULT 'in_progress' CHECK(status IN (
        'in_progress', 'go', 'nogo', 'conditional', 'revised', 'failed')),
    round_number INTEGER DEFAULT 1,
    max_rounds INTEGER DEFAULT 3,
    consensus TEXT CHECK(consensus IN ('go', 'nogo', 'conditional') OR consensus IS NULL),
    critics_assigned TEXT DEFAULT '[]',
    total_findings INTEGER DEFAULT 0,
    critical_count INTEGER DEFAULT 0,
    high_count INTEGER DEFAULT 0,
    medium_count INTEGER DEFAULT 0,
    low_count INTEGER DEFAULT 0,
    revision_summary TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_critique_session_project ON anvil_critique_sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_critique_session_status ON anvil_critique_sessions(status);

-- Critique findings: individual findings from critic agents (append-only, NIST AU)
CREATE TABLE IF NOT EXISTS anvil_critique_findings (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES anvil_critique_sessions(id),
    critic_agent TEXT NOT NULL,
    round_number INTEGER DEFAULT 1,
    finding_type TEXT NOT NULL CHECK(finding_type IN (
        'security_vulnerability', 'compliance_gap', 'architecture_flaw',
        'performance_risk', 'maintainability_concern', 'testing_gap',
        'deployment_risk', 'data_handling_issue')),
    severity TEXT NOT NULL CHECK(severity IN ('critical', 'high', 'medium', 'low')),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence TEXT,
    suggested_fix TEXT,
    nist_controls TEXT DEFAULT '[]',
    addressed_in_revision INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_critique_finding_session ON anvil_critique_findings(session_id);
CREATE INDEX IF NOT EXISTS idx_critique_finding_severity ON anvil_critique_findings(severity);
CREATE INDEX IF NOT EXISTS idx_critique_finding_type ON anvil_critique_findings(finding_type);

-- =========================================================================
-- PROMPT CHAIN EXECUTIONS (Phase 61 — Feature 2)
-- =========================================================================

-- Declarative prompt chain execution records
CREATE TABLE IF NOT EXISTS prompt_chain_executions (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    chain_name TEXT NOT NULL,
    original_input TEXT NOT NULL,
    original_input_hash TEXT NOT NULL,
    status TEXT DEFAULT 'running'
        CHECK(status IN ('running', 'completed', 'failed', 'cancelled')),
    steps_completed INTEGER DEFAULT 0,
    steps_total INTEGER NOT NULL,
    step_results TEXT DEFAULT '{}',
    final_output TEXT,
    final_output_hash TEXT,
    total_duration_ms INTEGER,
    total_tokens_used INTEGER DEFAULT 0,
    error_message TEXT,
    executed_by TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_chain_exec_project ON prompt_chain_executions(project_id);
CREATE INDEX IF NOT EXISTS idx_chain_exec_chain ON prompt_chain_executions(chain_name);
CREATE INDEX IF NOT EXISTS idx_chain_exec_status ON prompt_chain_executions(status);

-- =========================================================================
-- DISPATCHER MODE OVERRIDES (Phase 61 -- Feature 1, D-DISP-1)
-- =========================================================================

-- Per-project overrides for dispatcher-only orchestrator mode.
-- Allows UPDATE for enable/disable toggles (not append-only).
CREATE TABLE IF NOT EXISTS dispatcher_mode_overrides (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    custom_dispatch_tools TEXT DEFAULT '[]',
    custom_blocked_tools TEXT DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by TEXT NOT NULL DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_dispatcher_mode_project
    ON dispatcher_mode_overrides(project_id);

-- =========================================================================
-- SESSION PURPOSES (Phase 61 -- D-ORCH-5)
-- =========================================================================

-- Session-level intent tracking for NIST AU-3 event detail traceability.
-- Declares purpose before work begins, injected into agent system prompts.
CREATE TABLE IF NOT EXISTS session_purposes (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    purpose TEXT NOT NULL,
    purpose_hash TEXT NOT NULL,
    declared_by TEXT DEFAULT 'user',
    scope TEXT DEFAULT 'session' CHECK(scope IN ('session','workflow','task')),
    status TEXT DEFAULT 'active' CHECK(status IN ('active','completed','abandoned')),
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_session_purposes_project
    ON session_purposes(project_id);
CREATE INDEX IF NOT EXISTS idx_session_purposes_status
    ON session_purposes(status);

-- Phase 64: Universal RAG Subsystem (D-RAG-1 through D-RAG-14)
CREATE TABLE IF NOT EXISTS rag_chunks (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding BLOB,
    embedding_vec BLOB,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    source_table TEXT NOT NULL DEFAULT '',
    chunk_index INTEGER NOT NULL DEFAULT 0,
    total_chunks INTEGER NOT NULL DEFAULT 1,
    metadata TEXT DEFAULT '{}',
    tier TEXT NOT NULL DEFAULT 'hot'
        CHECK(tier IN ('hot', 'warm', 'cold')),
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_content_hash
    ON rag_chunks(content_hash);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_source
    ON rag_chunks(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_tier
    ON rag_chunks(tier);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_tenant
    ON rag_chunks(tenant_id);

-- RAG PDF documents — ingested PDF files (D-RAG-15)
CREATE TABLE IF NOT EXISTS rag_pdf_documents (
    id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    page_count INTEGER DEFAULT 0,
    extracted_text TEXT,
    provider_used TEXT DEFAULT ''
        CHECK(provider_used IN ('', 'anthropic', 'google', 'vision_llava', 'pypdf_text')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    status TEXT DEFAULT 'pending'
        CHECK(status IN ('pending', 'extracting', 'extracted', 'failed', 'ingested')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rag_pdf_hash
    ON rag_pdf_documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_rag_pdf_tenant
    ON rag_pdf_documents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rag_pdf_status
    ON rag_pdf_documents(status);

-- RAG ingestion log — tracks what was ingested when (append-only, D6, D-RAG-18)
CREATE TABLE IF NOT EXISTS rag_ingestion_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    source_table TEXT NOT NULL DEFAULT '',
    chunks_created INTEGER NOT NULL DEFAULT 0,
    chunks_skipped INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT DEFAULT '',
    ingestion_mode TEXT DEFAULT 'batch'
        CHECK(ingestion_mode IN ('realtime', 'batch', 'manual')),
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    agent_id TEXT DEFAULT '',
    correlation_id TEXT DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rag_ingestion_source
    ON rag_ingestion_log(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_rag_ingestion_tenant
    ON rag_ingestion_log(tenant_id);

-- RAG retrieval log — every retrieval logged (append-only, NIST AU-3, D-RAG-8)
CREATE TABLE IF NOT EXISTS rag_retrieval_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_hash TEXT NOT NULL,
    query_text TEXT DEFAULT '',
    results_count INTEGER NOT NULL DEFAULT 0,
    top_score REAL DEFAULT 0.0,
    -- 'reflective_reranked' (step 5b judged the candidates) and
    -- 'reflective_degraded' (step 5b ran and judged nothing, so the incoming
    -- order stands) are widened onto an EXISTING database by migration
    -- 20260815002727 — CREATE TABLE IF NOT EXISTS never alters one. Keep the
    -- two lists in step.
    retrieval_mode TEXT DEFAULT 'hybrid'
        CHECK(retrieval_mode IN ('vector', 'bm25', 'hybrid', 'rrf_hybrid', 'reranked',
                                 'reflective_reranked', 'reflective_degraded')),
    vector_top_k INTEGER DEFAULT 50,
    final_top_k INTEGER DEFAULT 5,
    rerank_used INTEGER DEFAULT 0,
    source_types_queried TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT 0,
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    agent_id TEXT DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rag_retrieval_tenant
    ON rag_retrieval_log(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rag_retrieval_created
    ON rag_retrieval_log(created_at);

-- RAG parent cache — child apps cache parent RAG query results (D-RAG-13)
CREATE TABLE IF NOT EXISTS rag_parent_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_hash TEXT UNIQUE,
    results TEXT DEFAULT '[]',
    retrieved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    source TEXT DEFAULT 'parent'
);

CREATE INDEX IF NOT EXISTS idx_rag_parent_cache_hash
    ON rag_parent_cache(query_hash);
CREATE INDEX IF NOT EXISTS idx_rag_parent_cache_expires
    ON rag_parent_cache(expires_at);

-- RAG provenance ledger — append-only AIA chain-of-custody log (D-AIDP, NIST AU-3)
-- event_type='ingest': chunk bound to source document with hash verification
-- event_type='chain_of_custody': LLM invocation audit block (model, hyperparams, prompt hash, signature)
-- event_type='retrieval': chunk served to a caller as citable evidence (cef-fnd-05)
-- The CHECK list is derived from tools/rag/provenance_ledger.py::PROVENANCE_EVENT_TYPES.
-- Widen BOTH together — a value the CHECK rejects is dropped by the caller's
-- best-effort INSERT and the row silently never appears.
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
        CHECK(event_type IN ('ingest', 'chain_of_custody', 'retrieval')),
    ingest_timestamp TIMESTAMP,
    -- rag_retrieval_log.id of the search that served this chunk (cef-fnd-05).
    -- Nullable: a row is still worth keeping when the retrieval-log INSERT
    -- itself failed, since prompt_sha256 equals that table's query_hash.
    retrieval_log_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rag_prov_chunk
    ON rag_provenance_ledger(chunk_uuid);
CREATE INDEX IF NOT EXISTS idx_rag_prov_parent_doc
    ON rag_provenance_ledger(parent_doc_uuid);
CREATE INDEX IF NOT EXISTS idx_rag_prov_event_type
    ON rag_provenance_ledger(event_type);
CREATE INDEX IF NOT EXISTS idx_rag_prov_retrieval_log
    ON rag_provenance_ledger(retrieval_log_id);

-- rag_queries: tracks RAG knowledge search requests and their lifecycle
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

-- rag_citations: source citations attached to a rag_queries result
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

-- ============================================================
-- FINE-TUNING SUBSYSTEM (Phase 64 Extension, D-FT-1 through D-FT-22)
-- ============================================================

-- 1. Datasets: versioned collections of training examples (D-FT-9)
CREATE TABLE IF NOT EXISTS ft_datasets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    purpose TEXT DEFAULT 'general'
        CHECK(purpose IN ('general','proposal_drafting','compliance_export','code_generation','custom')),
    base_model TEXT DEFAULT 'qwen3:latest',
    version INTEGER DEFAULT 1,
    example_count INTEGER DEFAULT 0,
    content_hash TEXT DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft','labeling','ready','archived')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Training examples — APPEND-ONLY (D6, D-FT-9)
CREATE TABLE IF NOT EXISTS ft_dataset_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL REFERENCES ft_datasets(id),
    system_prompt TEXT DEFAULT '',
    user_input TEXT NOT NULL,
    expected_output TEXT NOT NULL,
    source TEXT DEFAULT 'manual'
        CHECK(source IN ('manual','rag_auto_generated','document_extraction','imported','marketplace')),
    source_chunk_id TEXT DEFAULT '',
    source_document_id TEXT DEFAULT '',
    quality_score REAL DEFAULT 0.0,
    compliance_score REAL DEFAULT 0.0,
    relevance_score REAL DEFAULT 0.0,
    approved INTEGER DEFAULT 0,
    labeled_by TEXT DEFAULT '',
    labeled_at TIMESTAMP,
    content_hash TEXT NOT NULL,
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ft_examples_dataset
    ON ft_dataset_examples(dataset_id);
CREATE INDEX IF NOT EXISTS idx_ft_examples_hash
    ON ft_dataset_examples(content_hash);
CREATE INDEX IF NOT EXISTS idx_ft_examples_approved
    ON ft_dataset_examples(dataset_id, approved);

-- 3. Training jobs (D-FT-3)
CREATE TABLE IF NOT EXISTS ft_training_jobs (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES ft_datasets(id),
    provider TEXT NOT NULL DEFAULT 'unsloth_local'
        CHECK(provider IN ('unsloth_local','openai','bedrock','azure_openai')),
    base_model TEXT NOT NULL DEFAULT 'qwen3:latest',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','preparing','training','exporting','evaluating','completed','failed','canceled')),
    hyperparams TEXT DEFAULT '{}',
    lora_rank INTEGER DEFAULT 16,
    learning_rate REAL DEFAULT 2e-4,
    epochs INTEGER DEFAULT 3,
    batch_size INTEGER DEFAULT 2,
    max_seq_length INTEGER DEFAULT 2048,
    gpu_count INTEGER DEFAULT 1,
    distributed INTEGER DEFAULT 0,
    output_dir TEXT DEFAULT '',
    adapter_path TEXT DEFAULT '',
    gguf_path TEXT DEFAULT '',
    ollama_model_name TEXT DEFAULT '',
    cloud_job_id TEXT DEFAULT '',
    loss_history TEXT DEFAULT '[]',
    training_duration_seconds INTEGER DEFAULT 0,
    total_steps INTEGER DEFAULT 0,
    error_message TEXT DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ft_jobs_dataset
    ON ft_training_jobs(dataset_id);
CREATE INDEX IF NOT EXISTS idx_ft_jobs_status
    ON ft_training_jobs(status);

-- 4. Training job events — APPEND-ONLY (D6, D-FT-3)
CREATE TABLE IF NOT EXISTS ft_training_job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES ft_training_jobs(id),
    event_type TEXT NOT NULL
        CHECK(event_type IN ('created','started','checkpoint','progress','export_started',
                              'export_completed','eval_started','eval_completed',
                              'completed','failed','canceled','promoted','demoted')),
    details TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ft_events_job
    ON ft_training_job_events(job_id);

-- 5. Model versions — all trained adapters (D-FT-7)
CREATE TABLE IF NOT EXISTS ft_model_versions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES ft_training_jobs(id),
    model_name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    base_model TEXT NOT NULL,
    adapter_path TEXT DEFAULT '',
    gguf_path TEXT DEFAULT '',
    ollama_model_name TEXT DEFAULT '',
    adapter_hash TEXT DEFAULT '',
    file_size_bytes INTEGER DEFAULT 0,
    eval_bleu REAL DEFAULT 0.0,
    eval_rouge_l REAL DEFAULT 0.0,
    eval_perplexity REAL DEFAULT 0.0,
    eval_custom TEXT DEFAULT '{}',
    status TEXT DEFAULT 'created'
        CHECK(status IN ('created','evaluated','promoted','demoted','archived')),
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(model_name, version)
);
CREATE INDEX IF NOT EXISTS idx_ft_models_name
    ON ft_model_versions(model_name);
CREATE INDEX IF NOT EXISTS idx_ft_models_status
    ON ft_model_versions(status);

-- 6. Active model overrides — runtime routing (D-FT-6)
CREATE TABLE IF NOT EXISTS ft_active_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    function_name TEXT NOT NULL,
    model_version_id TEXT NOT NULL REFERENCES ft_model_versions(id),
    ollama_model_name TEXT NOT NULL,
    routing_tier TEXT DEFAULT 'worker' CHECK(routing_tier IN ('worker','scanner','planner')),
    activated_by TEXT DEFAULT '',
    activation_reason TEXT DEFAULT '',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deactivated_at TIMESTAMP,
    UNIQUE(function_name, tenant_id, project_id)
);
CREATE INDEX IF NOT EXISTS idx_ft_active_function
    ON ft_active_models(function_name, deactivated_at);

-- 7. Evaluations — APPEND-ONLY (D6, D-FT-14)
CREATE TABLE IF NOT EXISTS ft_evaluations (
    id TEXT PRIMARY KEY,
    model_version_id TEXT NOT NULL REFERENCES ft_model_versions(id),
    eval_type TEXT NOT NULL DEFAULT 'automated'
        CHECK(eval_type IN ('automated','ab_comparison','human','regression')),
    test_set_size INTEGER DEFAULT 0,
    bleu_score REAL DEFAULT 0.0,
    rouge_l_score REAL DEFAULT 0.0,
    perplexity REAL DEFAULT 0.0,
    custom_metrics TEXT DEFAULT '{}',
    comparison_model TEXT DEFAULT '',
    comparison_scores TEXT DEFAULT '{}',
    statistical_significance REAL DEFAULT 0.0,
    pass_threshold INTEGER DEFAULT 0,
    details TEXT DEFAULT '{}',
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ft_evals_model
    ON ft_evaluations(model_version_id);

-- 8. Promotion log — APPEND-ONLY (D6, D-FT-16)
CREATE TABLE IF NOT EXISTS ft_promotion_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version_id TEXT NOT NULL REFERENCES ft_model_versions(id),
    action TEXT NOT NULL CHECK(action IN ('promoted','demoted','override_promoted','override_demoted','auto_promoted')),
    function_name TEXT NOT NULL,
    previous_model TEXT DEFAULT '',
    eval_score_summary TEXT DEFAULT '{}',
    reason TEXT DEFAULT '',
    actor TEXT DEFAULT '',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ft_promo_model
    ON ft_promotion_log(model_version_id);

-- 9. Hyperparameter search results — APPEND-ONLY (D6, D-FT-13)
CREATE TABLE IF NOT EXISTS ft_hyperparam_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id TEXT NOT NULL,
    job_id TEXT REFERENCES ft_training_jobs(id),
    hyperparams TEXT NOT NULL DEFAULT '{}',
    eval_bleu REAL DEFAULT 0.0,
    eval_rouge_l REAL DEFAULT 0.0,
    eval_perplexity REAL DEFAULT 0.0,
    composite_score REAL DEFAULT 0.0,
    is_best INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ft_hp_search
    ON ft_hyperparam_results(search_id);

-- ============================================================
-- TRAJECTORY-TO-TRAINING PIPELINE (D-FT-TRAJ)
-- ============================================================

-- 10. Trajectory metadata — mutable until finalized (captured_at set)
CREATE TABLE IF NOT EXISTS ft_trajectories (
    id TEXT PRIMARY KEY,
    trace_id TEXT DEFAULT '',
    workflow_type TEXT NOT NULL DEFAULT 'general'
        CHECK(workflow_type IN ('compliance','build','proposal','test','general')),
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK(source IN ('otel_spans','a2a_tasks','manual')),
    outcome TEXT NOT NULL DEFAULT 'partial'
        CHECK(outcome IN ('success','partial','failed')),
    reward REAL DEFAULT 0.0,
    step_count INTEGER DEFAULT 0,
    dataset_id TEXT DEFAULT '',
    sharegpt_json TEXT DEFAULT '{}',
    project_id TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    captured_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ft_traj_workflow
    ON ft_trajectories(workflow_type, outcome);
CREATE INDEX IF NOT EXISTS idx_ft_traj_reward
    ON ft_trajectories(reward, outcome);

-- 11. Trajectory steps — APPEND-ONLY (D6, D-FT-TRAJ)
CREATE TABLE IF NOT EXISTS ft_trajectory_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trajectory_id TEXT NOT NULL REFERENCES ft_trajectories(id),
    step_index INTEGER NOT NULL,
    tool_name TEXT DEFAULT '',
    tool_input TEXT DEFAULT '{}',
    tool_output TEXT DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'success'
        CHECK(status IN ('success','error','skipped')),
    duration_ms INTEGER DEFAULT 0,
    span_id TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ft_traj_steps_traj
    ON ft_trajectory_steps(trajectory_id, step_index);

-- ============================================================
-- RAG-TO-FT PIPELINE (D-KARL-5)
-- ============================================================

-- Pipeline execution tracking (append-only)
CREATE TABLE IF NOT EXISTS ft_pipeline_runs (
    id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL DEFAULT 'rag_to_ft',
    source_type TEXT,
    chunks_processed INTEGER DEFAULT 0,
    pairs_generated INTEGER DEFAULT 0,
    pairs_approved INTEGER DEFAULT 0,
    retrain_triggered INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running' CHECK(status IN ('running','completed','failed','dry_run')),
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ft_pipeline_source
    ON ft_pipeline_runs(source_type, status);

-- Operator-configured indicator baselines (D-BASELINE-1)
CREATE TABLE IF NOT EXISTS indicator_baselines (
    id TEXT PRIMARY KEY,
    indicator_name TEXT NOT NULL,
    indicator_category TEXT DEFAULT 'general',
    scope TEXT NOT NULL DEFAULT 'project'
        CHECK(scope IN ('global', 'platform', 'tenant', 'project', 'user')),
    scope_id TEXT,
    threshold_score REAL NOT NULL,
    severity_band TEXT DEFAULT 'medium'
        CHECK(severity_band IN ('low', 'medium', 'high', 'critical')),
    operator_id TEXT NOT NULL,
    rationale TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_indicator_baselines_scope
    ON indicator_baselines(scope, scope_id, is_active);
CREATE INDEX IF NOT EXISTS idx_indicator_baselines_name
    ON indicator_baselines(indicator_name, is_active);
CREATE INDEX IF NOT EXISTS idx_indicator_baselines_operator
    ON indicator_baselines(operator_id, created_at);

-- Observed indicator scores with snapshotted evaluation results (D-SCORE-1)
CREATE TABLE IF NOT EXISTS indicator_scores (
    id TEXT PRIMARY KEY,
    indicator_name TEXT NOT NULL,
    indicator_category TEXT DEFAULT 'general',
    scope TEXT NOT NULL DEFAULT 'project'
        CHECK(scope IN ('global', 'platform', 'tenant', 'project', 'user')),
    scope_id TEXT,
    score REAL NOT NULL
        CHECK(score >= 0),
    score_type TEXT DEFAULT 'raw'
        CHECK(score_type IN ('raw', 'normalized', 'aggregated')),
    source TEXT,
    operator_id TEXT,
    baseline_id TEXT,
    exceeded INTEGER,
    delta REAL,
    severity_at_time TEXT
        CHECK(severity_at_time IN ('low', 'medium', 'high', 'critical')),
    evaluated_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_indicator_scores_name
    ON indicator_scores(indicator_name, created_at);
CREATE INDEX IF NOT EXISTS idx_indicator_scores_scope
    ON indicator_scores(scope, scope_id);
CREATE INDEX IF NOT EXISTS idx_indicator_scores_baseline
    ON indicator_scores(baseline_id);
CREATE INDEX IF NOT EXISTS idx_indicator_scores_exceeded
    ON indicator_scores(exceeded, created_at)
    WHERE exceeded = 1;

-- Quality monitoring snapshots (append-only, D-KARL-8)
CREATE TABLE IF NOT EXISTS ft_quality_snapshots (
    id TEXT PRIMARY KEY,
    snapshot_type TEXT NOT NULL CHECK(snapshot_type IN ('rag_eval','ft_eval')),
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    baseline_value REAL,
    below_threshold INTEGER DEFAULT 0,
    details TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ft_quality_type
    ON ft_quality_snapshots(snapshot_type, metric_name);

-- ============================================================
-- WRITEGUARD SUBSYSTEM (Phase 65, D-WG-1 through D-WG-14)
-- ============================================================

-- 1. Style guides — 5-layer cascade (mutable, versioned per D183)
CREATE TABLE IF NOT EXISTS wg_style_guides (
    id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('platform','tenant','program','project','user')),
    scope_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    guide_name TEXT NOT NULL,
    guide_yaml TEXT NOT NULL,
    inherits_from TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_active INTEGER DEFAULT 1,
    change_summary TEXT,
    classification TEXT DEFAULT 'CUI',
    PRIMARY KEY (id, version)
);
CREATE INDEX IF NOT EXISTS idx_wg_style_guides_scope
    ON wg_style_guides(scope, scope_id);

-- 2. Style guide locks — ISSO-lockable dimensions (D-WG-3)
CREATE TABLE IF NOT EXISTS wg_style_guide_locks (
    id TEXT PRIMARY KEY,
    -- guide_id references a style guide by id only; wg_style_guides has a
    -- composite PK (id, version), so id alone is not unique and a SQL FK is
    -- invalid on PostgreSQL ("no unique constraint matching given keys").
    -- A lock applies to a guide id across all its versions, so no per-row FK.
    guide_id TEXT NOT NULL,
    dimension_path TEXT NOT NULL,
    lock_owner_role TEXT NOT NULL CHECK(lock_owner_role IN ('isso','architect','pm','admin')),
    locked_by TEXT NOT NULL,
    locked_at TEXT NOT NULL DEFAULT (datetime('now')),
    reason TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_wg_locks_guide
    ON wg_style_guide_locks(guide_id);

-- 3. Analysis results — APPEND-ONLY (D6, D-WG-9)
CREATE TABLE IF NOT EXISTS wg_analysis_results (
    id TEXT PRIMARY KEY,
    input_text_hash TEXT NOT NULL,
    input_length INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL DEFAULT 'inline' CHECK(mode IN ('inline','batch')),
    readability_flesch REAL DEFAULT 0.0,
    readability_gunning_fog REAL DEFAULT 0.0,
    readability_grade_level REAL DEFAULT 0.0,
    grammar_error_count INTEGER DEFAULT 0,
    passive_voice_pct REAL DEFAULT 0.0,
    avg_sentence_length REAL DEFAULT 0.0,
    tone_profile TEXT DEFAULT '{}',
    coherence_score REAL DEFAULT 0.0,
    plagiarism_max_similarity REAL DEFAULT 0.0,
    ai_content_score REAL DEFAULT 0.0,
    overall_quality_score REAL DEFAULT 0.0,
    style_guide_id TEXT,
    section_id TEXT,
    opportunity_id TEXT,
    document_name TEXT DEFAULT '',
    findings_count INTEGER DEFAULT 0,
    analysis_duration_ms INTEGER DEFAULT 0,
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    user_id TEXT DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wg_results_hash
    ON wg_analysis_results(input_text_hash);
CREATE INDEX IF NOT EXISTS idx_wg_results_section
    ON wg_analysis_results(section_id);

-- 4. Analysis findings — APPEND-ONLY (D6, D-WG-9)
CREATE TABLE IF NOT EXISTS wg_analysis_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id TEXT NOT NULL REFERENCES wg_analysis_results(id),
    category TEXT NOT NULL CHECK(category IN (
        'grammar','spelling','punctuation','readability','sentence_length',
        'passive_voice','tone','style','coherence','plagiarism','ai_content',
        'cui_marking','classification','govcon_compliance','win_theme',
        'cross_volume','custom_rule'
    )),
    severity TEXT NOT NULL DEFAULT 'info'
        CHECK(severity IN ('critical','high','medium','low','info')),
    message TEXT NOT NULL,
    suggestion TEXT DEFAULT '',
    context TEXT DEFAULT '',
    line_number INTEGER DEFAULT 0,
    char_offset INTEGER DEFAULT 0,
    char_length INTEGER DEFAULT 0,
    rule_id TEXT DEFAULT '',
    confidence REAL DEFAULT 1.0,
    auto_fixable INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wg_findings_result
    ON wg_analysis_findings(result_id);
CREATE INDEX IF NOT EXISTS idx_wg_findings_category
    ON wg_analysis_findings(category);

-- 5. Snippets — reusable writing templates (D-WG-7)
CREATE TABLE IF NOT EXISTS wg_snippets (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN (
        'boilerplate','transition','introduction','conclusion',
        'methodology','compliance','evidence','custom'
    )),
    domain TEXT DEFAULT 'general',
    tags TEXT DEFAULT '[]',
    usage_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active' CHECK(status IN ('active','archived')),
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_wg_snippets_category
    ON wg_snippets(category);

-- 6. Batch runs — batch analysis sessions (D-WG-9)
CREATE TABLE IF NOT EXISTS wg_batch_runs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    document_count INTEGER DEFAULT 0,
    completed_count INTEGER DEFAULT 0,
    avg_quality_score REAL DEFAULT 0.0,
    cross_doc_consistency_score REAL DEFAULT 0.0,
    status TEXT DEFAULT 'pending'
        CHECK(status IN ('pending','running','completed','failed')),
    summary TEXT DEFAULT '{}',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- 7. Glossary — unified terminology management (D-WG-4c)
CREATE TABLE IF NOT EXISTS wg_glossary (
    id TEXT PRIMARY KEY,
    term TEXT NOT NULL,
    term_type TEXT NOT NULL CHECK(term_type IN (
        'acronym','preferred','deprecated','banned','custom_spell','required'
    )),
    expansion TEXT NOT NULL DEFAULT '',      -- full form for acronyms
    replacement TEXT NOT NULL DEFAULT '',    -- what to use instead (deprecated/banned)
    definition TEXT NOT NULL DEFAULT '',     -- hover tooltip text
    domain TEXT NOT NULL DEFAULT 'general'
        CHECK(domain IN ('general','far','nist','cyber','project')),
    scope TEXT NOT NULL DEFAULT 'platform'
        CHECK(scope IN ('platform','tenant','program','project','user')),
    scope_id TEXT NOT NULL DEFAULT '',       -- tenant/program/project/user ID
    case_sensitive INTEGER NOT NULL DEFAULT 1,
    enforcement TEXT NOT NULL DEFAULT 'suggest'
        CHECK(enforcement IN ('suggest','warn','block')),
    source TEXT NOT NULL DEFAULT 'admin'
        CHECK(source IN ('builtin','admin','user','import:far','import:nist','import:cui')),
    approved_by TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_active INTEGER NOT NULL DEFAULT 1,
    classification TEXT NOT NULL DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_wg_glossary_term   ON wg_glossary(term);
CREATE INDEX IF NOT EXISTS idx_wg_glossary_type   ON wg_glossary(term_type);
CREATE INDEX IF NOT EXISTS idx_wg_glossary_scope  ON wg_glossary(scope, scope_id);
CREATE INDEX IF NOT EXISTS idx_wg_glossary_domain ON wg_glossary(domain);
CREATE INDEX IF NOT EXISTS idx_wg_glossary_active ON wg_glossary(is_active);

-- 7a. Proposal taxonomy — customer-specific topic trees (D-WG-14)
CREATE TABLE IF NOT EXISTS proposal_taxonomy (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES proposal_opportunities(id),
    parent_id TEXT REFERENCES proposal_taxonomy(id),
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    weight REAL NOT NULL DEFAULT 1.0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_tax_opp ON proposal_taxonomy(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_prop_tax_parent ON proposal_taxonomy(parent_id);

-- 8. Style profiles — per-user/project writing style snapshots (D-WG-10b)
CREATE TABLE IF NOT EXISTS wg_style_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    profile_json TEXT NOT NULL DEFAULT '{}',
    scope TEXT NOT NULL CHECK(scope IN ('platform','tenant','program','project','user')) DEFAULT 'user',
    scope_id TEXT NOT NULL DEFAULT '',
    tone TEXT DEFAULT '',
    voice TEXT DEFAULT '',
    formality TEXT CHECK(formality IN ('formal','semi-formal','informal','')) DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_active INTEGER NOT NULL DEFAULT 1,
    classification TEXT NOT NULL DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_wg_style_profiles_scope  ON wg_style_profiles(scope, scope_id);
CREATE INDEX IF NOT EXISTS idx_wg_style_profiles_active ON wg_style_profiles(is_active);

-- Phase 68: Draft generation batch job tracking (D-P68-3)
CREATE TABLE IF NOT EXISTS draft_generation_jobs (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    job_type TEXT DEFAULT 'batch',
    total_sections INTEGER DEFAULT 0,
    completed_sections INTEGER DEFAULT 0,
    current_section_id TEXT,
    current_section_title TEXT,
    current_step TEXT,
    steps_completed TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pending'
        CHECK(status IN ('pending', 'running', 'completed', 'failed')),
    error_message TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI // SP-CTI'
);
CREATE INDEX IF NOT EXISTS idx_draftjob_opp ON draft_generation_jobs(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_draftjob_status ON draft_generation_jobs(status);

-- ═══════════════════════════════════════════════════════════════════
-- DataBridge — Universal Data & Storage Connector (D-DB-1 through D-DB-20)
-- ═══════════════════════════════════════════════════════════════════

-- Connections (D-DB-12, D-DB-13)
CREATE TABLE IF NOT EXISTS db_connections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    connector_type TEXT NOT NULL
        CHECK(connector_type IN ('database','cloud_storage','file',
                                  'streaming','saas_api','on_prem')),
    connector_name TEXT NOT NULL,
    config_yaml TEXT NOT NULL,
    auth_method TEXT NOT NULL DEFAULT 'none'
        CHECK(auth_method IN ('none','api_key','oauth2','iam_role',
                               'connection_string','pki','password','pat')),
    auth_secret_ref TEXT,
    sync_direction TEXT DEFAULT 'read'
        CHECK(sync_direction IN ('read','write','bidirectional')),
    status TEXT DEFAULT 'configured'
        CHECK(status IN ('configured','connected','syncing','error','disabled')),
    health_status TEXT DEFAULT 'unknown'
        CHECK(health_status IN ('unknown','healthy','degraded','unhealthy')),
    last_health_check TEXT,
    last_sync TEXT,
    sync_cadence_minutes INTEGER DEFAULT 60,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    impact_level TEXT DEFAULT 'IL4'
        CHECK(impact_level IN ('IL2','IL4','IL5','IL6')),
    tenant_id TEXT NOT NULL DEFAULT 'default',
    project_id TEXT,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(tenant_id, name)
);
CREATE INDEX IF NOT EXISTS idx_db_conn_tenant ON db_connections(tenant_id);
CREATE INDEX IF NOT EXISTS idx_db_conn_type ON db_connections(connector_type);
CREATE INDEX IF NOT EXISTS idx_db_conn_status ON db_connections(status);

-- Schema registry (versioned, D-DB-7)
CREATE TABLE IF NOT EXISTS db_schemas (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL REFERENCES db_connections(id),
    table_name TEXT NOT NULL,
    schema_json TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    inferred_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI // SP-CTI',
    UNIQUE(connection_id, table_name, version)
);
CREATE INDEX IF NOT EXISTS idx_db_schema_conn ON db_schemas(connection_id);

-- Sync log (append-only, D-DB-6)
CREATE TABLE IF NOT EXISTS db_sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id TEXT NOT NULL REFERENCES db_connections(id),
    sync_direction TEXT NOT NULL CHECK(sync_direction IN ('read','write')),
    table_name TEXT,
    rows_read INTEGER DEFAULT 0,
    rows_written INTEGER DEFAULT 0,
    rows_failed INTEGER DEFAULT 0,
    bytes_transferred INTEGER DEFAULT 0,
    sync_duration_ms INTEGER,
    incremental_key TEXT,
    incremental_value TEXT,
    error_details TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    synced_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_db_sync_conn ON db_sync_log(connection_id);
CREATE INDEX IF NOT EXISTS idx_db_sync_time ON db_sync_log(synced_at);

-- Agent access decisions for external DataBridge connectors.
--
-- DISTINCT from db_sync_log, which records sync OPERATIONS and requires a
-- connection_id FK plus row counts. An authorization decision is a different
-- thing: a DENIED fetch has no connection, transferred nothing, and is the most
-- important row in the table. Forcing it into a sync log meant the insert
-- failed silently and the trail was empty exactly when it mattered.
--
-- Append-only (NIST AU) -- see APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py.
--
-- THIS IS THE SQLITE INIT FALLBACK, NOT THE PRIMARY DEFINITION. On the primary
-- PostgreSQL backend the table is created by migration
-- 20260817010532_databridge_agent_access_log, which carries the PG-native DDL.
-- This block being the ONLY definition is why the table did not exist on PG at
-- all: AUTOINCREMENT and datetime('now') are SQLite syntax, so nothing here ever
-- ran there, and every audit insert raised UndefinedTable into a swallowed
-- warning. Keep the two in step -- a column added in one belongs in the other.
--
-- tenant_id and classification are the RLS predicate columns get_connection()
-- injects. classification holds the LABEL ('CUI'), never the banner
-- ('CUI // SP-CTI'): the predicate is `classification IN (<labels dominated by
-- the caller's clearance>)`, and a banner string matches no label at any
-- clearance, so such a row is written, retained and unreadable.
CREATE TABLE IF NOT EXISTS databridge_agent_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL DEFAULT 'unknown',
    connector_name TEXT NOT NULL DEFAULT '',
    table_name TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT 'denied' CHECK(decision IN ('allowed','denied')),
    reason TEXT NOT NULL DEFAULT '',
    rows_returned INTEGER NOT NULL DEFAULT 0,
    redactions_applied INTEGER NOT NULL DEFAULT 0,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    classification TEXT NOT NULL DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_db_agent_access_agent ON databridge_agent_access_log(agent_id);
CREATE INDEX IF NOT EXISTS idx_db_agent_access_decision ON databridge_agent_access_log(decision);
CREATE INDEX IF NOT EXISTS idx_db_agent_access_tenant ON databridge_agent_access_log(tenant_id, created_at);

-- Connector configuration registry
CREATE TABLE IF NOT EXISTS db_connector_configs (
    id TEXT PRIMARY KEY,
    connector_name TEXT NOT NULL UNIQUE,
    connector_type TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT,
    config_schema_json TEXT,
    requires_dependencies TEXT,
    is_builtin INTEGER DEFAULT 1,
    tier TEXT DEFAULT 'free' CHECK(tier IN ('free','paid')),
    created_at TEXT DEFAULT (datetime('now'))
);

-- Cloud storage path registry (Phase 3)
CREATE TABLE IF NOT EXISTS db_storage_paths (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL REFERENCES db_connections(id),
    logical_name TEXT NOT NULL,
    physical_path TEXT NOT NULL,
    file_format TEXT NOT NULL
        CHECK(file_format IN ('parquet','csv','json','avro','orc','ipc','auto')),
    partition_scheme TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(connection_id, logical_name)
);

-- Schema mappings (versioned, D-DB-7)
CREATE TABLE IF NOT EXISTS db_mappings (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_connection_id TEXT NOT NULL REFERENCES db_connections(id),
    target_connection_id TEXT NOT NULL REFERENCES db_connections(id),
    source_table TEXT NOT NULL,
    target_table TEXT NOT NULL,
    mapping_json TEXT NOT NULL,
    mapping_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT DEFAULT 'draft'
        CHECK(status IN ('draft','active','archived')),
    cui_field_markings TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(tenant_id, name, version)
);
CREATE INDEX IF NOT EXISTS idx_db_map_src ON db_mappings(source_connection_id);
CREATE INDEX IF NOT EXISTS idx_db_map_tgt ON db_mappings(target_connection_id);

-- Mapping execution log (append-only, D-DB-6)
CREATE TABLE IF NOT EXISTS db_mapping_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mapping_id TEXT NOT NULL REFERENCES db_mappings(id),
    rows_mapped INTEGER DEFAULT 0,
    rows_failed INTEGER DEFAULT 0,
    transform_errors TEXT,
    duration_ms INTEGER,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    executed_at TEXT DEFAULT (datetime('now'))
);

-- Lookup tables for transforms (D-DB-15)
CREATE TABLE IF NOT EXISTS db_lookup_tables (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    table_json TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(tenant_id, name)
);

-- Active stream subscriptions (Phase 6)
CREATE TABLE IF NOT EXISTS db_streams (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL REFERENCES db_connections(id),
    stream_name TEXT NOT NULL,
    consumer_group TEXT,
    status TEXT DEFAULT 'stopped'
        CHECK(status IN ('stopped','running','paused','error')),
    batch_window_seconds INTEGER DEFAULT 10,
    batch_size INTEGER DEFAULT 1000,
    last_offset TEXT,
    rows_consumed INTEGER DEFAULT 0,
    errors_count INTEGER DEFAULT 0,
    started_at TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_db_stream_conn ON db_streams(connection_id);
CREATE INDEX IF NOT EXISTS idx_db_stream_status ON db_streams(status);

-- OAuth2 token cache (Phase 7)
CREATE TABLE IF NOT EXISTS db_oauth_tokens (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL REFERENCES db_connections(id),
    access_token_ref TEXT NOT NULL,
    refresh_token_ref TEXT,
    token_type TEXT DEFAULT 'bearer',
    expires_at TEXT,
    scopes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(connection_id)
);

-- On-prem agent registrations (Phase 8)
CREATE TABLE IF NOT EXISTS db_agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    agent_key_hash TEXT NOT NULL,
    status TEXT DEFAULT 'registered'
        CHECK(status IN ('registered','connected','disconnected','revoked')),
    last_heartbeat TEXT,
    ip_address TEXT,
    agent_version TEXT,
    capabilities_json TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_db_agent_tenant ON db_agents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_db_agent_status ON db_agents(status);

-- DataBridge Messages (Phase 71, D-DB-26, append-only/immutable)
CREATE TABLE IF NOT EXISTS db_messages (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
    channel_id TEXT NOT NULL,
    channel_name TEXT,
    thread_id TEXT,
    author_id TEXT NOT NULL,
    author_name TEXT,
    content TEXT NOT NULL,
    content_html TEXT,
    attachments_json TEXT,
    platform_message_id TEXT,
    reply_to_message_id TEXT,
    agent_routed_to TEXT,
    agent_response_task_id TEXT,
    pii_scan_status TEXT DEFAULT 'pending'
        CHECK(pii_scan_status IN ('pending','clean','flagged','error')),
    pii_findings_json TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_db_msg_conn ON db_messages(connection_id);
CREATE INDEX IF NOT EXISTS idx_db_msg_channel ON db_messages(channel_id);
CREATE INDEX IF NOT EXISTS idx_db_msg_platform ON db_messages(platform);
CREATE INDEX IF NOT EXISTS idx_db_msg_time ON db_messages(created_at);
CREATE INDEX IF NOT EXISTS idx_db_msg_pii ON db_messages(pii_scan_status);

-- Agent routing overrides (Phase 71, D-DB-25)
CREATE TABLE IF NOT EXISTS db_message_routing (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    channel_id TEXT,
    platform TEXT NOT NULL DEFAULT '',
    target_agent_name TEXT NOT NULL DEFAULT 'orchestrator',
    target_agent_url TEXT,
    routing_rules_json TEXT,
    enabled INTEGER DEFAULT 1,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(connection_id, channel_id)
);

-- Messaging daemon state (Phase 71, D-DB-23)
CREATE TABLE IF NOT EXISTS db_messaging_daemon_state (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    listener_type TEXT NOT NULL
        CHECK(listener_type IN ('long_poll','websocket','gateway','webhook_only','error')),
    status TEXT DEFAULT 'stopped'
        CHECK(status IN ('stopped','starting','running','error','reconnecting')),
    last_heartbeat TEXT,
    error_details TEXT,
    pid INTEGER,
    thread_name TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(connection_id)
);

-- ============================================================
-- CONNECTOR FORGE (Phase 78, D-CF-1 through D-CF-10)
-- Dynamic DataBridge connector generation, validation, sandbox
-- ============================================================

-- Generated connector code and metadata
CREATE TABLE IF NOT EXISTS db_forge_connectors (
    id TEXT PRIMARY KEY,
    connector_name TEXT NOT NULL,
    connector_type TEXT NOT NULL,
    base_class TEXT NOT NULL,
    protocol TEXT NOT NULL,
    generated_code TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'sandboxed'
        CHECK(status IN ('sandboxed','promoted','published','deprecated')),
    spec_id TEXT,
    promoted_by TEXT,
    promoted_at TEXT,
    published_slug TEXT,
    marketplace_artifact_id TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    project_id TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_forge_connector_name ON db_forge_connectors(connector_name);
CREATE INDEX IF NOT EXISTS idx_forge_connector_status ON db_forge_connectors(status);
CREATE INDEX IF NOT EXISTS idx_forge_connector_tenant ON db_forge_connectors(tenant_id);

-- Input specs used to generate connectors
CREATE TABLE IF NOT EXISTS db_forge_specs (
    id TEXT PRIMARY KEY,
    input_type TEXT NOT NULL
        CHECK(input_type IN ('openapi','wsdl','html','yaml','manual')),
    input_source TEXT NOT NULL DEFAULT '',
    raw_input TEXT NOT NULL,
    parsed_manifest TEXT,
    detected_protocol TEXT,
    target_base_class TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Static + integration validation results per connector version
CREATE TABLE IF NOT EXISTS db_forge_validations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connector_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    stage TEXT NOT NULL
        CHECK(stage IN ('py_compile','ruff','ast_abc','bandit','secret_scan',
                        'import_whitelist','integration')),
    passed INTEGER NOT NULL DEFAULT 0,
    details TEXT,
    duration_ms INTEGER,
    run_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_forge_val_connector ON db_forge_validations(connector_id);

-- Sandbox runtime events (append-only, NIST AU)
CREATE TABLE IF NOT EXISTS db_forge_sandbox_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connector_id TEXT NOT NULL,
    sandbox_type TEXT NOT NULL CHECK(sandbox_type IN ('docker','subprocess')),
    event_type TEXT NOT NULL
        CHECK(event_type IN ('start','connect','read','write','schema',
                             'health','error','stop','import','instantiate',
                             'list_tables')),
    details TEXT,
    duration_ms INTEGER,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    logged_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_forge_sandbox_conn ON db_forge_sandbox_log(connector_id);

-- Promotion approvals (append-only, NIST AU)
CREATE TABLE IF NOT EXISTS db_forge_promotions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connector_id TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    promoted_by TEXT NOT NULL,
    review_notes TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    promoted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_forge_promo_conn ON db_forge_promotions(connector_id);

-- ============================================================
-- CLOUDFORGE: LANDING ZONES (D-CF-1, D-CF-15)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_landing_zones (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cloud_type TEXT NOT NULL CHECK(cloud_type IN
        ('aws_commercial','aws_govcloud','aws_secret',
         'azure_commercial','azure_gov','azure_secret','gcp','oci')),
    region TEXT NOT NULL,
    impact_level TEXT NOT NULL DEFAULT 'IL4' CHECK(impact_level IN ('IL2','IL4','IL5','IL6')),
    environment TEXT NOT NULL DEFAULT 'staging' CHECK(environment IN ('dev','staging','production','dr')),
    blueprint_name TEXT NOT NULL,
    iac_engine TEXT DEFAULT 'terraform' CHECK(iac_engine IN ('terraform','opentofu','pulumi')),
    status TEXT DEFAULT 'planned' CHECK(status IN
        ('planned','provisioning','active','updating','destroying','destroyed','error','drifted')),
    iac_output_path TEXT,
    state_file_path TEXT,
    estimated_monthly_cost_usd REAL DEFAULT 0.0,
    inherited_controls_json TEXT,
    parameters_json TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    project_id TEXT,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(tenant_id, name)
);
CREATE INDEX IF NOT EXISTS idx_cf_lz_cloud ON cf_landing_zones(cloud_type);
CREATE INDEX IF NOT EXISTS idx_cf_lz_status ON cf_landing_zones(status);
CREATE INDEX IF NOT EXISTS idx_cf_lz_tenant ON cf_landing_zones(tenant_id);

-- ============================================================
-- CLOUDFORGE: PROVISIONING LOG (append-only, D-CF-15)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_provision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('plan','apply','validate','destroy','drift_check','rollback')),
    status TEXT NOT NULL CHECK(status IN ('started','completed','failed','rolled_back')),
    resources_affected INTEGER DEFAULT 0,
    duration_ms INTEGER,
    iac_plan_json TEXT,
    error_details TEXT,
    actor TEXT DEFAULT 'cloudforge',
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cf_plog_zone ON cf_provision_log(zone_id);
CREATE INDEX IF NOT EXISTS idx_cf_plog_action ON cf_provision_log(action);

-- ============================================================
-- CLOUDFORGE: ATO PHASES (D-CF-14)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_ato_phases (
    id TEXT PRIMARY KEY,
    zone_id TEXT NOT NULL,
    phase_number INTEGER NOT NULL CHECK(phase_number BETWEEN 1 AND 5),
    phase_name TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','in_progress','passed','failed','waived')),
    started_at TEXT,
    completed_at TEXT,
    gate_results_json TEXT,
    evidence_artifact_ids TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    UNIQUE(zone_id, phase_number)
);
CREATE INDEX IF NOT EXISTS idx_cf_ato_zone ON cf_ato_phases(zone_id);

-- ============================================================
-- CLOUDFORGE: MIGRATION ASSESSMENTS (D-CF-7, D-CF-12)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_migration_assessments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_cloud TEXT NOT NULL,
    target_cloud TEXT NOT NULL,
    status TEXT DEFAULT 'draft' CHECK(status IN
        ('draft','assessing','planned','executing','completed','failed','cancelled')),
    workload_count INTEGER DEFAULT 0,
    total_data_volume_gb REAL DEFAULT 0.0,
    overall_risk_score REAL DEFAULT 0.0,
    overall_complexity_score REAL DEFAULT 0.0,
    strategy TEXT DEFAULT 'phased' CHECK(strategy IN ('phased','big_bang','trickle','replatform','hybrid')),
    estimated_downtime_hours REAL DEFAULT 0.0,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    project_id TEXT,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cf_mig_status ON cf_migration_assessments(status);
CREATE INDEX IF NOT EXISTS idx_cf_mig_tenant ON cf_migration_assessments(tenant_id);

-- ============================================================
-- CLOUDFORGE: MIGRATION WORKLOADS
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_migration_workloads (
    id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL,
    name TEXT NOT NULL,
    workload_type TEXT NOT NULL CHECK(workload_type IN
        ('vm','container','database','storage','serverless','network','identity','application','data_pipeline')),
    source_details_json TEXT,
    target_details_json TEXT,
    risk_score REAL DEFAULT 0.0,
    complexity_score REAL DEFAULT 0.0,
    data_volume_gb REAL DEFAULT 0.0,
    dependencies_json TEXT,
    migration_phase INTEGER DEFAULT 1,
    status TEXT DEFAULT 'pending' CHECK(status IN
        ('pending','migrating','validating','completed','failed','rolled_back')),
    databridge_sync_id TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cf_mwl_assess ON cf_migration_workloads(assessment_id);
CREATE INDEX IF NOT EXISTS idx_cf_mwl_status ON cf_migration_workloads(status);

-- ============================================================
-- CLOUDFORGE: HYBRID TOPOLOGY (D-CF-13)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_hybrid_topology (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    topology_type TEXT NOT NULL CHECK(topology_type IN ('hub_spoke','mesh','gateway','point_to_point')),
    status TEXT DEFAULT 'planned' CHECK(status IN ('planned','deploying','active','degraded','error')),
    nodes_json TEXT,
    edges_json TEXT,
    health_status TEXT DEFAULT 'unknown',
    last_health_check TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    project_id TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cf_topo_tenant ON cf_hybrid_topology(tenant_id);

-- ============================================================
-- CLOUDFORGE: HYBRID CONNECTIONS (D-CF-13)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_hybrid_connections (
    id TEXT PRIMARY KEY,
    topology_id TEXT NOT NULL,
    source_zone_id TEXT NOT NULL,
    target_zone_id TEXT NOT NULL,
    connection_type TEXT NOT NULL CHECK(connection_type IN
        ('vpn','peering','direct_connect','expressroute','interconnect','transit_gateway','relay')),
    status TEXT DEFAULT 'planned' CHECK(status IN
        ('planned','establishing','active','degraded','error','disconnected')),
    bandwidth_mbps INTEGER,
    latency_ms INTEGER,
    encrypted INTEGER DEFAULT 1,
    health_status TEXT DEFAULT 'unknown',
    config_json TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cf_conn_topo ON cf_hybrid_connections(topology_id);
CREATE INDEX IF NOT EXISTS idx_cf_conn_src ON cf_hybrid_connections(source_zone_id);
CREATE INDEX IF NOT EXISTS idx_cf_conn_tgt ON cf_hybrid_connections(target_zone_id);

-- ============================================================
-- CLOUDFORGE: FINOPS COST RECORDS (D-CF-11)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_cost_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_id TEXT,
    cloud_type TEXT NOT NULL,
    service_name TEXT NOT NULL,
    cost_usd REAL NOT NULL,
    usage_quantity REAL,
    usage_unit TEXT,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    tags_json TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    collected_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cf_cost_zone ON cf_cost_records(zone_id);
CREATE INDEX IF NOT EXISTS idx_cf_cost_period ON cf_cost_records(period_start, period_end);

-- ============================================================
-- CLOUDFORGE: FINOPS BUDGETS
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_budgets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    zone_id TEXT,
    monthly_limit_usd REAL NOT NULL,
    alert_threshold_pct REAL DEFAULT 80.0,
    current_spend_usd REAL DEFAULT 0.0,
    period TEXT NOT NULL,
    status TEXT DEFAULT 'active' CHECK(status IN ('active','exceeded','closed')),
    classification TEXT DEFAULT 'CUI // SP-CTI',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cf_budget_tenant ON cf_budgets(tenant_id);

-- ============================================================
-- CLOUDFORGE: SIEM EVENTS (append-only, D-CF-10)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_siem_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_id TEXT,
    cloud_type TEXT NOT NULL,
    event_source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT DEFAULT 'info' CHECK(severity IN ('info','low','medium','high','critical')),
    raw_event_json TEXT,
    correlated_event_id TEXT,
    alert_fired INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    event_time TEXT NOT NULL,
    ingested_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cf_siem_zone ON cf_siem_events(zone_id);
CREATE INDEX IF NOT EXISTS idx_cf_siem_sev ON cf_siem_events(severity);
CREATE INDEX IF NOT EXISTS idx_cf_siem_time ON cf_siem_events(event_time);

-- ============================================================
-- SIEM EVENTS (append-only) — agentic AI safety_layer forwarder sink
-- Mirrors public.siem_events in pg_consolidated.sql; written best-effort
-- by tools/agentic_ai_canvas/safety_layer.py::_forward_siem
-- ============================================================
CREATE TABLE IF NOT EXISTS siem_events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT DEFAULT 'INFO' NOT NULL CHECK(severity IN ('INFO','LOW','MEDIUM','HIGH','CRITICAL')),
    detail TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now')) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_siem_events_source ON siem_events(source);
CREATE INDEX IF NOT EXISTS idx_siem_events_severity ON siem_events(severity);
CREATE INDEX IF NOT EXISTS idx_siem_events_created ON siem_events(created_at);

-- ============================================================
-- CLOUDFORGE: SHIFT EMULATOR SESSIONS (D-CF-6)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_shift_sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    emulated_il_level TEXT NOT NULL CHECK(emulated_il_level IN ('IL4','IL5','IL6')),
    container_ids_json TEXT,
    network_policy_json TEXT,
    status TEXT DEFAULT 'stopped' CHECK(status IN ('stopped','starting','running','error')),
    classification TEXT DEFAULT 'CUI // SP-CTI',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now')),
    stopped_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cf_shift_tenant ON cf_shift_sessions(tenant_id);

-- ============================================================
-- CLOUDFORGE: CD HUB DEPLOYMENTS (D-CF-8)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_deployments (
    id TEXT PRIMARY KEY,
    zone_id TEXT NOT NULL,
    artifact_name TEXT NOT NULL,
    artifact_version TEXT NOT NULL,
    strategy TEXT DEFAULT 'rolling' CHECK(strategy IN ('rolling','canary','blue_green','recreate')),
    status TEXT DEFAULT 'pending' CHECK(status IN
        ('pending','deploying','healthy','degraded','failed','rolled_back')),
    canary_pct INTEGER DEFAULT 0,
    spoke_id TEXT,
    gitops_repo TEXT,
    gitops_path TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cf_deploy_zone ON cf_deployments(zone_id);
CREATE INDEX IF NOT EXISTS idx_cf_deploy_status ON cf_deployments(status);

-- ============================================================
-- CLOUDFORGE: CONTAINER SCANS (D-CF-9)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_container_scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_name TEXT NOT NULL,
    image_tag TEXT NOT NULL,
    scan_type TEXT NOT NULL CHECK(scan_type IN ('vulnerability','stig','sbom','cis_benchmark')),
    findings_count INTEGER DEFAULT 0,
    critical_count INTEGER DEFAULT 0,
    high_count INTEGER DEFAULT 0,
    results_json TEXT,
    hardened INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    scanned_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cf_cscan_image ON cf_container_scans(image_name);

-- ============================================================
-- CLOUDFORGE: RUNBOOKS (D-CF-19)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_runbooks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft','published','archived','deprecated')),
    template_source TEXT,
    tasks_json TEXT NOT NULL,
    edges_json TEXT NOT NULL,
    snippets_used TEXT,
    estimated_duration_minutes INTEGER,
    owner TEXT,
    tags TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    project_id TEXT,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(tenant_id, name, version)
);
CREATE INDEX IF NOT EXISTS idx_cf_rb_tenant ON cf_runbooks(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cf_rb_status ON cf_runbooks(status);

-- ============================================================
-- CLOUDFORGE: RUNBOOK EXECUTIONS (append-only, D-CF-20)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_runbook_executions (
    id TEXT PRIMARY KEY,
    runbook_id TEXT NOT NULL,
    runbook_version INTEGER NOT NULL,
    status TEXT DEFAULT 'pending' CHECK(status IN
        ('pending','running','completed','failed','cancelled','paused')),
    trigger_type TEXT DEFAULT 'manual' CHECK(trigger_type IN ('manual','scheduled','event','api')),
    triggered_by TEXT,
    started_at TEXT,
    completed_at TEXT,
    duration_ms INTEGER,
    task_results_json TEXT,
    parallel_paths_count INTEGER DEFAULT 0,
    tasks_completed INTEGER DEFAULT 0,
    tasks_failed INTEGER DEFAULT 0,
    tasks_skipped INTEGER DEFAULT 0,
    tasks_total INTEGER DEFAULT 0,
    error_summary TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cf_rbe_runbook ON cf_runbook_executions(runbook_id);
CREATE INDEX IF NOT EXISTS idx_cf_rbe_status ON cf_runbook_executions(status);
CREATE INDEX IF NOT EXISTS idx_cf_rbe_tenant ON cf_runbook_executions(tenant_id);

-- ============================================================
-- CLOUDFORGE: RUNBOOK TASK LOG (append-only, D-CF-21, NIST AU)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_runbook_task_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_name TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('started','completed','failed','skipped','retried','paused','resumed')),
    output_json TEXT,
    error_details TEXT,
    duration_ms INTEGER,
    actor TEXT DEFAULT 'cloudforge',
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cf_rtl_exec ON cf_runbook_task_log(execution_id);
CREATE INDEX IF NOT EXISTS idx_cf_rtl_task ON cf_runbook_task_log(task_id);

-- ============================================================
-- CLOUDFORGE: RUNBOOK SNIPPETS (D-CF-22)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_runbook_snippets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL CHECK(category IN
        ('dr_failover','backup','health_check','patching','incident','provisioning','migration','custom')),
    tasks_json TEXT NOT NULL,
    edges_json TEXT NOT NULL,
    usage_count INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(tenant_id, name)
);
CREATE INDEX IF NOT EXISTS idx_cf_snp_category ON cf_runbook_snippets(category);
CREATE INDEX IF NOT EXISTS idx_cf_snp_tenant ON cf_runbook_snippets(tenant_id);

-- ============================================================
-- CLOUDFORGE: APPLICATION METASTORE (D-CF-23)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_applications (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    app_type TEXT NOT NULL CHECK(app_type IN
        ('web','api','database','queue','cache','batch','streaming','edge','firmware','monolith','microservice','other')),
    status TEXT DEFAULT 'active' CHECK(status IN ('active','inactive','deprecated','decommissioned','discovered')),
    environment TEXT DEFAULT 'production' CHECK(environment IN ('dev','staging','production','dr')),
    rto_hours REAL,
    rpo_hours REAL,
    criticality TEXT DEFAULT 'medium' CHECK(criticality IN ('critical','high','medium','low')),
    owner_team TEXT,
    owner_email TEXT,
    zone_ids TEXT,
    device_ids TEXT,
    connection_ids TEXT,
    runbook_ids TEXT,
    metadata_json TEXT,
    discovery_source TEXT,
    tags TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    project_id TEXT,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(tenant_id, name, environment)
);
CREATE INDEX IF NOT EXISTS idx_cf_app_tenant ON cf_applications(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cf_app_type ON cf_applications(app_type);
CREATE INDEX IF NOT EXISTS idx_cf_app_status ON cf_applications(status);
CREATE INDEX IF NOT EXISTS idx_cf_app_criticality ON cf_applications(criticality);

-- ============================================================
-- CLOUDFORGE: APPLICATION DEPENDENCIES (adjacency list, D-CF-24)
-- ============================================================
CREATE TABLE IF NOT EXISTS cf_app_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_app_id TEXT NOT NULL,
    target_app_id TEXT NOT NULL,
    dependency_type TEXT NOT NULL CHECK(dependency_type IN
        ('hard','soft','data','network','auth','queue','shared_storage')),
    protocol TEXT,
    port INTEGER,
    description TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(source_app_id, target_app_id, dependency_type)
);
CREATE INDEX IF NOT EXISTS idx_cf_adep_src ON cf_app_dependencies(source_app_id);
CREATE INDEX IF NOT EXISTS idx_cf_adep_tgt ON cf_app_dependencies(target_app_id);
CREATE INDEX IF NOT EXISTS idx_cf_adep_tenant ON cf_app_dependencies(tenant_id);

-- ============================================================
-- INNOVATION: cATO LIVE EVIDENCE STREAM (D-INV-1)
-- ============================================================
CREATE TABLE IF NOT EXISTS cato_evidence_stream (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    control_id TEXT NOT NULL,
    oscal_artifact_type TEXT NOT NULL DEFAULT 'assessment-results',
    oscal_json TEXT NOT NULL,
    evidence_ids TEXT,
    stream_status TEXT NOT NULL DEFAULT 'current' CHECK(stream_status IN ('current','stale','expired')),
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cato_stream_project ON cato_evidence_stream(project_id);
CREATE INDEX IF NOT EXISTS idx_cato_stream_control ON cato_evidence_stream(control_id);

-- ============================================================
-- INNOVATION: COMPLIANCE TEMPLATE EXCHANGE (D-INV-5)
-- ============================================================
CREATE TABLE IF NOT EXISTS compliance_templates (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    name TEXT NOT NULL,
    description TEXT,
    template_type TEXT NOT NULL CHECK(template_type IN ('ssp_section','poam','narrative','control_set','checklist')),
    framework TEXT,
    content TEXT NOT NULL,
    version TEXT DEFAULT '1.0.0',
    author TEXT,
    author_org TEXT,
    provenance_hash TEXT,
    rating_sum REAL DEFAULT 0,
    rating_count INTEGER DEFAULT 0,
    download_count INTEGER DEFAULT 0,
    is_published INTEGER DEFAULT 0,
    marketplace_slug TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ct_project ON compliance_templates(project_id);
CREATE INDEX IF NOT EXISTS idx_ct_type ON compliance_templates(template_type);
CREATE INDEX IF NOT EXISTS idx_ct_framework ON compliance_templates(framework);

CREATE TABLE IF NOT EXISTS compliance_template_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id TEXT NOT NULL REFERENCES compliance_templates(id),
    user_id TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    comment TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(template_id, user_id)
);

-- ============================================================
-- INNOVATION: VSM DASHBOARD (D-INV-9)
-- ============================================================
CREATE TABLE IF NOT EXISTS vsm_stage_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    pipeline_run_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('architect','trace','link','assemble','stress_test')),
    event_type TEXT NOT NULL CHECK(event_type IN ('started','completed','failed','blocked')),
    actor TEXT,
    duration_seconds REAL,
    metadata TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_vsm_project ON vsm_stage_events(project_id);
CREATE INDEX IF NOT EXISTS idx_vsm_pipeline ON vsm_stage_events(pipeline_run_id);
CREATE INDEX IF NOT EXISTS idx_vsm_stage ON vsm_stage_events(stage);

CREATE TABLE IF NOT EXISTS vsm_dora_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    snapshot_date TEXT NOT NULL,
    deployment_frequency REAL,
    lead_time_hours REAL,
    change_failure_rate REAL,
    mttr_hours REAL,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dora_project ON vsm_dora_snapshots(project_id);

-- ============================================================
-- INNOVATION: NARRATIVE APPROVALS (D-INV-13)
-- ============================================================
CREATE TABLE IF NOT EXISTS narrative_approvals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    control_id TEXT NOT NULL,
    narrative_type TEXT NOT NULL CHECK(narrative_type IN ('ssp','poam','executive','control')),
    template_draft TEXT,
    llm_draft TEXT,
    llm_reviewed TEXT,
    final_narrative TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','pending_review','approved','rejected')),
    reviewer TEXT,
    review_comment TEXT,
    reviewed_at TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_narr_project ON narrative_approvals(project_id);
CREATE INDEX IF NOT EXISTS idx_narr_status ON narrative_approvals(status);
CREATE INDEX IF NOT EXISTS idx_narr_control ON narrative_approvals(control_id);

-- ============================================================
-- INNOVATION: DIGITAL THREAD HEATMAP SNAPSHOTS (D-INV-17)
-- ============================================================
CREATE TABLE IF NOT EXISTS thread_heatmap_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    matrix_json TEXT NOT NULL,
    total_links INTEGER,
    total_orphans INTEGER,
    overall_coverage REAL,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_heatmap_project ON thread_heatmap_snapshots(project_id);

-- ============================================================
-- INNOVATION: PR INTELLIGENCE REPORTS (D-INV-21)
-- ============================================================
CREATE TABLE IF NOT EXISTS pr_intelligence_reports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    pr_reference TEXT,
    diff_summary TEXT,
    files_changed INTEGER,
    security_findings TEXT,
    compliance_impacts TEXT,
    code_quality_delta TEXT,
    overall_status TEXT NOT NULL CHECK(overall_status IN ('pass','warn','fail')),
    report_json TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pr_intel_project ON pr_intelligence_reports(project_id);

-- ============================================================
-- INNOVATION: STRIDE THREAT MODELS (D-INV-25)
-- ============================================================
CREATE TABLE IF NOT EXISTS threat_models (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    description TEXT,
    components TEXT NOT NULL,
    data_flows TEXT,
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft','analyzed','reviewed','approved')),
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tm_project ON threat_models(project_id);

CREATE TABLE IF NOT EXISTS threat_findings (
    id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL REFERENCES threat_models(id),
    component_id TEXT NOT NULL,
    stride_category TEXT NOT NULL CHECK(stride_category IN ('spoofing','tampering','repudiation','information_disclosure','denial_of_service','elevation_of_privilege')),
    threat_description TEXT NOT NULL,
    risk_level TEXT NOT NULL CHECK(risk_level IN ('critical','high','moderate','low')),
    attack_technique TEXT,
    nist_controls TEXT,
    mitigation TEXT,
    poam_id TEXT,
    status TEXT DEFAULT 'open' CHECK(status IN ('open','mitigated','accepted','transferred')),
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tf_model ON threat_findings(model_id);
CREATE INDEX IF NOT EXISTS idx_tf_stride ON threat_findings(stride_category);
CREATE INDEX IF NOT EXISTS idx_tf_risk ON threat_findings(risk_level);

-- ============================================================
-- INNOVATION: DEVELOPER SCORECARDS (D-INV-29)
-- ============================================================
CREATE TABLE IF NOT EXISTS developer_scorecards (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    actor TEXT,
    overall_score REAL NOT NULL,
    letter_grade TEXT NOT NULL CHECK(letter_grade IN ('A','B','C','D','F')),
    code_quality_score REAL,
    security_score REAL,
    compliance_score REAL,
    test_coverage_score REAL,
    velocity_score REAL,
    dimension_details TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sc_project ON developer_scorecards(project_id);
CREATE INDEX IF NOT EXISTS idx_sc_actor ON developer_scorecards(actor);
CREATE INDEX IF NOT EXISTS idx_sc_created ON developer_scorecards(created_at);

-- ============================================================
-- INNOVATION: SCAFFOLD RUNS (D-INV-33)
-- ============================================================
CREATE TABLE IF NOT EXISTS scaffold_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    template_name TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    parameters TEXT,
    files_created INTEGER,
    compliance_bootstrapped INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','in_progress','completed','failed')),
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scaffold_project ON scaffold_runs(project_id);

-- ============================================================
-- INNOVATION: FORGE HUB RATINGS & TRUST (D-INV-37)
-- ============================================================
CREATE TABLE IF NOT EXISTS forge_hub_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connector_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    review_text TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT,
    UNIQUE(connector_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_fhr_connector ON forge_hub_ratings(connector_id);

CREATE TABLE IF NOT EXISTS forge_hub_trust_scores (
    id TEXT PRIMARY KEY,
    connector_id TEXT NOT NULL,
    trust_score REAL NOT NULL,
    validation_pass_rate REAL,
    download_count INTEGER,
    avg_rating REAL,
    age_days INTEGER,
    score_breakdown TEXT,
    breakdown TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    computed_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fht_connector ON forge_hub_trust_scores(connector_id);

-- ============================================================
-- INNOVATION: ATO SIMULATION RUNS (D-INV-41)
-- ============================================================
CREATE TABLE IF NOT EXISTS ato_simulation_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    scenario_id TEXT,
    iterations INTEGER NOT NULL,
    stig_findings_count INTEGER,
    poam_open_count INTEGER,
    evidence_stale_count INTEGER,
    result_json TEXT NOT NULL,
    p50_days REAL,
    p80_days REAL,
    p90_days REAL,
    p95_days REAL,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ato_sim_project ON ato_simulation_runs(project_id);

-- ============================================================
-- INNOVATION: FIRMWARE SBOM & VEX (D-INV-45)
-- ============================================================
CREATE TABLE IF NOT EXISTS firmware_sbom_records (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    board TEXT,
    cmake_path TEXT,
    components TEXT NOT NULL,
    component_count INTEGER,
    rtos_detected TEXT,
    sbom_json TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fw_sbom_project ON firmware_sbom_records(project_id);

CREATE TABLE IF NOT EXISTS firmware_vex_records (
    id TEXT PRIMARY KEY,
    sbom_id TEXT NOT NULL REFERENCES firmware_sbom_records(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    vex_format TEXT NOT NULL DEFAULT 'csaf',
    vulnerabilities TEXT NOT NULL,
    vulnerability_count INTEGER,
    vex_json TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fw_vex_project ON firmware_vex_records(project_id);
CREATE INDEX IF NOT EXISTS idx_fw_vex_sbom ON firmware_vex_records(sbom_id);

-- ============================================================
-- GENESIS v2.0 — AUTONOMOUS RESEARCH LAB (D-GEN-6, D-GEN-10)
-- ============================================================
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
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_genesis_audit_type ON genesis_audit(event_type);
CREATE INDEX IF NOT EXISTS idx_genesis_audit_reflex ON genesis_audit(reflex_name);
CREATE INDEX IF NOT EXISTS idx_genesis_audit_created ON genesis_audit(created_at);
-- crx-gen-02: reflex health-trend queries filter by reflex + time window and by
-- event_type + time window; composite indexes keep the 7/30-day rollups cheap.
CREATE INDEX IF NOT EXISTS idx_genesis_audit_reflex_created ON genesis_audit(reflex_name, created_at);
CREATE INDEX IF NOT EXISTS idx_genesis_audit_type_created ON genesis_audit(event_type, created_at);

CREATE TABLE IF NOT EXISTS genesis_reflex_state (
    reflex_name         TEXT PRIMARY KEY,
    enabled             INTEGER NOT NULL DEFAULT 1,
    last_run_at         TEXT,
    next_run_at         TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_open INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_tripped_at TEXT,
    total_runs          INTEGER NOT NULL DEFAULT 0,
    total_successes     INTEGER NOT NULL DEFAULT 0,
    total_failures      INTEGER NOT NULL DEFAULT 0,
    last_metric_value   REAL,
    last_error          TEXT,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS genesis_gkp (
    id              TEXT PRIMARY KEY,
    gkp_version     TEXT NOT NULL DEFAULT '1.0',
    artifact_type   TEXT NOT NULL,
    genesis_reflex  TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 0.0,
    evidence        TEXT,
    payload         TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    promotion_status TEXT NOT NULL DEFAULT 'pending_review'
        CHECK(promotion_status IN ('pending_review','auto_promoted','promoted','rejected','dedup_skipped')),
    promoted_at     TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_genesis_gkp_type ON genesis_gkp(artifact_type);
CREATE INDEX IF NOT EXISTS idx_genesis_gkp_status ON genesis_gkp(promotion_status);
CREATE INDEX IF NOT EXISTS idx_genesis_gkp_reflex ON genesis_gkp(genesis_reflex);
CREATE INDEX IF NOT EXISTS idx_genesis_gkp_created ON genesis_gkp(created_at);

-- Notification Gateway delivery log (Phase 72 — append-only, NIST AU)
CREATE TABLE IF NOT EXISTS notification_log (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    adapter         TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'info'
        CHECK(severity IN ('info','warning','error','critical')),
    title           TEXT,
    type            TEXT,
    body            TEXT,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    delivered       BOOLEAN NOT NULL DEFAULT FALSE,
    error           TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notification_log_event ON notification_log(event_type);
CREATE INDEX IF NOT EXISTS idx_notification_log_created ON notification_log(created_at);

-- PII Redaction audit trail (Phase 72, D-RDT-2 — append-only, NIST AU)
-- Superset schema: router.py uses function/action/entity_types_json,
-- anonymizer.py uses timestamp/text_length/entity_types/module
CREATE TABLE IF NOT EXISTS redaction_audit (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    function        TEXT DEFAULT '',
    detection_count INTEGER NOT NULL DEFAULT 0,
    entity_types_json TEXT,
    entity_types    TEXT,
    impact_level    TEXT NOT NULL DEFAULT 'IL4',
    action          TEXT DEFAULT 'redacted'
        CHECK(action IN ('redacted','skipped','error','')),
    timestamp       TEXT,
    text_length     INTEGER,
    module          TEXT DEFAULT 'unknown',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_redaction_audit_session ON redaction_audit(session_id);
CREATE INDEX IF NOT EXISTS idx_redaction_audit_created ON redaction_audit(created_at);

-- Genesis Synthesize Reflex — detected tool-chain patterns (Phase 72, Hermes adaptation)
CREATE TABLE IF NOT EXISTS genesis_tool_patterns (
    id              TEXT PRIMARY KEY,
    chain_hash      TEXT NOT NULL UNIQUE,
    tool_chain      TEXT NOT NULL,
    frequency       INTEGER NOT NULL DEFAULT 0,
    caller_diversity INTEGER NOT NULL DEFAULT 0,
    chain_length    INTEGER NOT NULL DEFAULT 0,
    composite_score REAL NOT NULL DEFAULT 0.0,
    sessions        TEXT,
    status          TEXT NOT NULL DEFAULT 'detected'
        CHECK(status IN ('detected','goal_generated','dismissed','archived')),
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_genesis_tool_patterns_status ON genesis_tool_patterns(status);
CREATE INDEX IF NOT EXISTS idx_genesis_tool_patterns_score ON genesis_tool_patterns(composite_score);

-- Goal Learner — Self-Improving Goals from Experience (D-GEN-GL-1)
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
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gg_status ON genesis_generated_goals(status);
CREATE INDEX IF NOT EXISTS idx_gg_domain ON genesis_generated_goals(domain_label);
CREATE INDEX IF NOT EXISTS idx_gg_created ON genesis_generated_goals(created_at);

-- ============================================================
-- PROPOSAL GENESIS — AUTONOMOUS PROPOSAL INTELLIGENCE (D-PG-1 through D-PG-10)
-- ============================================================

-- Audit trail for all autonomous proposal decisions (append-only, NIST AU)
CREATE TABLE IF NOT EXISTS pg_proposal_genesis_audit (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    reflex_name     TEXT,
    risk_tier       TEXT,
    opportunity_id  TEXT,
    details         TEXT,
    success         INTEGER,
    duration_ms     INTEGER,
    metric_name     TEXT,
    metric_value    REAL,
    created_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_audit_type ON pg_proposal_genesis_audit(event_type);
CREATE INDEX IF NOT EXISTS idx_pg_audit_reflex ON pg_proposal_genesis_audit(reflex_name);
CREATE INDEX IF NOT EXISTS idx_pg_audit_opp ON pg_proposal_genesis_audit(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_pg_audit_created ON pg_proposal_genesis_audit(created_at);

-- Reflex state tracking (mirrors genesis_reflex_state pattern)
CREATE TABLE IF NOT EXISTS pg_proposal_genesis_state (
    reflex_name         TEXT PRIMARY KEY,
    enabled             INTEGER NOT NULL DEFAULT 1,
    last_run_at         TEXT,
    next_run_at         TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_open INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_tripped_at TEXT,
    total_runs          INTEGER NOT NULL DEFAULT 0,
    total_successes     INTEGER NOT NULL DEFAULT 0,
    total_failures      INTEGER NOT NULL DEFAULT 0,
    last_metric_value   REAL,
    last_error          TEXT,
    updated_at          TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);

-- Daemon configuration overrides (D-PG-3: toggle)
CREATE TABLE IF NOT EXISTS pg_proposal_genesis_config (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);

-- Amendment tracking diffs (R1 Discover → R5 Extract)
CREATE TABLE IF NOT EXISTS pg_amendment_diffs (
    id              TEXT PRIMARY KEY,
    opportunity_id  TEXT NOT NULL,
    amendment_id    TEXT,
    diff_type       TEXT NOT NULL CHECK(diff_type IN ('added', 'removed', 'modified')),
    section         TEXT,
    old_text        TEXT,
    new_text        TEXT,
    re_extracted    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_amend_opp ON pg_amendment_diffs(opportunity_id);

-- Pulse ↔ Proposal content links (D-PG-5: bidirectional)
CREATE TABLE IF NOT EXISTS pg_pulse_proposal_links (
    id              TEXT PRIMARY KEY,
    pulse_post_id   TEXT,
    opportunity_id  TEXT,
    section_id      TEXT,
    link_type       TEXT NOT NULL CHECK(link_type IN ('article_to_proposal', 'capability_to_article', 'cdrl_to_case_study')),
    relevance_score REAL DEFAULT 0.0,
    created_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_pulse_link_post ON pg_pulse_proposal_links(pulse_post_id);
CREATE INDEX IF NOT EXISTS idx_pg_pulse_link_opp ON pg_pulse_proposal_links(opportunity_id);

-- Proposal quality scores from WriteGuard (R8 Polish)
CREATE TABLE IF NOT EXISTS pg_proposal_quality_scores (
    id              TEXT PRIMARY KEY,
    opportunity_id  TEXT NOT NULL,
    section_id      TEXT,
    draft_id        TEXT,
    grammar_score   REAL,
    readability_score REAL,
    tone_score      REAL,
    plagiarism_score REAL,
    ai_detection_score REAL,
    composite_score REAL,
    overall_score   REAL NOT NULL DEFAULT 0.0,
    passed          INTEGER NOT NULL DEFAULT 0,
    findings        TEXT,
    check_details   TEXT,
    created_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_quality_opp ON pg_proposal_quality_scores(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_pg_quality_created ON pg_proposal_quality_scores(created_at);

-- Bid/no-bid decisions (R9 Decide — Phase B)
CREATE TABLE IF NOT EXISTS pg_bid_decisions (
    id              TEXT PRIMARY KEY,
    opportunity_id  TEXT NOT NULL,
    decision        TEXT NOT NULL CHECK(decision IN ('bid', 'no_bid', 'pending', 'deferred')),
    win_probability REAL,
    score_breakdown TEXT,
    rationale       TEXT,
    decided_by      TEXT DEFAULT 'autonomous',
    created_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_bid_opp ON pg_bid_decisions(opportunity_id);

-- Bid decision outcomes for calibration (R13 Analyze — Phase B)
CREATE TABLE IF NOT EXISTS pg_bid_decision_outcomes (
    id              TEXT PRIMARY KEY,
    bid_decision_id TEXT NOT NULL,
    outcome         TEXT NOT NULL CHECK(outcome IN ('won', 'lost', 'no_award', 'cancelled', 'withdrawn')),
    actual_award_date TEXT,
    award_amount    REAL,
    notes           TEXT,
    created_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);

-- Win/loss records (R13 Analyze — Phase B)
CREATE TABLE IF NOT EXISTS pg_win_loss_records (
    id              TEXT PRIMARY KEY,
    opportunity_id  TEXT NOT NULL,
    outcome         TEXT NOT NULL CHECK(outcome IN ('won', 'lost', 'no_award', 'cancelled')),
    competitor_name TEXT,
    competitor_strengths TEXT,
    our_strengths   TEXT,
    our_weaknesses  TEXT,
    lessons_learned TEXT,
    created_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_winloss_opp ON pg_win_loss_records(opportunity_id);

-- Win/loss lessons for feedback loop (R13 Analyze → R14 Train)
CREATE TABLE IF NOT EXISTS pg_win_loss_lessons (
    id              TEXT PRIMARY KEY,
    win_loss_id     TEXT NOT NULL,
    category        TEXT NOT NULL CHECK(category IN ('technical', 'management', 'pricing', 'past_performance', 'compliance', 'staffing', 'other')),
    lesson          TEXT NOT NULL,
    actionable      INTEGER NOT NULL DEFAULT 1,
    applied         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);

-- Win/loss analysis runs (win_loss_engine.py — Phase B)
CREATE TABLE IF NOT EXISTS win_loss_analysis_runs (
    id                  TEXT PRIMARY KEY,
    run_at              TEXT,
    outcomes_analyzed   INTEGER,
    patterns_found      INTEGER,
    top_win_features    TEXT,
    top_loss_features   TEXT,
    result_json         TEXT,
    classification      TEXT DEFAULT 'CUI // SP-CTI'
);

-- Win/loss feature impact scores per run (win_loss_engine.py — Phase B)
CREATE TABLE IF NOT EXISTS win_loss_feature_impacts (
    id                      TEXT PRIMARY KEY,
    run_id                  TEXT,
    feature_tag             TEXT,
    win_count               INTEGER,
    loss_count              INTEGER,
    win_rate                REAL,
    impact_score            REAL,
    innovation_signal_id    TEXT,
    analyzed_at             TEXT
);
CREATE INDEX IF NOT EXISTS idx_wl_feature_impacts_run ON win_loss_feature_impacts(run_id);

-- Voice-of-customer documents (transcript_ingestor.py) — insert-only.
-- Also created by migration 069_voc_signals; declared here because the SQLite
-- seed path builds from this file alone and never runs migrations, so without
-- these two the ingestor's INSERTs raise inside its best-effort except and the
-- engine reports "0 job statements" instead of an error.
CREATE TABLE IF NOT EXISTS voc_documents (
    id                  TEXT PRIMARY KEY,
    filename            TEXT NOT NULL,
    source_type         TEXT NOT NULL,
    ingested_at         TEXT NOT NULL,
    word_count          INTEGER,
    job_statement_count INTEGER,
    classification      TEXT DEFAULT 'CUI // SP-CTI'
);

-- Voice-of-customer job statements (voc_engine.py). Mutable by design, unlike
-- voc_documents: the ingestor INSERTs each statement, then
-- VOCEngine._cluster_and_signal() UPDATEs job_category, frequency, the three
-- score columns and creative_gap_id in place. Migration 069_voc_signals'
-- docstring describes both tables as write-once; that holds for voc_documents
-- only, so this table is deliberately absent from APPEND_ONLY_TABLES in
-- .claude/hooks/pre_tool_use.py — listing it would block the engine's own
-- scoring pass.
CREATE TABLE IF NOT EXISTS voc_job_statements (
    id                  TEXT PRIMARY KEY,
    document_id         TEXT NOT NULL,
    raw_text            TEXT NOT NULL,
    job_category        TEXT,
    frequency           INTEGER,
    severity_score      REAL,
    strategic_fit_score REAL,
    composite_score     REAL,
    creative_gap_id     TEXT,
    analyzed_at         TEXT NOT NULL,
    classification      TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_voc_score ON voc_job_statements(composite_score);
CREATE INDEX IF NOT EXISTS idx_voc_document_id ON voc_job_statements(document_id);
CREATE INDEX IF NOT EXISTS idx_voc_category ON voc_job_statements(job_category);

-- CRM accounts (R4 Engage — Phase C)
CREATE TABLE IF NOT EXISTS pg_crm_accounts (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    agency          TEXT,
    sub_agency      TEXT,
    account_type    TEXT DEFAULT 'government' CHECK(account_type IN ('government', 'prime', 'subcontractor', 'partner', 'other')),
    website         TEXT,
    naics_codes     TEXT,
    set_asides      TEXT,
    notes           TEXT,
    status          TEXT DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'prospect')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_crm_acct_name ON pg_crm_accounts(name);

-- CRM contacts (R4 Engage — Phase C)
CREATE TABLE IF NOT EXISTS pg_crm_contacts (
    id              TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL,
    name            TEXT NOT NULL,
    title           TEXT,
    email           TEXT,
    phone           TEXT,
    role_in_procurement TEXT,
    influence_level TEXT CHECK(influence_level IN ('decision_maker', 'influencer', 'evaluator', 'end_user', 'unknown')),
    notes           TEXT,
    last_contact_at TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_crm_contact_acct ON pg_crm_contacts(account_id);

-- CRM interactions (R4 Engage — Phase C)
CREATE TABLE IF NOT EXISTS pg_crm_interactions (
    id              TEXT PRIMARY KEY,
    contact_id      TEXT NOT NULL,
    account_id      TEXT NOT NULL,
    interaction_type TEXT NOT NULL CHECK(interaction_type IN ('meeting', 'call', 'email', 'conference', 'site_visit', 'rfi_response', 'industry_day', 'other')),
    subject         TEXT,
    notes           TEXT,
    opportunity_id  TEXT,
    interaction_date TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_crm_interact_contact ON pg_crm_interactions(contact_id);
CREATE INDEX IF NOT EXISTS idx_pg_crm_interact_acct ON pg_crm_interactions(account_id);

-- CRM engagement scores (R4 Engage — Phase C)
CREATE TABLE IF NOT EXISTS pg_crm_engagement_scores (
    id              TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL,
    score           REAL NOT NULL DEFAULT 0.0,
    score_breakdown TEXT,
    interaction_count INTEGER DEFAULT 0,
    last_interaction_at TEXT,
    opportunity_count INTEGER DEFAULT 0,
    win_rate        REAL,
    created_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_crm_eng_acct ON pg_crm_engagement_scores(account_id);

-- Capture plans (R3 Shape — Phase D)
CREATE TABLE IF NOT EXISTS pg_capture_plans (
    id              TEXT PRIMARY KEY,
    opportunity_id  TEXT NOT NULL,
    status          TEXT DEFAULT 'draft' CHECK(status IN ('draft', 'active', 'completed', 'abandoned')),
    win_strategy    TEXT,
    discriminators  TEXT,
    teaming_strategy TEXT,
    price_strategy  TEXT,
    gate_reviews    TEXT,
    current_phase   TEXT DEFAULT 'qualify' CHECK(current_phase IN ('qualify','pursue','capture','bid','proposal')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_capture_opp ON pg_capture_plans(opportunity_id);

-- Capture activities (R3 Shape — Phase D)
CREATE TABLE IF NOT EXISTS pg_capture_activities (
    id              TEXT PRIMARY KEY,
    capture_plan_id TEXT NOT NULL,
    activity_type   TEXT NOT NULL,
    description     TEXT,
    assigned_to     TEXT,
    due_date        TEXT,
    status          TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'in_progress', 'completed', 'cancelled')),
    created_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_capture_act_plan ON pg_capture_activities(capture_plan_id);

-- Phase-gate audit trail (append-only — NIST AU, prop-cap-11)
CREATE TABLE IF NOT EXISTS pg_capture_gate_decisions (
    id TEXT PRIMARY KEY,
    capture_plan_id TEXT NOT NULL,
    opportunity_id TEXT,
    from_phase TEXT NOT NULL CHECK(from_phase IN ('qualify','pursue','capture','bid','proposal')),
    to_phase TEXT NOT NULL CHECK(to_phase IN ('pursue','capture','bid','proposal','no_bid')),
    decision TEXT NOT NULL CHECK(decision IN ('advance','hold','no_bid','return')),
    rationale TEXT,
    decided_by TEXT,
    gate_criteria_met TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_cap_gates_plan ON pg_capture_gate_decisions(capture_plan_id);
CREATE INDEX IF NOT EXISTS idx_pg_cap_gates_created ON pg_capture_gate_decisions(created_at);

-- Teaming partners (R3 Shape — Phase D, D-PG-6)
CREATE TABLE IF NOT EXISTS pg_teaming_partners (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    partner_type    TEXT NOT NULL CHECK(partner_type IN ('prime', 'subcontractor', 'consultant', 'technology_partner', 'mentor_protege')),
    capabilities    TEXT,
    past_performance TEXT,
    contract_vehicles TEXT,
    certifications  TEXT,
    set_asides      TEXT,
    status          TEXT DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'prospect')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_team_name ON pg_teaming_partners(name);

-- Teaming assessments (R3 Shape — Phase D)
CREATE TABLE IF NOT EXISTS pg_teaming_assessments (
    id              TEXT PRIMARY KEY,
    opportunity_id  TEXT NOT NULL,
    partner_id      TEXT NOT NULL,
    fit_score       REAL DEFAULT 0.0,
    capability_gaps_filled TEXT,
    risk_assessment TEXT,
    recommendation  TEXT CHECK(recommendation IN ('strong_fit', 'good_fit', 'marginal', 'not_recommended')),
    created_at      TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_team_assess_opp ON pg_teaming_assessments(opportunity_id);

-- ================================================================
-- Proposal Genesis Enhancement (3-Engine Research, §3.11-§3.18)
-- ================================================================

-- §3.11 Compliance Matrix (R5 enhancement + R22 Trace)
CREATE TABLE IF NOT EXISTS pg_compliance_matrix (
    id                  TEXT PRIMARY KEY,
    opportunity_id      TEXT NOT NULL,
    requirement_id      TEXT NOT NULL,
    requirement_text    TEXT NOT NULL,
    source_section      TEXT NOT NULL CHECK(source_section IN ('L', 'M', 'C', 'attachment', 'amendment')),
    evaluation_factor   TEXT,
    evaluation_weight   REAL,
    assigned_volume     TEXT,
    assigned_section    TEXT,
    compliance_status   TEXT DEFAULT 'gap' CHECK(compliance_status IN ('addressed', 'partial', 'gap', 'na')),
    amendment_version   INTEGER DEFAULT 0,
    notes               TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    FOREIGN KEY (opportunity_id) REFERENCES proposal_opportunities(id)
);
CREATE INDEX IF NOT EXISTS idx_pg_cmatrix_opp ON pg_compliance_matrix(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_pg_cmatrix_status ON pg_compliance_matrix(compliance_status);

-- §3.12 AI Color Team Review Simulator (R15 Review)
CREATE TABLE IF NOT EXISTS pg_review_findings (
    id                  TEXT PRIMARY KEY,
    opportunity_id      TEXT NOT NULL,
    review_type         TEXT NOT NULL CHECK(review_type IN ('blue', 'pink', 'red', 'green', 'gold')),
    review_iteration    INTEGER DEFAULT 1,
    section_id          TEXT,
    severity            TEXT NOT NULL CHECK(severity IN ('critical', 'major', 'minor', 'observation')),
    category            TEXT NOT NULL CHECK(category IN ('compliance', 'persuasion', 'readability', 'formatting', 'pricing', 'strategy')),
    finding_text        TEXT NOT NULL,
    recommendation      TEXT,
    resolution_status   TEXT DEFAULT 'open' CHECK(resolution_status IN ('open', 'addressed', 'deferred', 'wontfix')),
    resolution_notes    TEXT,
    created_at          TEXT NOT NULL,
    resolved_at         TEXT,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    FOREIGN KEY (opportunity_id) REFERENCES proposal_opportunities(id)
);
CREATE INDEX IF NOT EXISTS idx_pg_review_opp ON pg_review_findings(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_pg_review_type ON pg_review_findings(review_type);

-- §3.13 Cost Volume Automation (R17 Price)
CREATE TABLE IF NOT EXISTS pg_cost_volumes (
    id                      TEXT PRIMARY KEY,
    opportunity_id          TEXT NOT NULL,
    contract_type           TEXT NOT NULL CHECK(contract_type IN ('ffp', 't_and_m', 'cpff', 'cpaf', 'idiq', 'hybrid')),
    pricing_strategy        TEXT CHECK(pricing_strategy IN ('lpta', 'best_value', 'tradeoff')),
    total_evaluated_price   REAL,
    direct_labor_cost       REAL,
    fringe_rate             REAL,
    overhead_rate           REAL,
    g_and_a_rate            REAL,
    fee_rate                REAL,
    subcontractor_cost      REAL,
    odc_cost                REAL,
    ptw_estimate_low        REAL,
    ptw_estimate_high       REAL,
    calc_benchmark_median   REAL,
    period_of_performance   TEXT,
    status                  TEXT DEFAULT 'draft' CHECK(status IN ('draft', 'reviewed', 'approved', 'submitted')),
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    FOREIGN KEY (opportunity_id) REFERENCES proposal_opportunities(id)
);
CREATE INDEX IF NOT EXISTS idx_pg_costvol_opp ON pg_cost_volumes(opportunity_id);

CREATE TABLE IF NOT EXISTS pg_lcat_allocations (
    id                  TEXT PRIMARY KEY,
    cost_volume_id      TEXT NOT NULL,
    task_description    TEXT NOT NULL,
    labor_category      TEXT NOT NULL,
    bls_soc_code        TEXT,
    fte_count           REAL NOT NULL,
    hourly_rate         REAL,
    annual_cost         REAL,
    basis_of_estimate   TEXT,
    created_at          TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    FOREIGN KEY (cost_volume_id) REFERENCES pg_cost_volumes(id)
);
CREATE INDEX IF NOT EXISTS idx_pg_lcat_vol ON pg_lcat_allocations(cost_volume_id);

-- §3.14 CMMC Supply Chain Validator (R18 Comply_CMMC)
CREATE TABLE IF NOT EXISTS pg_cmmc_supply_chain (
    id                      TEXT PRIMARY KEY,
    opportunity_id          TEXT NOT NULL,
    team_member_name        TEXT NOT NULL,
    team_member_cage        TEXT,
    role                    TEXT CHECK(role IN ('prime', 'sub_tier1', 'sub_tier2', 'consultant')),
    required_cmmc_level     INTEGER NOT NULL,
    actual_cmmc_level       INTEGER,
    assessment_type         TEXT CHECK(assessment_type IN ('self', 'c3pao', 'dibcac')),
    sprs_score              INTEGER,
    poam_status             TEXT CHECK(poam_status IN ('none', 'open', 'closed')),
    certification_expiry    TEXT,
    compliance_status       TEXT DEFAULT 'unknown' CHECK(compliance_status IN ('compliant', 'poam', 'non_compliant', 'unknown', 'expired')),
    flow_down_generated     INTEGER DEFAULT 0,
    checked_at              TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    FOREIGN KEY (opportunity_id) REFERENCES proposal_opportunities(id)
);
CREATE INDEX IF NOT EXISTS idx_pg_cmmc_opp ON pg_cmmc_supply_chain(opportunity_id);

-- §3.15 GSA "American AI" Clause Compliance
CREATE TABLE IF NOT EXISTS pg_ai_clause_compliance (
    id                          TEXT PRIMARY KEY,
    opportunity_id              TEXT NOT NULL,
    clause_type                 TEXT NOT NULL CHECK(clause_type IN ('gsar_552_239_7001', 'omb_m26_04', 'omb_m25_21')),
    model_cards_generated       INTEGER DEFAULT 0,
    bias_evaluations_generated  INTEGER DEFAULT 0,
    source_disclosure_generated INTEGER DEFAULT 0,
    system_card_generated       INTEGER DEFAULT 0,
    american_ai_certified       INTEGER DEFAULT 0,
    ip_rights_documented        INTEGER DEFAULT 0,
    bundle_path                 TEXT,
    status                      TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'generated', 'reviewed', 'attached')),
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    FOREIGN KEY (opportunity_id) REFERENCES proposal_opportunities(id)
);
CREATE INDEX IF NOT EXISTS idx_pg_aiclause_opp ON pg_ai_clause_compliance(opportunity_id);

-- §3.15b IGCE Estimator — pre-bid cost estimates (task-plan-ddef9424ab46)
-- System shall produce IGCE estimates within 10% of vendor actuals,
-- validated against GSA Schedule pricing or market data.

-- GSA Schedule reference rates (hourly unit prices by labor category + SIN)
-- Used as one of two benchmarks when generating IGCE estimates.
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

-- Market data benchmarks — secondary source when GSA rates unavailable
-- (e.g., from FPDS-NG, BLS OEWS, commercial surveys).
CREATE TABLE IF NOT EXISTS gsa_market_rates (
    id              TEXT PRIMARY KEY,
    labor_category  TEXT NOT NULL,
    bls_soc_code    TEXT,
    source          TEXT NOT NULL,                 -- e.g., "fpds_ng", "bls_oews", "vendor_award"
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

-- IGCE estimate header — one row per generated IGCE
CREATE TABLE IF NOT EXISTS igce_estimates (
    id                      TEXT PRIMARY KEY,
    procurement_id          TEXT,
    opportunity_id          TEXT,
    solicitation            TEXT NOT NULL DEFAULT '',
    agency                  TEXT NOT NULL DEFAULT '',
    title                   TEXT NOT NULL DEFAULT '',
    period_of_performance   TEXT,
    estimation_method       TEXT NOT NULL DEFAULT 'deterministic'
                            CHECK(estimation_method IN ('deterministic', 'historical_blend', 'market_only')),
    status                  TEXT NOT NULL DEFAULT 'draft'
                            CHECK(status IN ('draft', 'reviewed', 'submitted', 'archived')),
    total_estimated_cost    REAL NOT NULL DEFAULT 0.0,
    total_low_estimate      REAL NOT NULL DEFAULT 0.0,
    total_high_estimate     REAL NOT NULL DEFAULT 0.0,
    within_10pct_confidence REAL,                  -- 0.0-1.0
    benchmark_source        TEXT,                  -- "gsa_schedule", "market", "blended", "historical"
    benchmark_sample_size   INTEGER DEFAULT 0,
    notes                   TEXT,
    created_by              TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_igce_est_proc ON igce_estimates(procurement_id);
CREATE INDEX IF NOT EXISTS idx_igce_est_opp ON igce_estimates(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_igce_est_status ON igce_estimates(status);

-- IGCE line items — one row per CLIN in an estimate
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
    benchmark_source        TEXT,                  -- which table provided the rate
    benchmark_rate          REAL,                  -- actual benchmark rate pulled
    benchmark_year          INTEGER,
    benchmark_n             INTEGER DEFAULT 0,     -- sample size backing the benchmark
    confidence              REAL,                  -- 0.0-1.0 for hitting within 10% of vendor actuals
    rationale               TEXT,                  -- human-readable basis (GSA Schedule, market median, etc.)
    created_at              TEXT NOT NULL,
    FOREIGN KEY (igce_estimate_id) REFERENCES igce_estimates(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_igce_line_est ON igce_estimate_line_items(igce_estimate_id);

-- Calibration history — actuals vs estimates (for tracking 10% accuracy)
-- Populated by procurement_quote_compare when an IGCE estimate is linked to a
-- procurement and actuals come in. Used to refine future confidence scoring.
CREATE TABLE IF NOT EXISTS igce_calibration_log (
    id                  TEXT PRIMARY KEY,
    igce_estimate_id    TEXT NOT NULL,
    procurement_id      TEXT,
    clin                TEXT NOT NULL DEFAULT '',
    estimated_unit_cost REAL NOT NULL,
    actual_unit_cost    REAL NOT NULL,
    actual_vendor       TEXT,
    variance_pct        REAL NOT NULL,             -- (actual - estimate) / estimate * 100
    within_10pct        INTEGER NOT NULL,         -- 1 if |variance_pct| <= 10, else 0
    benchmark_source    TEXT,
    confidence_predicted REAL,
    captured_at         TEXT NOT NULL,
    FOREIGN KEY (igce_estimate_id) REFERENCES igce_estimates(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_igce_cal_est ON igce_calibration_log(igce_estimate_id);
CREATE INDEX IF NOT EXISTS idx_igce_cal_proc ON igce_calibration_log(procurement_id);

-- §3.16 Talent Intelligence (R20 Talent)
CREATE TABLE IF NOT EXISTS pg_talent_signals (
    id                          TEXT PRIMARY KEY,
    competitor_name             TEXT NOT NULL,
    role_title                  TEXT NOT NULL,
    location                    TEXT,
    clearance_required          TEXT,
    salary_low                  REAL,
    salary_high                 REAL,
    tools_mentioned             TEXT,
    certifications_mentioned    TEXT,
    source_url                  TEXT,
    posting_date                TEXT,
    scan_date                   TEXT NOT NULL,
    correlated_opportunity_id   TEXT,
    signal_type                 TEXT CHECK(signal_type IN ('velocity_spike', 'role_cluster', 'new_capability', 'pricing_intel', 'general')),
    created_at                  TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_talent_comp ON pg_talent_signals(competitor_name);
CREATE INDEX IF NOT EXISTS idx_pg_talent_date ON pg_talent_signals(scan_date);

CREATE TABLE IF NOT EXISTS pg_talent_velocity (
    id                      TEXT PRIMARY KEY,
    competitor_name         TEXT NOT NULL,
    week_start              TEXT NOT NULL,
    posting_count           INTEGER NOT NULL,
    velocity_zscore         REAL,
    dominant_role_category  TEXT,
    dominant_location       TEXT,
    dominant_clearance      TEXT,
    created_at              TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_pg_talvel_comp ON pg_talent_velocity(competitor_name);

-- §3.17 Win Theme & Discriminator Registry
CREATE TABLE IF NOT EXISTS pg_win_themes (
    id                  TEXT PRIMARY KEY,
    opportunity_id      TEXT NOT NULL,
    theme_type          TEXT NOT NULL CHECK(theme_type IN ('win_theme', 'discriminator', 'ghost_strategy')),
    theme_statement     TEXT NOT NULL,
    supporting_evidence TEXT,
    target_eval_factor  TEXT,
    ghost_competitor    TEXT,
    priority            INTEGER DEFAULT 1,
    status              TEXT DEFAULT 'active' CHECK(status IN ('active', 'archived', 'superseded')),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    FOREIGN KEY (opportunity_id) REFERENCES proposal_opportunities(id)
);
CREATE INDEX IF NOT EXISTS idx_pg_wintheme_opp ON pg_win_themes(opportunity_id);

-- prem-pstaff-01: the bid side's person -> LCAT registry. pg_lcat_allocations is
-- task->LCAT->FTE and never names a human; pma_personnel is post-award (contract_id).
-- Before this, the Key Personnel volume was built by regex-scraping capitalised
-- bigrams out of proposal prose (program_bridge._gather_key_personnel).
--
-- The CHECKs here MUST match tools/govcon/key_personnel.py's QUALIFICATION_VERDICTS
-- and PERSON_SOURCES. They are restated rather than derived because this file is the
-- literal SQLite bootstrap; tests/test_key_personnel.py asserts they agree, so a
-- change to the Python constants that is not mirrored here fails the suite.
--
-- The evidence CHECK is the refuse-the-unevidenced rule in the schema: an unevidenced
-- person->LCAT mapping reaches the customer as an assertion nobody can defend.
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
CREATE INDEX IF NOT EXISTS idx_pkp_opportunity ON proposal_key_personnel(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_pkp_verdict ON proposal_key_personnel(qualification_verdict);

CREATE TABLE IF NOT EXISTS pg_theme_tracking (
    id                      TEXT PRIMARY KEY,
    theme_id                TEXT NOT NULL,
    section_id              TEXT NOT NULL,
    implementation_status   TEXT DEFAULT 'pending' CHECK(implementation_status IN ('pending', 'implemented', 'partial', 'missing')),
    density_score           REAL,
    reviewer_notes          TEXT,
    checked_at              TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    FOREIGN KEY (theme_id) REFERENCES pg_win_themes(id)
);
CREATE INDEX IF NOT EXISTS idx_pg_themetrack_theme ON pg_theme_tracking(theme_id);

-- §3.18 Teaming Coordination Hub enhancements (extends pg_teaming_partners)
CREATE TABLE IF NOT EXISTS pg_teaming_workshare (
    id                  TEXT PRIMARY KEY,
    opportunity_id      TEXT NOT NULL,
    partner_id          TEXT NOT NULL,
    clin_number         TEXT,
    workshare_pct       REAL NOT NULL,
    labor_categories    TEXT,
    status              TEXT DEFAULT 'proposed' CHECK(status IN ('proposed', 'agreed', 'contracted', 'disputed')),
    ta_status           TEXT DEFAULT 'none' CHECK(ta_status IN ('none', 'draft', 'negotiating', 'executed', 'expired')),
    ta_expiry_date      TEXT,
    oci_risk            TEXT DEFAULT 'none' CHECK(oci_risk IN ('none', 'low', 'medium', 'high', 'disqualifying')),
    socioeconomic_status TEXT,
    reliability_score   REAL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    FOREIGN KEY (opportunity_id) REFERENCES proposal_opportunities(id)
);
CREATE INDEX IF NOT EXISTS idx_pg_workshare_opp ON pg_teaming_workshare(opportunity_id);

-- ================================================================
-- File Sync Module (D-SYNC-1 through D-SYNC-10)
-- ================================================================

-- Sync jobs — mutable (status updates allowed)
CREATE TABLE IF NOT EXISTS sync_jobs (
    id                          TEXT PRIMARY KEY,
    name                        TEXT NOT NULL,
    source_path                 TEXT NOT NULL,
    source_provider             TEXT NOT NULL DEFAULT 'local'
        CHECK(source_provider IN ('local', 'sftp', 's3', 'azure', 'gcs')),
    dest_path                   TEXT NOT NULL,
    dest_provider               TEXT NOT NULL DEFAULT 'local'
        CHECK(dest_provider IN ('local', 'sftp', 's3', 'azure', 'gcs')),
    sync_mode                   TEXT NOT NULL DEFAULT 'push'
        CHECK(sync_mode IN ('push', 'pull', 'bidirectional')),
    conflict_strategy           TEXT NOT NULL DEFAULT 'last_write_wins'
        CHECK(conflict_strategy IN ('last_write_wins', 'rename_both', 'newest_wins', 'source_wins', 'skip')),
    ignore_file                 TEXT DEFAULT '.syncignore',
    delete_orphans              INTEGER DEFAULT 0,
    status                      TEXT NOT NULL DEFAULT 'idle'
        CHECK(status IN ('idle', 'scanning', 'syncing', 'completed', 'failed', 'paused', 'watching')),
    schedule_interval_seconds   INTEGER DEFAULT 0,
    bandwidth_limit_kbps        INTEGER DEFAULT 0,
    max_workers                 INTEGER DEFAULT 4,
    last_run_at                 TIMESTAMP,
    last_success_at             TIMESTAMP,
    files_synced                INTEGER DEFAULT 0,
    files_skipped               INTEGER DEFAULT 0,
    files_conflicted            INTEGER DEFAULT 0,
    bytes_transferred           INTEGER DEFAULT 0,
    error_message               TEXT,
    config_json                 TEXT,
    classification              TEXT DEFAULT 'CUI',
    project_id                  TEXT,
    created_by                  TEXT,
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sync_jobs_status ON sync_jobs(status);
CREATE INDEX IF NOT EXISTS idx_sync_jobs_project ON sync_jobs(project_id);

-- Sync state — mutable (per-file hash cache for fast-skip, D-SYNC-2)
CREATE TABLE IF NOT EXISTS sync_state (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL REFERENCES sync_jobs(id),
    relative_path   TEXT NOT NULL,
    content_hash    TEXT,
    file_size       INTEGER,
    mtime_epoch     REAL,
    side            TEXT NOT NULL DEFAULT 'source'
        CHECK(side IN ('source', 'dest')),
    last_synced_at  TIMESTAMP,
    last_synced_hash TEXT,
    UNIQUE(job_id, relative_path, side)
);
CREATE INDEX IF NOT EXISTS idx_sync_state_job ON sync_state(job_id);

-- Sync log — APPEND-ONLY (NIST AU compliant, D6, D-SYNC-7)
CREATE TABLE IF NOT EXISTS sync_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              TEXT NOT NULL REFERENCES sync_jobs(id),
    action              TEXT NOT NULL CHECK(action IN (
        'copy', 'update', 'delete', 'rename', 'skip',
        'conflict_resolved', 'conflict_skipped', 'error',
        'scan_started', 'scan_completed',
        'sync_started', 'sync_completed', 'sync_failed'
    )),
    relative_path       TEXT,
    source_hash         TEXT,
    dest_hash           TEXT,
    bytes_transferred   INTEGER DEFAULT 0,
    duration_ms         INTEGER DEFAULT 0,
    resolution          TEXT,
    error_detail        TEXT,
    classification      TEXT DEFAULT 'CUI',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sync_log_job ON sync_log(job_id);
CREATE INDEX IF NOT EXISTS idx_sync_log_action ON sync_log(action);
CREATE INDEX IF NOT EXISTS idx_sync_log_created ON sync_log(created_at);

-- Sync conflicts — mutable (resolution status updates)
CREATE TABLE IF NOT EXISTS sync_conflicts (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES sync_jobs(id),
    relative_path   TEXT NOT NULL,
    source_hash     TEXT,
    source_mtime    REAL,
    source_size     INTEGER,
    dest_hash       TEXT,
    dest_mtime      REAL,
    dest_size       INTEGER,
    resolution      TEXT CHECK(resolution IN (
        'pending', 'source_wins', 'dest_wins', 'renamed', 'skipped', 'manual'
    )) DEFAULT 'pending',
    resolved_at     TIMESTAMP,
    resolved_by     TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sync_conflicts_job ON sync_conflicts(job_id);
CREATE INDEX IF NOT EXISTS idx_sync_conflicts_status ON sync_conflicts(resolution);

-- =========================================================================
-- Pulse AI Blog Engine
-- =========================================================================

-- Pulse demand signals (topics extracted from SAM.gov, community, etc.)
CREATE TABLE IF NOT EXISTS pulse_demand_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pain_point_text TEXT NOT NULL,
    domain_category TEXT,
    keywords        TEXT,
    source          TEXT DEFAULT 'manual',
    frequency       INTEGER DEFAULT 1,
    velocity        REAL DEFAULT 0.0,
    is_high_demand  INTEGER DEFAULT 0,
    article_generated INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pulse_demand_high ON pulse_demand_signals(is_high_demand);
CREATE INDEX IF NOT EXISTS idx_pulse_demand_created ON pulse_demand_signals(created_at);

-- Pulse blog posts (articles — draft/staged/published lifecycle)
CREATE TABLE IF NOT EXISTS pulse_posts (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    slug                TEXT,
    status              TEXT NOT NULL DEFAULT 'draft'
                        CHECK(status IN ('draft', 'staged', 'review', 'published', 'archived')),
    topic               TEXT,
    body_markdown       TEXT,
    hero_image_path     TEXT,
    hero_image_method   TEXT CHECK(hero_image_method IN ('sdxl_turbo', 'svg', 'manual', NULL)),
    hero_image_prompt   TEXT,
    readability_score   REAL DEFAULT 0.0,
    author_id           TEXT,
    demand_signal_id    INTEGER REFERENCES pulse_demand_signals(id),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pulse_posts_status ON pulse_posts(status);
CREATE INDEX IF NOT EXISTS idx_pulse_posts_slug ON pulse_posts(slug);
CREATE INDEX IF NOT EXISTS idx_pulse_posts_created ON pulse_posts(created_at);

-- Pulse research cache (web scraping results: DuckDuckGo, Reddit, SO, etc.)
CREATE TABLE IF NOT EXISTS pulse_research_cache (
    id                  TEXT PRIMARY KEY,
    query               TEXT NOT NULL,
    source              TEXT NOT NULL,
    url                 TEXT,
    title               TEXT,
    snippet             TEXT,
    full_text           TEXT,
    relevance_score     REAL,
    sentiment           TEXT,
    pain_point_category TEXT,
    fetched_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pulse_research_query ON pulse_research_cache(query);

-- Pulse topic clusters (TF-IDF grouped research themes for article generation)
CREATE TABLE IF NOT EXISTS pulse_topic_clusters (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    pain_points     TEXT,
    research_ids    TEXT,
    priority_score  REAL DEFAULT 0.5,
    used_count      INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pulse_topic_clusters_priority ON pulse_topic_clusters(priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_pulse_topic_clusters_used ON pulse_topic_clusters(used_count);

-- =========================================================================
-- Phase 65 — Adaptive Intelligence (Red Team, Convergence, Stagnation, Benchmarks)
-- =========================================================================

-- Red Team Plugin Registry results (append-only, D6)
CREATE TABLE IF NOT EXISTS red_team_results (
    id              TEXT PRIMARY KEY,
    project_id      TEXT,
    plugin_id       TEXT NOT NULL,
    plugin_name     TEXT NOT NULL,
    category        TEXT NOT NULL,
    passed          INTEGER DEFAULT 0,
    tests_run       INTEGER DEFAULT 0,
    tests_passed    INTEGER DEFAULT 0,
    findings_json   TEXT,
    duration_ms     INTEGER DEFAULT 0,
    classification  TEXT DEFAULT 'CUI',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_red_team_plugin ON red_team_results(plugin_id);
CREATE INDEX IF NOT EXISTS idx_red_team_project ON red_team_results(project_id);

-- Genesis Convergence Log (append-only, D6)
CREATE TABLE IF NOT EXISTS genesis_convergence_log (
    id                      TEXT PRIMARY KEY,
    reflex_name             TEXT NOT NULL,
    generation              INTEGER DEFAULT 0,
    goal_drift              REAL DEFAULT 0.0,
    metric_drift            REAL DEFAULT 0.0,
    output_similarity       REAL DEFAULT 0.0,
    combined_drift          REAL DEFAULT 0.0,
    ambiguity_score         REAL DEFAULT 0.0,
    converged               INTEGER DEFAULT 0,
    retrospective_triggered INTEGER DEFAULT 0,
    details_json            TEXT,
    classification          TEXT DEFAULT 'CUI',
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_conv_reflex ON genesis_convergence_log(reflex_name);

-- Genesis Stagnation Log (append-only, D6)
CREATE TABLE IF NOT EXISTS genesis_stagnation_log (
    id                   TEXT PRIMARY KEY,
    reflex_name          TEXT NOT NULL,
    pattern_type         TEXT NOT NULL CHECK(pattern_type IN (
        'oscillation', 'stagnation', 'diminishing_returns', 'repetitive_output'
    )),
    persona_used         TEXT,
    alternatives_json    TEXT,
    selected_alternative TEXT,
    score                REAL DEFAULT 0.0,
    details_json         TEXT,
    classification       TEXT DEFAULT 'CUI',
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_stag_reflex ON genesis_stagnation_log(reflex_name);

-- Agent Benchmark Results (append-only, D6)
CREATE TABLE IF NOT EXISTS agent_benchmark_results (
    id                  TEXT PRIMARY KEY,
    scan_id             TEXT NOT NULL,
    project_id          TEXT,
    agent_type          TEXT NOT NULL,
    scenario_id         TEXT NOT NULL,
    scenario_name       TEXT NOT NULL,
    category            TEXT NOT NULL,
    outcome_passed      INTEGER DEFAULT 0,
    methodology_passed  INTEGER DEFAULT 0,
    composite_score     REAL DEFAULT 0.0,
    duration_ms         INTEGER DEFAULT 0,
    details_json        TEXT,
    classification      TEXT DEFAULT 'CUI',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bench_scan ON agent_benchmark_results(scan_id);
CREATE INDEX IF NOT EXISTS idx_bench_agent ON agent_benchmark_results(agent_type);

-- GSD-adapted: 4-Level Verification & Stub Detection Results (D-GSD-1/3)
CREATE TABLE IF NOT EXISTS stub_detection_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL,
    project_dir     TEXT,
    files_checked   INTEGER DEFAULT 0,
    files_passed    INTEGER DEFAULT 0,
    files_failed    INTEGER DEFAULT 0,
    stub_total      INTEGER DEFAULT 0,
    level_summary   TEXT,       -- JSON: per-level pass/fail counts
    failures        TEXT,       -- JSON: list of failure details
    overall_passed  INTEGER DEFAULT 1,
    classification  TEXT DEFAULT 'CUI',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_stub_project ON stub_detection_results(project_id);

-- GSD-adapted: Context Pressure & Stuck Detection Events (D-GSD-4/6)
CREATE TABLE IF NOT EXISTS context_pressure_events (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id               TEXT NOT NULL,
    event_type               TEXT NOT NULL CHECK(event_type IN (
        'pressure_check', 'stuck_analysis_paralysis',
        'stuck_duplicate_loop', 'stuck_retry_spiral', 'stuck_unknown'
    )),
    pressure_level           TEXT NOT NULL CHECK(pressure_level IN (
        'normal', 'warning', 'critical', 'stuck'
    )),
    estimated_tokens_used    INTEGER DEFAULT 0,
    estimated_remaining_pct  REAL DEFAULT 100.0,
    tool_call_count          INTEGER DEFAULT 0,
    recommendation           TEXT,
    classification           TEXT DEFAULT 'CUI',
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ctx_pressure_session ON context_pressure_events(session_id);

-- GSD-adapted: Category-Based Deviation Rule Events (D-GSD-7/9)
CREATE TABLE IF NOT EXISTS deviation_rule_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    category            TEXT NOT NULL,
    confidence          REAL NOT NULL,
    original_decision   TEXT NOT NULL CHECK(original_decision IN (
        'auto_heal', 'suggest', 'escalate'
    )),
    final_decision      TEXT NOT NULL CHECK(final_decision IN (
        'auto_heal', 'suggest', 'escalate'
    )),
    category_overrode   INTEGER DEFAULT 0,
    matched_keywords    TEXT,       -- JSON array of matched keywords
    reason              TEXT,
    classification      TEXT DEFAULT 'CUI',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_deviation_category ON deviation_rule_events(category);
CREATE INDEX IF NOT EXISTS idx_deviation_decision ON deviation_rule_events(final_decision);

--- ============================================================
--- NEMOCLAW-ADAPTED AGENT SANDBOXING (D-NC-1 through D-NC-3)
--- ============================================================

-- Credential broker audit log (D-NC-1 — append-only, NIST AU)
CREATE TABLE IF NOT EXISTS credential_broker_log (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    function        TEXT NOT NULL,
    provider        TEXT,
    scope           TEXT,
    action          TEXT NOT NULL CHECK(action IN ('grant', 'deny', 'revoke', 'expire', 'fallback')),
    token_hash      TEXT,
    reason          TEXT,
    ttl_seconds     INTEGER,
    expires_at      TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cbl_agent ON credential_broker_log(agent_id);
CREATE INDEX IF NOT EXISTS idx_cbl_function ON credential_broker_log(function);
CREATE INDEX IF NOT EXISTS idx_cbl_action ON credential_broker_log(action);
CREATE INDEX IF NOT EXISTS idx_cbl_created ON credential_broker_log(created_at);

-- Active credential tokens (D-NC-1 — allows UPDATE for revocation)
CREATE TABLE IF NOT EXISTS credential_active_tokens (
    token_hash      TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    function        TEXT NOT NULL,
    provider        TEXT,
    scope           TEXT,
    issued_at       TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    revoked         INTEGER NOT NULL DEFAULT 0,
    revoked_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_cat_agent ON credential_active_tokens(agent_id);
CREATE INDEX IF NOT EXISTS idx_cat_expires ON credential_active_tokens(expires_at);

-- Egress policy audit log (D-NC-2 — append-only, NIST AU)
CREATE TABLE IF NOT EXISTS egress_policy_audit (
    id              TEXT PRIMARY KEY,
    agent_role      TEXT NOT NULL,
    policy_hash     TEXT NOT NULL,
    action          TEXT NOT NULL CHECK(action IN ('resolve', 'generate', 'apply', 'override')),
    applied_by      TEXT,
    diff_summary    TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_epa_role ON egress_policy_audit(agent_role);
CREATE INDEX IF NOT EXISTS idx_epa_created ON egress_policy_audit(created_at);

-- Blueprint digest store (D-NC-3 — append-only for computed records)
CREATE TABLE IF NOT EXISTS blueprint_digests (
    id              TEXT PRIMARY KEY,
    entity_type     TEXT NOT NULL CHECK(entity_type IN ('genome', 'marketplace_asset', 'child_app', 'capability', 'propagation')),
    entity_id       TEXT NOT NULL,
    digest          TEXT NOT NULL,
    file_count      INTEGER NOT NULL,
    total_bytes     INTEGER NOT NULL,
    directory_path  TEXT,
    computed_at     TEXT NOT NULL,
    verified_at     TEXT,
    verified_by     TEXT,
    verification_result TEXT CHECK(verification_result IN ('pass', 'fail', NULL))
);
CREATE INDEX IF NOT EXISTS idx_bd_entity ON blueprint_digests(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_bd_digest ON blueprint_digests(digest);
CREATE INDEX IF NOT EXISTS idx_bd_computed ON blueprint_digests(computed_at);

-- Post-propagation verifications (D-NC-5 — append-only, NIST AU)
CREATE TABLE IF NOT EXISTS propagation_verifications (
    id              TEXT PRIMARY KEY,
    propagation_id  TEXT NOT NULL,
    child_id        TEXT,
    check_name      TEXT NOT NULL,
    check_status    TEXT NOT NULL CHECK(check_status IN ('pass', 'fail', 'skip', 'error')),
    detail          TEXT,
    verified_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pv_prop ON propagation_verifications(propagation_id);
CREATE INDEX IF NOT EXISTS idx_pv_child ON propagation_verifications(child_id);

--- ============================================================
--- EVOLUTION DAEMON — Phase 36 Autonomous Lifecycle (D-EVO-1)
--- ============================================================

-- Append-only audit trail for evolution daemon decisions (NIST AU)
CREATE TABLE IF NOT EXISTS evolution_audit (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    reflex_name     TEXT,
    risk_tier       TEXT,
    details         TEXT,
    success         INTEGER,
    duration_ms     INTEGER,
    metric_name     TEXT,
    metric_value    REAL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evolution_audit_type ON evolution_audit(event_type);
CREATE INDEX IF NOT EXISTS idx_evolution_audit_reflex ON evolution_audit(reflex_name);
CREATE INDEX IF NOT EXISTS idx_evolution_audit_created ON evolution_audit(created_at);

-- Per-reflex state tracking for evolution daemon
CREATE TABLE IF NOT EXISTS evolution_reflex_state (
    reflex_name             TEXT PRIMARY KEY,
    enabled                 INTEGER NOT NULL DEFAULT 1,
    last_run_at             TEXT,
    next_run_at             TEXT,
    consecutive_failures    INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_open    INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_tripped_at TEXT,
    total_runs              INTEGER NOT NULL DEFAULT 0,
    total_successes         INTEGER NOT NULL DEFAULT 0,
    total_failures          INTEGER NOT NULL DEFAULT 0,
    last_metric_value       REAL,
    last_error              TEXT,
    updated_at              TEXT NOT NULL
);

--- ============================================================
--- OUTCOME VERIFIER — Self-Healing Feedback Loop (D-EVO-6)
--- ============================================================

-- Append-only verification log for auto-resolution outcomes (NIST AU)
CREATE TABLE IF NOT EXISTS outcome_verification_log (
    id TEXT PRIMARY KEY,
    resolution_id TEXT NOT NULL,
    pr_url TEXT,
    pattern_signature TEXT,
    verification_type TEXT NOT NULL
        CHECK(verification_type IN ('pr_merge_check', 'recurrence_check')),
    result TEXT NOT NULL DEFAULT 'pending'
        CHECK(result IN ('pending', 'merged', 'closed', 'recurred',
                         'resolved', 'timeout', 'cli_unavailable')),
    checked_at TEXT,
    confidence_delta REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ov_resolution ON outcome_verification_log(resolution_id);
CREATE INDEX IF NOT EXISTS idx_ov_result ON outcome_verification_log(result);

--- ============================================================
--- PER-PROJECT PATTERN LEARNING (D-EVO-8)
--- ============================================================

-- Track self-healing pattern effectiveness per project
CREATE TABLE IF NOT EXISTS self_heal_project_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_signature TEXT NOT NULL,
    project_id TEXT NOT NULL,
    attempts INTEGER DEFAULT 0,
    successes INTEGER DEFAULT 0,
    failures INTEGER DEFAULT 0,
    effectiveness REAL DEFAULT 0.0,
    last_attempt_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(pattern_signature, project_id)
);
CREATE INDEX IF NOT EXISTS idx_shpp_project ON self_heal_project_patterns(project_id);
CREATE INDEX IF NOT EXISTS idx_shpp_signature ON self_heal_project_patterns(pattern_signature);

-- ============================================================
-- BAYESIAN TEACHING (D-BT-1 through D-BT-6)
-- Adapts Bayesian Teaching (Shafto 2014, Zhu 2015, Qiu 2025)
-- and DeepFlow SmartEncoding (ACM SIGCOMM 2023)
-- ============================================================

-- Append-only scoring log (NIST AU-2 compliant)
CREATE TABLE IF NOT EXISTS bayesian_teaching_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    candidate_type TEXT NOT NULL CHECK(candidate_type IN (
        'training_pair', 'compliance_ordering', 'rag_chunk',
        'teaching_set', 'onboarding_item'
    )),
    info_gain_score REAL NOT NULL,
    dimensions TEXT,
    threshold_band TEXT CHECK(threshold_band IN ('auto_select', 'suggest', 'exclude')),
    context_id TEXT,
    project_id TEXT,
    agent_id TEXT DEFAULT 'intelligence-engine',
    classification TEXT DEFAULT 'CUI',
    scored_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bts_candidate ON bayesian_teaching_scores(candidate_id);
CREATE INDEX IF NOT EXISTS idx_bts_type ON bayesian_teaching_scores(candidate_type);
CREATE INDEX IF NOT EXISTS idx_bts_context ON bayesian_teaching_scores(context_id);

-- SmartEncoding dictionary (DeepFlow-inspired tag compression, D-BT-5)
CREATE TABLE IF NOT EXISTS smart_encoding_dictionary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_value TEXT NOT NULL UNIQUE,
    encoded_id INTEGER NOT NULL UNIQUE,
    category TEXT CHECK(category IN (
        'agent', 'status', 'operation', 'framework', 'classification', 'custom'
    )),
    usage_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================
-- WORKFLOW DISCIPLINE ENGINE (Phase 66, D-WF-1 through D-WF-7)
-- Adapts PAUL (Plan-Apply-Unify Loop) into LLM-agnostic tools
-- ============================================================

-- Workflow loops — PLAN → APPLY → UNIFY lifecycle
CREATE TABLE IF NOT EXISTS workflow_loops (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    phase_name TEXT NOT NULL,
    loop_type TEXT DEFAULT 'build' CHECK(loop_type IN (
        'build', 'compliance', 'deploy', 'fix', 'research', 'custom'
    )),
    status TEXT DEFAULT 'planning' CHECK(status IN (
        'planning', 'planned', 'applying', 'applied',
        'unifying', 'closed', 'abandoned'
    )),
    plan_summary TEXT,
    boundaries TEXT,
    task_count INTEGER DEFAULT 0,
    tasks_completed INTEGER DEFAULT 0,
    acceptance_criteria_count INTEGER DEFAULT 0,
    acceptance_pass_count INTEGER DEFAULT 0,
    acceptance_fail_count INTEGER DEFAULT 0,
    acceptance_skip_count INTEGER DEFAULT 0,
    planned_at TEXT,
    apply_started_at TEXT,
    apply_completed_at TEXT,
    unify_started_at TEXT,
    closed_at TEXT,
    created_by TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wl_project ON workflow_loops(project_id);
CREATE INDEX IF NOT EXISTS idx_wl_status ON workflow_loops(status);

-- Acceptance criteria per loop (Given/When/Then)
-- Optional bdd_story_id links to safe_decomposition for traceability
CREATE TABLE IF NOT EXISTS workflow_acceptance_criteria (
    id TEXT PRIMARY KEY,
    loop_id TEXT NOT NULL,
    criterion_number INTEGER NOT NULL,
    given_text TEXT,
    when_text TEXT,
    then_text TEXT,
    bdd_story_id TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN (
        'pending', 'pass', 'fail', 'skip'
    )),
    evidence TEXT,
    verified_at TEXT,
    cot_config TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (loop_id) REFERENCES workflow_loops(id),
    FOREIGN KEY (bdd_story_id) REFERENCES safe_decomposition(id)
);
CREATE INDEX IF NOT EXISTS idx_wac_loop ON workflow_acceptance_criteria(loop_id);

-- Reconciliation records (UNIFY output — append-only, NIST AU)
CREATE TABLE IF NOT EXISTS workflow_reconciliations (
    id TEXT PRIMARY KEY,
    loop_id TEXT NOT NULL,
    planned_tasks INTEGER DEFAULT 0,
    completed_tasks INTEGER DEFAULT 0,
    deviations TEXT,
    lessons_learned TEXT,
    process_checks TEXT,
    required_processes_invoked INTEGER DEFAULT 0,
    required_processes_total INTEGER DEFAULT 0,
    overall_result TEXT CHECK(overall_result IN (
        'success', 'partial', 'failed'
    )),
    classification TEXT DEFAULT 'CUI',
    reconciled_at TEXT NOT NULL,
    FOREIGN KEY (loop_id) REFERENCES workflow_loops(id)
);
CREATE INDEX IF NOT EXISTS idx_wr_loop ON workflow_reconciliations(loop_id);

-- Session handoffs (structured context transfer)
-- chat_context_id links to the chat context where the conversation happened
CREATE TABLE IF NOT EXISTS workflow_handoffs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    loop_id TEXT,
    loop_status TEXT,
    chat_context_id TEXT,
    decisions_made TEXT,
    blockers TEXT,
    next_actions TEXT,
    context_summary TEXT,
    handoff_to TEXT,
    created_by TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wh_project ON workflow_handoffs(project_id);

-- ============================================================
-- EVENT-SOURCED WORKFLOW REPLAY (NIST AU extension, D-REPLAY-1 through D-REPLAY-7)
-- Deterministic ANVIL pipeline replay from immutable audit trail
-- ============================================================

-- Replay sessions — mutable status tracking (rows are updated on completion).
-- Durable event log lives in audit_trail (workflow_replay_* event types).
CREATE TABLE IF NOT EXISTS workflow_replay_sessions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    project_id TEXT,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN (
        'running', 'completed', 'failed'
    )),
    resume_step TEXT,
    completed_steps_snapshot TEXT DEFAULT '[]',
    total_steps INTEGER DEFAULT 0,
    skipped_steps INTEGER DEFAULT 0,
    replayed_steps INTEGER DEFAULT 0,
    error_message TEXT,
    triggered_by TEXT DEFAULT 'replay-engine',
    classification TEXT DEFAULT 'CUI',
    started_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_wrs_workflow ON workflow_replay_sessions(workflow_id);
CREATE INDEX IF NOT EXISTS idx_wrs_project ON workflow_replay_sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_wrs_status ON workflow_replay_sessions(status);

-- ============================================================
-- ENGINEERING REVIEW BOARD (Phase 67, D-RB-1 through D-RB-7)
-- Continuous multi-persona code analysis daemon
-- ============================================================

-- Audit trail (append-only, NIST AU — D-RB-2)
CREATE TABLE IF NOT EXISTS review_board_audit (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    reflex_name     TEXT,
    risk_tier       TEXT,
    details         TEXT,
    success         INTEGER,
    duration_ms     INTEGER,
    metric_name     TEXT,
    metric_value    REAL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rba_reflex ON review_board_audit(reflex_name);
CREATE INDEX IF NOT EXISTS idx_rba_created ON review_board_audit(created_at);

-- Reflex state (allows UPDATE for scheduling/circuit breaker)
CREATE TABLE IF NOT EXISTS review_board_reflex_state (
    reflex_name             TEXT PRIMARY KEY,
    enabled                 INTEGER NOT NULL DEFAULT 1,
    last_run_at             TEXT,
    next_run_at             TEXT,
    consecutive_failures    INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_open    INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_tripped_at TEXT,
    total_runs              INTEGER NOT NULL DEFAULT 0,
    total_successes         INTEGER NOT NULL DEFAULT 0,
    total_failures          INTEGER NOT NULL DEFAULT 0,
    last_metric_value       REAL,
    last_error              TEXT,
    updated_at              TEXT NOT NULL
);

-- Findings (append-only, NIST AU — D-RB-2)
CREATE TABLE IF NOT EXISTS review_board_findings (
    id              TEXT PRIMARY KEY,
    reflex_name     TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK(severity IN (
        'critical', 'high', 'medium', 'low', 'info'
    )),
    category        TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    recommendation  TEXT,
    evidence        TEXT,
    confidence      REAL DEFAULT 0.0,
    auto_fixable    INTEGER DEFAULT 0,
    fix_applied     INTEGER DEFAULT 0,
    sha256          TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rbf_reflex ON review_board_findings(reflex_name);
CREATE INDEX IF NOT EXISTS idx_rbf_severity ON review_board_findings(severity);
CREATE INDEX IF NOT EXISTS idx_rbf_created ON review_board_findings(created_at);

-- Remediation audit log (append-only, NIST AU — D-RB-10)
CREATE TABLE IF NOT EXISTS review_board_remediation_log (
    id              TEXT PRIMARY KEY,
    finding_id      TEXT NOT NULL,
    reflex_name     TEXT,
    category        TEXT NOT NULL,
    severity        TEXT,
    confidence      REAL DEFAULT 0.0,
    tier            TEXT NOT NULL CHECK(tier IN (
        'auto_fix', 'suggest', 'escalate'
    )),
    status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'fixing', 'fixed', 'failed',
        'suggested', 'escalated', 'skipped', 'verified'
    )),
    fix_description TEXT,
    fix_result      TEXT,
    verification    TEXT,
    dry_run         INTEGER DEFAULT 0,
    duration_ms     INTEGER,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rbrlog_finding ON review_board_remediation_log(finding_id);
CREATE INDEX IF NOT EXISTS idx_rbrlog_status ON review_board_remediation_log(status);

-- Health score history (append-only, D-RB-12)
CREATE TABLE IF NOT EXISTS review_board_health_history (
    id          TEXT PRIMARY KEY,
    score       REAL NOT NULL,
    grade       TEXT NOT NULL,
    trend       TEXT NOT NULL DEFAULT 'stable',
    breakdown   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rbhh_created ON review_board_health_history(created_at);

-- ============================================================
-- AUTONOMY ENGINE (Phase 68, D-AE-1 through D-AE-12)
-- Bayesian trust-graduated self-evolution
-- ============================================================

-- Trust state per action category — allows UPDATE (posterior evolves)
CREATE TABLE IF NOT EXISTS autonomy_trust_state (
    category            TEXT PRIMARY KEY,
    alpha               REAL NOT NULL DEFAULT 2.0,
    beta                REAL NOT NULL DEFAULT 8.0,
    ceiling             REAL NOT NULL DEFAULT 1.0,
    total_observations  INTEGER NOT NULL DEFAULT 0,
    total_successes     INTEGER NOT NULL DEFAULT 0,
    total_failures      INTEGER NOT NULL DEFAULT 0,
    current_tier        TEXT DEFAULT 'observer',
    last_updated        TEXT NOT NULL
);

-- Observations (append-only, NIST AU — D-AE-5)
CREATE TABLE IF NOT EXISTS autonomy_observations (
    id              TEXT PRIMARY KEY,
    category        TEXT NOT NULL,
    outcome         TEXT NOT NULL CHECK(outcome IN ('success', 'failure')),
    alpha_before    REAL,
    beta_before     REAL,
    alpha_after     REAL,
    beta_after      REAL,
    mean_before     REAL,
    mean_after      REAL,
    tier_before     TEXT,
    tier_after      TEXT,
    source          TEXT,
    details         TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ao_category ON autonomy_observations(category);
CREATE INDEX IF NOT EXISTS idx_ao_created ON autonomy_observations(created_at);

-- Actions (append-only, NIST AU — D-AE-10)
CREATE TABLE IF NOT EXISTS autonomy_actions (
    id              TEXT PRIMARY KEY,
    category        TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    decision        TEXT NOT NULL CHECK(decision IN (
        'approved', 'denied', 'explored', 'safety_blocked'
    )),
    trust_mean      REAL,
    thompson_sample REAL,
    tier_required   TEXT,
    tier_current    TEXT,
    details         TEXT,
    coherence_passed INTEGER,
    remediation_applied INTEGER,
    outcome         TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aa_category ON autonomy_actions(category);

-- Behavior log (append-only, NIST AU — D-AE-12)
CREATE TABLE IF NOT EXISTS autonomy_behavior_log (
    id              TEXT PRIMARY KEY,
    signal_type     TEXT NOT NULL,
    finding_id      TEXT,
    pr_url          TEXT,
    alpha_delta     REAL DEFAULT 0.0,
    beta_delta      REAL DEFAULT 0.0,
    category        TEXT,
    details         TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_abl_signal ON autonomy_behavior_log(signal_type);

-- ============================================================
-- Phase 67 — Bayesian Autoresearch (D-AR-1 through D-AR-10)
-- ============================================================

-- Experiment programs (reference data, allows UPDATE)
CREATE TABLE IF NOT EXISTS experiment_programs (
    id              TEXT PRIMARY KEY,
    domain          TEXT NOT NULL UNIQUE,
    objective_metric TEXT NOT NULL,
    objective_direction TEXT NOT NULL CHECK(objective_direction IN ('maximize', 'minimize')),
    measurement_command TEXT NOT NULL,
    metric_path     TEXT,
    modifiable_paths TEXT,
    forbidden_paths TEXT,
    time_budget_seconds INTEGER NOT NULL DEFAULT 300,
    keep_threshold  REAL NOT NULL DEFAULT 0.005,
    category_order  TEXT,
    categories      TEXT,
    config          TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Experiment candidates (allows UPDATE for status transitions)
CREATE TABLE IF NOT EXISTS experiment_candidates (
    id              TEXT PRIMARY KEY,
    domain          TEXT NOT NULL,
    hypothesis      TEXT NOT NULL,
    category        TEXT,
    modifications   TEXT,
    source          TEXT DEFAULT 'manual' CHECK(source IN (
        'manual', 'innovation', 'creative', 'research', 'genesis', 'llm_generated'
    )),
    signal_id       TEXT,
    status          TEXT NOT NULL DEFAULT 'created' CHECK(status IN (
        'created', 'scoring', 'selected', 'running', 'completed', 'discarded', 'failed', 'deduped'
    )),
    embedding       BLOB,
    content_hash    TEXT,
    info_gain_score REAL,
    thompson_sample REAL,
    estimated_impact TEXT CHECK(estimated_impact IN ('high', 'medium', 'low')),
    risk_level      TEXT CHECK(risk_level IN ('high', 'medium', 'low')),
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ec_domain ON experiment_candidates(domain);
CREATE INDEX IF NOT EXISTS idx_ec_status ON experiment_candidates(status);
CREATE INDEX IF NOT EXISTS idx_ec_hash ON experiment_candidates(content_hash);

-- Experiment results (append-only, NIST AU-2 — D-AR-4)
CREATE TABLE IF NOT EXISTS experiment_results (
    id              TEXT PRIMARY KEY,
    experiment_id   TEXT NOT NULL,
    domain          TEXT NOT NULL,
    hypothesis      TEXT NOT NULL,
    category        TEXT,
    pre_metric      REAL,
    post_metric     REAL,
    metric_delta    REAL,
    improvement_pct REAL,
    decision        TEXT NOT NULL CHECK(decision IN ('keep', 'discard', 'timeout', 'error')),
    decision_rationale TEXT,
    duration_ms     INTEGER,
    git_branch      TEXT,
    git_commit      TEXT,
    tests_passed    INTEGER,
    coherence_passed INTEGER,
    files_modified  TEXT,
    details         TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_er_domain ON experiment_results(domain);
CREATE INDEX IF NOT EXISTS idx_er_decision ON experiment_results(decision);
CREATE INDEX IF NOT EXISTS idx_er_created ON experiment_results(created_at);

-- Experiment landscapes (allows UPDATE — posteriors evolve)
CREATE TABLE IF NOT EXISTS experiment_landscapes (
    id              TEXT PRIMARY KEY,
    domain          TEXT NOT NULL,
    category        TEXT NOT NULL,
    alpha           REAL NOT NULL DEFAULT 2.0,
    beta_val        REAL NOT NULL DEFAULT 8.0,
    total_experiments INTEGER NOT NULL DEFAULT 0,
    total_kept      INTEGER NOT NULL DEFAULT 0,
    total_discarded INTEGER NOT NULL DEFAULT 0,
    best_improvement REAL DEFAULT 0.0,
    cumulative_improvement REAL DEFAULT 0.0,
    last_experiment_at TEXT,
    updated_at      TEXT NOT NULL,
    UNIQUE(domain, category)
);

-- Bayesian experiment scores (append-only, NIST AU-2 — D-AR-4)
CREATE TABLE IF NOT EXISTS bayesian_experiment_scores (
    id              TEXT PRIMARY KEY,
    candidate_id    TEXT NOT NULL,
    domain          TEXT NOT NULL,
    info_gain_score REAL NOT NULL,
    dimensions      TEXT,
    threshold_band  TEXT,
    thompson_sample REAL,
    prior_distribution TEXT,
    selected        INTEGER DEFAULT 0,
    classification  TEXT DEFAULT 'CUI',
    scored_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bes_domain ON bayesian_experiment_scores(domain);
CREATE INDEX IF NOT EXISTS idx_bes_candidate ON bayesian_experiment_scores(candidate_id);

-- ============================================================
-- REDACTION & DATA PROTECTION (Phase 70 — D-RDT-1)
-- ============================================================

-- Conversation-scoped real<->surrogate mapping (reversible anonymization)
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
CREATE INDEX IF NOT EXISTS idx_redaction_registry_session ON redaction_registry(session_id);
CREATE INDEX IF NOT EXISTS idx_redaction_registry_expires ON redaction_registry(expires_at);

-- redaction_audit: duplicate removed — canonical definition is at Phase 72, D-RDT-2 above
-- (merged superset schema covers both router.py and anonymizer.py column sets)

-- ============================================================
-- ORACLE ENGINE — MULTI-LENS PREDICTION INTELLIGENCE
-- ============================================================

-- Individual predictions emitted by oracle lenses (append-only, NIST AU)
CREATE TABLE IF NOT EXISTS oracle_predictions (
    id              TEXT PRIMARY KEY,
    lens_id         TEXT NOT NULL,
    lens_name       TEXT NOT NULL,
    subject_type    TEXT NOT NULL
        CHECK(subject_type IN ('project','agent','pipeline','requirement','deployment','system')),
    subject_id      TEXT NOT NULL,
    prediction_type TEXT NOT NULL,
    prediction_text TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 0.0
        CHECK(confidence >= 0.0 AND confidence <= 1.0),
    severity        TEXT NOT NULL DEFAULT 'medium'
        CHECK(severity IN ('critical','high','medium','low','info')),
    horizon_days    INTEGER NOT NULL DEFAULT 7,
    evidence_json   TEXT DEFAULT '{}',
    scoring_weights TEXT DEFAULT '{}',
    target          TEXT,
    rationale       TEXT,
    suggested_action TEXT,
    status          TEXT DEFAULT 'pending'
        CHECK(status IN ('pending','accepted','rejected','archived')),
    expires_at      TEXT,
    outcome         TEXT CHECK(outcome IN ('confirmed','refuted','expired','pending')),
    outcome_at      TEXT,
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_oracle_predictions_lens ON oracle_predictions(lens_id);
CREATE INDEX IF NOT EXISTS idx_oracle_predictions_subject ON oracle_predictions(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_oracle_predictions_severity ON oracle_predictions(severity);
CREATE INDEX IF NOT EXISTS idx_oracle_predictions_created ON oracle_predictions(created_at);

-- Convergence events: when 2+ lenses agree on the same prediction (append-only, NIST AU)
CREATE TABLE IF NOT EXISTS oracle_convergence_events (
    id                  TEXT PRIMARY KEY,
    convergence_type    TEXT NOT NULL
        CHECK(convergence_type IN ('risk','opportunity','anomaly','trend','threshold_breach')),
    subject_type        TEXT NOT NULL,
    subject_id          TEXT NOT NULL,
    lens_ids_json       TEXT NOT NULL,
    lens_count          INTEGER NOT NULL DEFAULT 2,
    consensus_score     REAL NOT NULL DEFAULT 0.0
        CHECK(consensus_score >= 0.0 AND consensus_score <= 1.0),
    severity            TEXT NOT NULL DEFAULT 'medium'
        CHECK(severity IN ('critical','high','medium','low','info')),
    summary             TEXT NOT NULL,
    prediction_ids_json TEXT DEFAULT '[]',
    recommended_action  TEXT,
    action_taken        INTEGER NOT NULL DEFAULT 0,
    action_notes        TEXT,
    resolved_at         TEXT,
    classification      TEXT NOT NULL DEFAULT 'CUI',
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_oracle_convergence_type ON oracle_convergence_events(convergence_type);
CREATE INDEX IF NOT EXISTS idx_oracle_convergence_subject ON oracle_convergence_events(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_oracle_convergence_severity ON oracle_convergence_events(severity);
CREATE INDEX IF NOT EXISTS idx_oracle_convergence_created ON oracle_convergence_events(created_at);

-- Per-lens runtime state: scheduling, health, and scoring config
CREATE TABLE IF NOT EXISTS oracle_lens_state (
    lens_id             TEXT PRIMARY KEY,
    lens_name           TEXT NOT NULL UNIQUE,
    domain              TEXT NOT NULL
        CHECK(domain IN ('technical_debt','security_risk','performance','compliance_risk','architecture_drift','delivery_risk')),
    enabled             INTEGER NOT NULL DEFAULT 1,
    weight              REAL NOT NULL DEFAULT 1.0,
    last_run_at         TEXT,
    next_run_at         TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_open INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_tripped_at TEXT,
    total_runs          INTEGER NOT NULL DEFAULT 0,
    total_predictions   INTEGER NOT NULL DEFAULT 0,
    accuracy_rate       REAL DEFAULT NULL,
    avg_confidence      REAL DEFAULT NULL,
    config_json         TEXT DEFAULT '{}',
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_oracle_lens_domain ON oracle_lens_state(domain);
CREATE INDEX IF NOT EXISTS idx_oracle_lens_enabled ON oracle_lens_state(enabled);

-- Remediation proposals generated by Oracle RemediationLens (append-only, NIST AU)
-- id is a deterministic hash from source_lens + subject_id
CREATE TABLE IF NOT EXISTS oracle_remediation_proposals (
    id                   TEXT PRIMARY KEY,
    source_lens          TEXT NOT NULL,
    source_prediction_id TEXT NOT NULL DEFAULT '',
    subject_type         TEXT NOT NULL
        CHECK(subject_type IN ('canvas','tool','pipeline','system')),
    subject_id           TEXT NOT NULL,
    title                TEXT NOT NULL,
    severity             TEXT NOT NULL DEFAULT 'medium'
        CHECK(severity IN ('critical','high','medium','low')),
    current_score        REAL,
    projected_score      REAL,
    proposal_json        TEXT NOT NULL DEFAULT '[]',
    impact_summary       TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','approved','rejected','executed','failed','expired')),
    reviewed_by          TEXT NOT NULL DEFAULT '',
    reviewed_at          TEXT NOT NULL DEFAULT '',
    review_notes         TEXT NOT NULL DEFAULT '',
    execution_result     TEXT NOT NULL DEFAULT '{}',
    executed_at          TEXT NOT NULL DEFAULT '',
    expires_at           TEXT NOT NULL DEFAULT '',
    classification       TEXT NOT NULL DEFAULT 'CUI',
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_oracle_proposals_status ON oracle_remediation_proposals(status);
CREATE INDEX IF NOT EXISTS idx_oracle_proposals_subject ON oracle_remediation_proposals(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_oracle_proposals_created ON oracle_remediation_proposals(created_at);

-- ============================================================
-- CANVAS PROJECTS (Cross-Canvas Integration Layer)
-- Phase: Cross-Canvas Integration
-- Links a "Design Project" entity across all 7 canvases:
--   IDC (infra), NDC (network), SDC (security), BDC (boundary),
--   PDC (pipeline), ODC (observability), DDC (data)
-- ============================================================
CREATE TABLE IF NOT EXISTS canvas_projects (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    links_json      TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_canvas_projects_name ON canvas_projects(name);
CREATE INDEX IF NOT EXISTS idx_canvas_projects_updated ON canvas_projects(updated_at);

-- ============================================================
-- CANVAS KG BUILD LOG (Append-only — NIST AU)
-- Phase: Cross-Canvas KG Incremental Updates
-- Records every targeted or full KG rebuild triggered by a
-- canvas design save or a manual CLI rebuild.
-- ============================================================
CREATE TABLE IF NOT EXISTS canvas_kg_build_log (
    build_id        TEXT PRIMARY KEY,
    canvas          TEXT NOT NULL,
    design_id       TEXT NOT NULL,
    nodes_upserted  INTEGER NOT NULL DEFAULT 0,
    edges_upserted  INTEGER NOT NULL DEFAULT 0,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ckg_build_log_canvas
    ON canvas_kg_build_log(canvas);
CREATE INDEX IF NOT EXISTS idx_ckg_build_log_design
    ON canvas_kg_build_log(design_id);

-- ============================================================
-- CANVAS KG NODES — Unified knowledge graph across all canvases
-- Phase: Cross-Canvas Integration
-- ============================================================
CREATE TABLE IF NOT EXISTS canvas_kg_nodes (
    id              TEXT PRIMARY KEY,
    canvas          TEXT NOT NULL,
    design_id       TEXT NOT NULL,
    node_id         TEXT NOT NULL,
    node_type       TEXT,
    label           TEXT,
    ontology_id     TEXT,
    metadata_json   TEXT DEFAULT '{}',
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ckg_nodes_canvas
    ON canvas_kg_nodes(canvas, design_id);

-- ============================================================
-- CANVAS KG EDGES — Unified knowledge graph edges across canvases
-- Phase: Cross-Canvas Integration
-- ============================================================
CREATE TABLE IF NOT EXISTS canvas_kg_edges (
    id              TEXT PRIMARY KEY,
    canvas          TEXT NOT NULL,
    design_id       TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    edge_type       TEXT,
    confidence      REAL DEFAULT 1.0,
    metadata_json   TEXT DEFAULT '{}',
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ckg_edges_canvas
    ON canvas_kg_edges(canvas, design_id);

CREATE TABLE IF NOT EXISTS reflex_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    reflex_name     TEXT NOT NULL,
    started_at      TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    finished_at     TIMESTAMP,
    duration_ms     INTEGER,
    status          TEXT NOT NULL DEFAULT 'running',
    artifact_count  INTEGER DEFAULT 0,
    error_msg       TEXT,
    result_json     TEXT DEFAULT '{}',
    trace_id        TEXT,
    span_id         TEXT
);
CREATE INDEX IF NOT EXISTS idx_reflex_obs_name
    ON reflex_observations(reflex_name);
CREATE INDEX IF NOT EXISTS idx_reflex_obs_started
    ON reflex_observations(started_at);

-- Migration 079: HITL Workflow Management
CREATE TABLE IF NOT EXISTS wf_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    canvas_type TEXT,
    stages_json TEXT NOT NULL,
    roles_json TEXT NOT NULL,
    approval_policy TEXT NOT NULL DEFAULT 'any_one',
    kickback_limit INTEGER NOT NULL DEFAULT 3,
    is_default INTEGER NOT NULL DEFAULT 0,
    is_system INTEGER NOT NULL DEFAULT 0,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    canvas_type TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_team_members (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES wf_teams(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    role_label TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(team_id, user_id)
);
CREATE TABLE IF NOT EXISTS wf_team_assignments (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES wf_teams(id) ON DELETE CASCADE,
    template_id TEXT REFERENCES wf_templates(id),
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(team_id, scope_type, scope_id)
);
CREATE TABLE IF NOT EXISTS wf_instances (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL REFERENCES wf_templates(id),
    task_id TEXT,
    project_id TEXT,
    canvas_type TEXT,
    current_stage TEXT NOT NULL DEFAULT 'build',
    kickback_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_approvals (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES wf_instances(id),
    stage TEXT NOT NULL,
    team_id TEXT REFERENCES wf_teams(id),
    assigned_to TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    due_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_feedback (
    id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL REFERENCES wf_approvals(id),
    instance_id TEXT NOT NULL REFERENCES wf_instances(id),
    task_id TEXT,
    canvas_type TEXT,
    template_id TEXT REFERENCES wf_templates(id),
    stage TEXT NOT NULL,
    decision TEXT NOT NULL,
    feedback_types TEXT,
    rating INTEGER,
    comments TEXT,
    improvement_tags TEXT,
    kickback_reason TEXT,
    citations_json TEXT,
    submitted_by TEXT NOT NULL,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_feedback_insights (
    id TEXT PRIMARY KEY,
    canvas_type TEXT,
    template_id TEXT,
    feedback_type TEXT,
    avg_rating REAL,
    issue_count INTEGER,
    top_tags TEXT,
    kickback_rate REAL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_external_steps (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES wf_instances(id),
    stage_name TEXT NOT NULL,
    step_type TEXT NOT NULL,
    external_system TEXT,
    external_ref TEXT,
    webhook_token TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    notified_at TEXT,
    completed_at TEXT,
    completed_by TEXT,
    payload_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_document_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    schema_json TEXT NOT NULL,
    canvas_type TEXT,
    stage_scope TEXT,
    is_ai_reference INTEGER NOT NULL DEFAULT 0,
    is_human_required INTEGER NOT NULL DEFAULT 0,
    version TEXT NOT NULL DEFAULT '1',
    is_system INTEGER NOT NULL DEFAULT 0,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_document_submissions (
    id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL REFERENCES wf_approvals(id),
    instance_id TEXT NOT NULL REFERENCES wf_instances(id),
    doc_template_id TEXT NOT NULL REFERENCES wf_document_templates(id),
    stage TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    submission_json TEXT NOT NULL,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_citations (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES wf_instances(id),
    stage TEXT NOT NULL,
    source_doc TEXT NOT NULL,
    source_type TEXT,
    doc_version TEXT,
    section TEXT,
    page_number INTEGER,
    excerpt TEXT,
    cited_by TEXT NOT NULL,
    cited_in_type TEXT NOT NULL,
    cited_in_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_wf_instances_task ON wf_instances(task_id);
CREATE INDEX IF NOT EXISTS idx_wf_instances_project ON wf_instances(project_id);
CREATE INDEX IF NOT EXISTS idx_wf_approvals_instance ON wf_approvals(instance_id, status);
CREATE INDEX IF NOT EXISTS idx_wf_feedback_instance ON wf_feedback(instance_id);
CREATE INDEX IF NOT EXISTS idx_wf_feedback_canvas ON wf_feedback(canvas_type, submitted_at);
CREATE INDEX IF NOT EXISTS idx_wf_team_assignments_scope ON wf_team_assignments(scope_type, scope_id);
CREATE INDEX IF NOT EXISTS idx_wf_ext_instance ON wf_external_steps(instance_id, status);
CREATE INDEX IF NOT EXISTS idx_wf_ext_token ON wf_external_steps(webhook_token);
CREATE INDEX IF NOT EXISTS idx_wf_docsub_approval ON wf_document_submissions(approval_id);
CREATE INDEX IF NOT EXISTS idx_wf_citations_instance ON wf_citations(instance_id, stage);

-- Migration 080: WF Report Ingestion
CREATE TABLE IF NOT EXISTS wf_ingested_files (
    id TEXT PRIMARY KEY,
    doc_template_id TEXT NOT NULL REFERENCES wf_document_templates(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_size_bytes INTEGER,
    content_hash TEXT NOT NULL,
    storage_path TEXT,
    page_count INTEGER,
    section_count INTEGER,
    ingestion_status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    chunk_count INTEGER DEFAULT 0,
    ingested_by TEXT,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_wf_ingest_doc ON wf_ingested_files(doc_template_id, ingestion_status);
CREATE INDEX IF NOT EXISTS idx_wf_ingest_hash ON wf_ingested_files(content_hash);
CREATE TABLE IF NOT EXISTS wf_report_section_defs (
    id TEXT PRIMARY KEY,
    report_type TEXT NOT NULL,
    section_key TEXT NOT NULL,
    section_name TEXT NOT NULL,
    description TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    required INTEGER NOT NULL DEFAULT 1,
    source_hints TEXT,
    min_chunks INTEGER DEFAULT 1,
    max_chunks INTEGER DEFAULT 8,
    max_words INTEGER DEFAULT 800,
    UNIQUE(report_type, section_key)
);
CREATE TABLE IF NOT EXISTS wf_generated_reports (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES wf_instances(id),
    report_type TEXT NOT NULL,
    style_guide_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    content_html TEXT,
    content_json TEXT,
    word_count INTEGER,
    section_count INTEGER,
    citation_count INTEGER,
    error_message TEXT,
    generated_by TEXT,
    generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_wf_reports_instance ON wf_generated_reports(instance_id, status);
CREATE TABLE IF NOT EXISTS wf_report_section_chunks (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES wf_generated_reports(id) ON DELETE CASCADE,
    section_key TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    relevance_score REAL,
    rank INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_wf_rsc_report ON wf_report_section_chunks(report_id, section_key);

-- Cross-Agency Data Transfer Audit (NIST AU-2, AU-9 — append-only)
CREATE TABLE IF NOT EXISTS cross_agency_transfers (
    id                  TEXT PRIMARY KEY,
    transfer_id         TEXT NOT NULL,
    event_type          TEXT NOT NULL CHECK(event_type IN (
                            'initiated', 'completed', 'failed', 'rejected')),
    source_agency       TEXT NOT NULL,
    target_agency       TEXT NOT NULL,
    data_type           TEXT,
    data_classification TEXT NOT NULL DEFAULT 'CUI',
    actor               TEXT NOT NULL DEFAULT '',
    project_id          TEXT,
    bytes_transferred   INTEGER,
    checksum            TEXT,
    duration_ms         INTEGER,
    rejection_reason    TEXT,
    error_code          TEXT,
    details             TEXT,
    occurred_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cat_transfer_id ON cross_agency_transfers(transfer_id);
CREATE INDEX IF NOT EXISTS idx_cat_occurred_at ON cross_agency_transfers(occurred_at);

-- ============================================================
-- CANVAS INSTANCES (Append-only — NIST AU-3/AU-12)
-- Tracks which catalog artifacts were activated per session+tenant.
-- ============================================================
CREATE TABLE IF NOT EXISTS canvas_instances (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    tenant_id       TEXT NOT NULL,
    canvas          TEXT NOT NULL,
    artifact_type   TEXT NOT NULL CHECK (artifact_type IN ('template','snippet','sop','runbook')),
    artifact_name   TEXT NOT NULL,
    use_case_id     TEXT,
    status          TEXT NOT NULL DEFAULT 'seeded' CHECK (status IN ('seeded','active','superseded')),
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_canvas_instances_session ON canvas_instances (session_id);
CREATE INDEX IF NOT EXISTS idx_canvas_instances_tenant_canvas ON canvas_instances (tenant_id, canvas);

-- JISE requirements feed (tools/dashboard/api/jise.py GET /requirements)
CREATE TABLE IF NOT EXISTS requirements (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open', 'closed', 'in_progress', 'draft', 'review', 'deferred')),
    priority       TEXT NOT NULL DEFAULT 'medium'
        CHECK(priority IN ('critical', 'high', 'medium', 'low')),
    classification TEXT NOT NULL DEFAULT 'CUI',
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_requirements_status   ON requirements (status);
CREATE INDEX IF NOT EXISTS idx_requirements_priority ON requirements (priority);
CREATE INDEX IF NOT EXISTS idx_requirements_created  ON requirements (created_at);

-- ============================================================
-- SIPA — Software Integrity & Provenance Assessor (sipa-db-03)
-- Sensitive findings tables (RLS-aware: tenant_id + classification on every
-- table). Canonical DDL lives in tools/integrity/db/init_db.py; mirrored here
-- (SQLite-flavored) so a fresh icdev.db carries the schema. CHECK values are
-- derived from tools/integrity/constants.py — keep the two in lock-step.
-- integrity_assessments is the mutable root row (HITL updates status/verdict);
-- its child tables are protected (see APPEND_ONLY_TABLES in pre_tool_use.py).
-- ============================================================
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
CREATE INDEX IF NOT EXISTS idx_integrity_assessments_tenant  ON integrity_assessments(tenant_id);
CREATE INDEX IF NOT EXISTS idx_integrity_assessments_project ON integrity_assessments(project_id);

-- append-only (NIST AU): findings/capability rows are evidence, never mutated.
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
CREATE INDEX IF NOT EXISTS idx_integrity_capabilities_assessment ON integrity_capabilities(assessment_id);
CREATE INDEX IF NOT EXISTS idx_integrity_capabilities_tenant     ON integrity_capabilities(tenant_id);

-- append-only (NIST AU): scanner findings are immutable evidence.
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
CREATE INDEX IF NOT EXISTS idx_integrity_findings_assessment ON integrity_findings(assessment_id);
CREATE INDEX IF NOT EXISTS idx_integrity_findings_tenant     ON integrity_findings(tenant_id);

-- append-only (NIST AU): each verdict is a permanent decision record.
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
CREATE INDEX IF NOT EXISTS idx_integrity_verdicts_assessment ON integrity_verdicts(assessment_id);
CREATE INDEX IF NOT EXISTS idx_integrity_verdicts_tenant     ON integrity_verdicts(tenant_id);

-- append-only (NIST AU): HITL authorization decisions are immutable evidence.
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
CREATE INDEX IF NOT EXISTS idx_integrity_authorizations_assessment ON integrity_authorizations(assessment_id);
CREATE INDEX IF NOT EXISTS idx_integrity_authorizations_tenant     ON integrity_authorizations(tenant_id);

-- ============================================================
-- EQO — Centralized logging (eqo-log-01)
-- Global append-only log sink (NOT a canvas table): carries tenant_id +
-- classification so the RLS-aware get_connection() applies the standard
-- row-level predicate. Append-only (NIST AU) — log rows are immutable evidence;
-- retention is enforced by bulk time-window pruning, never row mutation.
-- Registered in APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py.
-- Mirror of migration 181_centralized_logs — keep the two in lock-step.
-- ============================================================
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

-- ============================================================
-- TENANT COMPONENT OVERRIDES (Phase 5 enterprise-configurable platform)
-- ============================================================
CREATE TABLE IF NOT EXISTS tenant_component_overrides (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    component_key TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    updated_by TEXT DEFAULT 'system',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(tenant_id, component_key)
);

CREATE INDEX IF NOT EXISTS idx_tenant_component_overrides_tenant ON tenant_component_overrides(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_component_overrides_key ON tenant_component_overrides(component_key);

-- ============================================================
-- COMPONENT AUDIT LOG (Phase 5 enterprise-configurable platform)
-- ============================================================
-- Append-only record of enable/disable actions, profile applies, and
-- tenant-level component overrides. Kept separate from audit_trail because
-- component events have a stable, narrow schema.
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

-- ============================================================
-- USER GROUPS (Migration 163: G-01, NIST AC-3/AC-6)
-- ============================================================
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    classification TEXT NOT NULL DEFAULT 'CUI',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled'))
);
CREATE INDEX IF NOT EXISTS idx_groups_tenant ON groups (tenant_id);

CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    added_at TEXT NOT NULL,
    added_by TEXT,
    PRIMARY KEY (group_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members (user_id);

CREATE TABLE IF NOT EXISTS group_roles (
    group_id TEXT NOT NULL,
    role TEXT NOT NULL,
    canvas_scope TEXT,
    granted_at TEXT NOT NULL,
    granted_by TEXT,
    PRIMARY KEY (group_id, role, canvas_scope)
);

-- ============================================================
-- CANVAS ACCESS GRANTS (Migration 163: ZTA Pillar 5, NIST AC-3/AC-16)
-- Explicit deny-all default: principal must have a row to access any canvas.
-- ============================================================
CREATE TABLE IF NOT EXISTS canvas_access_grants (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    principal_type TEXT NOT NULL CHECK (principal_type IN ('user', 'group', 'role')),
    principal_id TEXT NOT NULL,
    canvas_name TEXT NOT NULL,
    access_level TEXT NOT NULL CHECK (access_level IN ('read', 'write', 'admin')),
    granted_by TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    UNIQUE (tenant_id, principal_type, principal_id, canvas_name)
);
CREATE INDEX IF NOT EXISTS idx_cag_tenant_canvas ON canvas_access_grants (tenant_id, canvas_name);
CREATE INDEX IF NOT EXISTS idx_cag_principal ON canvas_access_grants (principal_type, principal_id);

"""

# ---------------------------------------------------------------------------
# audit_trail.event_type CHECK — generated, never hand-maintained
# ---------------------------------------------------------------------------
#
# This list used to be duplicated here as a literal. It drifted: the constant
# held 221 event types while this copy admitted 189, and neither included the
# 25 govcon.* types that tools/govcon writes — so every govcon audit INSERT was
# rejected by the constraint and silently swallowed.
#
# CLAUDE.md requires CHECK constraints be derived from Python constants rather
# than hardcoded. Deriving it here means the two cannot drift again: adding an
# event type to VALID_EVENT_TYPES is now sufficient for a fresh database, and
# tests/test_audit_event_type_parity.py fails if an existing one falls behind.
from tools.audit.audit_logger import VALID_EVENT_TYPES as _VALID_EVENT_TYPES  # noqa: E402

_AUDIT_EVENT_TYPES_SQL = ",\n".join(
    f"        '{_t}'" for _t in _VALID_EVENT_TYPES
)
SCHEMA_SQL = SCHEMA_SQL.replace("@@AUDIT_EVENT_TYPES@@", _AUDIT_EVENT_TYPES_SQL)


# Phase 64: RAG columns on projects table
RAG_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN rag_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN rag_chunk_count INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN rag_last_ingestion TIMESTAMP",
]

MBSE_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN sysml_model_path TEXT",
    "ALTER TABLE projects ADD COLUMN doors_module_path TEXT",
    "ALTER TABLE projects ADD COLUMN mbse_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN des_compliant INTEGER DEFAULT 0",
]

MODERNIZATION_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN modernization_status TEXT DEFAULT 'none'",
    "ALTER TABLE projects ADD COLUMN legacy_app_count INTEGER DEFAULT 0",
]

RICOAS_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN ricoas_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN intake_session_count INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN active_coa_id TEXT",
]

GOVCON_ALTER_SQL = [
    "ALTER TABLE proposal_opportunities ADD COLUMN licensing_model TEXT",
    "ALTER TABLE proposal_opportunities ADD COLUMN sam_gov_opportunity_id TEXT",
]

AGENTIC_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN agentic_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN fitness_score REAL",
    "ALTER TABLE projects ADD COLUMN architecture_recommendation TEXT",
    "ALTER TABLE projects ADD COLUMN child_app_count INTEGER DEFAULT 0",
]

MARKETPLACE_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN marketplace_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN marketplace_asset_count INTEGER DEFAULT 0",
]

FIPS_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN fips199_confidentiality TEXT",
    "ALTER TABLE projects ADD COLUMN fips199_integrity TEXT",
    "ALTER TABLE projects ADD COLUMN fips199_availability TEXT",
    "ALTER TABLE projects ADD COLUMN fips199_overall TEXT",
    "ALTER TABLE projects ADD COLUMN fips199_categorization_id INTEGER",
    "ALTER TABLE projects ADD COLUMN nss_system INTEGER DEFAULT 0",
]

COMPLIANCE_PLATFORM_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN data_categories TEXT",
    "ALTER TABLE projects ADD COLUMN applicable_frameworks TEXT",
    "ALTER TABLE projects ADD COLUMN multi_regime_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN compliance_detection_date TIMESTAMP",
]

MOSA_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN mosa_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN mosa_modularity_score REAL",
]

INNOVATION_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN innovation_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN innovation_signal_count INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN innovation_solution_count INTEGER DEFAULT 0",
]

# Phase 36: Evolutionary Intelligence columns
EVOLUTION_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN genome_version TEXT",
    "ALTER TABLE projects ADD COLUMN child_capability_count INTEGER DEFAULT 0",
]

# Phase 37: AI Security columns
AI_SECURITY_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN atlas_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN ai_telemetry_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN prompt_injection_defense_active INTEGER DEFAULT 0",
]

# Phase 38: Cloud-Agnostic columns
CLOUD_AGNOSTIC_ALTER_SQL = [
    "ALTER TABLE tenants ADD COLUMN cloud_provider TEXT DEFAULT 'aws'",
    "ALTER TABLE tenants ADD COLUMN cloud_region TEXT DEFAULT 'us-gov-west-1'",
]

# Phase 43: Cross-Language Translation columns
TRANSLATION_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN translation_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN translation_job_count INTEGER DEFAULT 0",
]

# Phase 45: OWASP Agentic AI Security columns (D257-D264)
OWASP_AGENTIC_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN owasp_agentic_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN agent_trust_scoring_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN tool_chain_validation_enabled INTEGER DEFAULT 0",
]

# Phase 46: Observability, Traceability & XAI columns (D280-D290)
OBSERVABILITY_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN observability_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN tracing_backend TEXT DEFAULT 'sqlite'",
    "ALTER TABLE projects ADD COLUMN provenance_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN shap_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN xai_assessment_status TEXT DEFAULT 'not_assessed'",
]

# Phase 48: AI Transparency & Accountability columns (D307-D315)
AI_TRANSPARENCY_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN ai_transparency_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN ai_inventory_count INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN model_card_count INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN gao_readiness_score REAL",
]

# Spec-kit Pattern 7: Parallel task markers (D161)
SPECKIT_ALTER_SQL = [
    "ALTER TABLE safe_decomposition ADD COLUMN parallel_group TEXT",
]

# Phase 30: Dashboard auth — extend agent_token_usage for per-user tracking (D177)
DASHBOARD_AUTH_ALTER_SQL = [
    "ALTER TABLE agent_token_usage ADD COLUMN user_id TEXT DEFAULT NULL",
    "ALTER TABLE agent_token_usage ADD COLUMN api_key_source TEXT DEFAULT 'config'",
]

# Phase 59: Questions to Government columns (D-QTG-1)
QTG_ALTER_SQL = [
    "ALTER TABLE proposal_opportunities ADD COLUMN questions_due_date TEXT",
    "ALTER TABLE proposal_opportunities ADD COLUMN amendment_count INTEGER DEFAULT 0",
    "ALTER TABLE proposal_opportunities ADD COLUMN question_count INTEGER DEFAULT 0",
]

# Phase 60: CPMP — link proposal_opportunities and customer_deliveries to contracts (D-CPMP-9)
CPMP_ALTER_SQL = [
    "ALTER TABLE proposal_opportunities ADD COLUMN contract_id TEXT",
    "ALTER TABLE customer_deliveries ADD COLUMN contract_id TEXT",
]

# Phase 64 Extension: Fine-Tuning columns (D-FT-1)
FINETUNE_ALTER_SQL = [
    "ALTER TABLE projects ADD COLUMN finetune_enabled INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN finetune_dataset_count INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN finetune_active_model_count INTEGER DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN finetune_last_training TIMESTAMP",
]

# Proposals Module Enhancement — capture / review / compliance columns (prop-cap/rev/cmp)
PROPOSALS_ALTER_SQL = [
    "ALTER TABLE proposal_opportunities ADD COLUMN win_probability INTEGER",
    "ALTER TABLE proposal_opportunities ADD COLUMN capture_notes TEXT",
    "ALTER TABLE proposal_opportunities ADD COLUMN win_themes TEXT",
    "ALTER TABLE proposal_opportunities ADD COLUMN key_discriminators TEXT",
    "ALTER TABLE proposal_opportunities ADD COLUMN ptw_low NUMERIC",
    "ALTER TABLE proposal_opportunities ADD COLUMN ptw_high NUMERIC",
    "ALTER TABLE proposal_opportunities ADD COLUMN capture_phase TEXT",
    "ALTER TABLE proposal_reviews ADD COLUMN executive_summary TEXT",
    "ALTER TABLE proposal_review_findings ADD COLUMN resolved_evidence TEXT",
    "ALTER TABLE proposal_review_findings ADD COLUMN closure_approved_by TEXT",
    "ALTER TABLE proposal_amendments ADD COLUMN changed_requirement_ids TEXT",
]


def _has_migration_system(path):
    """Check if the database is managed by the migration framework (D150)."""
    path = Path(path) if not isinstance(path, Path) else path
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(str(path))
        # pg-portability: sqlite-only path — monolithic SQLite initializer probing
        # a raw sqlite3 file; the PG init path is handled by pg_init / MigrationRunner.
        c = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
        has_table = c.fetchone() is not None
        conn.close()
        return has_table
    except Exception:
        return False


def init_db(db_path=None):
    """Initialize the ICDEV™ database with full schema.

    If the migration system (schema_migrations table) is detected, redirects
    to the migration runner instead of re-running the monolithic init script.

    For PostgreSQL backends (ICDEV_STORAGE_BACKEND=postgresql), this monolithic
    SQLite-flavored init is not the right tool — delegate to the migration
    runner which handles cross-backend DDL via the SQL translator.
    """
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()
    if backend == "postgresql":
        print(
            "ICDEV_STORAGE_BACKEND=postgresql detected. This monolithic "
            "init script is SQLite-only. Run the migration framework "
            "instead: `python -m icdev.tools.db.migrate --up` (or "
            "`python tools/db/migrate.py --up` from a checkout)."
        )
        # G-06: Apply PostgreSQL column-level GRANTs after schema is up
        try:
            from tools.security.column_security import apply_column_grants
            grant_result = apply_column_grants()
            print(
                f"Column GRANTs: {grant_result['grants_applied']} applied, "
                f"{grant_result['grants_skipped']} skipped."
            )
        except Exception as _cg_exc:
            print(f"Warning: column grant application failed: {_cg_exc}")
        return []

    path = Path(db_path) if db_path and not isinstance(db_path, Path) else (db_path or DB_PATH)

    # D150: Detect migration system — if active, delegate to migration runner
    if _has_migration_system(path):
        print(f"Migration system detected in {path} — use 'python tools/db/migrate.py --up' for schema changes.")
        return []

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA_SQL)
    # Idempotent ALTER TABLE for MBSE columns (Phase 18)
    for alter_sql in MBSE_ALTER_SQL:
        try:
            conn.execute(alter_sql)
        except sqlite3.OperationalError:
            pass  # Column already exists
    # Idempotent ALTER TABLE for Modernization columns (Phase 19)
    for alter_sql in MODERNIZATION_ALTER_SQL:
        try:
            conn.execute(alter_sql)
        except sqlite3.OperationalError:
            pass  # Column already exists
    # Idempotent ALTER TABLE for RICOAS columns (Phase 20)
    for alter_sql in RICOAS_ALTER_SQL:
        try:
            conn.execute(alter_sql)
        except sqlite3.OperationalError:
            pass  # Column already exists
    # Idempotent ALTER TABLE for Agentic columns (Phase 19 - Agentic Generation)
    for alter_sql in AGENTIC_ALTER_SQL:
        try:
            conn.execute(alter_sql)
        except sqlite3.OperationalError:
            pass  # Column already exists
    # Idempotent ALTER TABLE for Marketplace columns (Phase 22)
    for alter_sql in MARKETPLACE_ALTER_SQL:
        try:
            conn.execute(alter_sql)
        except sqlite3.OperationalError:
            pass  # Column already exists
    # Idempotent ALTER TABLE for FIPS 199/200 columns (Phase 20)
    for alter_sql in FIPS_ALTER_SQL:
        try:
            conn.execute(alter_sql)
        except sqlite3.OperationalError:
            pass  # Column already exists
    # Idempotent ALTER TABLE for Universal Compliance Platform columns (Phase 23)
    for alter_sql in COMPLIANCE_PLATFORM_ALTER_SQL:
        try:
            conn.execute(alter_sql)
        except sqlite3.OperationalError:
            pass  # Column already exists
    # Phase 26: MOSA columns
    for sql in MOSA_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Spec-kit Pattern 7: Parallel task markers (D161)
    for sql in SPECKIT_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Phase 30: Dashboard auth — extend agent_token_usage (D177)
    for sql in DASHBOARD_AUTH_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Phase 35: Innovation Engine columns (D199-D208)
    for sql in INNOVATION_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Phase 36: Evolutionary Intelligence columns
    for sql in EVOLUTION_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Phase 37: AI Security columns
    for sql in AI_SECURITY_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Phase 38: Cloud-Agnostic columns (tenants table may not exist in all envs)
    for sql in CLOUD_AGNOSTIC_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Phase 43: Cross-Language Translation columns
    for sql in TRANSLATION_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Phase 45: OWASP Agentic AI Security columns (D257-D264)
    for sql in OWASP_AGENTIC_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Phase 46: Observability, Traceability & XAI columns (D280-D290)
    for sql in OBSERVABILITY_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Phase 48: AI Transparency & Accountability columns (D307-D315)
    for sql in AI_TRANSPARENCY_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Phase 59: GovCon Intelligence columns (D361-D373)
    for sql in GOVCON_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Phase 59: Questions to Government columns (D-QTG-1)
    for sql in QTG_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Phase 60: CPMP — link proposals/deliveries to contracts (D-CPMP-9)
    for sql in CPMP_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Phase 64 Extension: Fine-Tuning columns (D-FT-1)
    for sql in FINETUNE_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Phase 64: RAG Subsystem columns (D-RAG-1)
    for sql in RAG_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    # Proposals Module Enhancement — capture / review / compliance columns
    for sql in PROPOSALS_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass

    # D-WG-14: Migrate wg_glossary CHECK constraint to include 'required'
    try:
        # pg-portability: sqlite-only path — reads table DDL from sqlite_master to
        # migrate a CHECK constraint during the monolithic SQLite init; not run on PG.
        _cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='wg_glossary'")
        _row = _cur.fetchone()
        if _row and _row[0] and "'required'" not in _row[0]:
            conn.execute("BEGIN TRANSACTION")
            conn.execute("""
                CREATE TABLE wg_glossary_new (
                    id TEXT PRIMARY KEY,
                    term TEXT NOT NULL,
                    term_type TEXT NOT NULL CHECK(term_type IN (
                        'acronym','preferred','deprecated','banned','custom_spell','required'
                    )),
                    expansion TEXT NOT NULL DEFAULT '',
                    replacement TEXT NOT NULL DEFAULT '',
                    definition TEXT NOT NULL DEFAULT '',
                    domain TEXT NOT NULL DEFAULT 'general'
                        CHECK(domain IN ('general','far','nist','cyber','project')),
                    scope TEXT NOT NULL DEFAULT 'platform'
                        CHECK(scope IN ('platform','tenant','program','project','user')),
                    scope_id TEXT NOT NULL DEFAULT '',
                    case_sensitive INTEGER NOT NULL DEFAULT 1,
                    enforcement TEXT NOT NULL DEFAULT 'suggest'
                        CHECK(enforcement IN ('suggest','warn','block')),
                    source TEXT NOT NULL DEFAULT 'admin'
                        CHECK(source IN ('builtin','admin','user','import:far','import:nist','import:cui')),
                    approved_by TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    is_active INTEGER NOT NULL DEFAULT 1,
                    classification TEXT NOT NULL DEFAULT 'CUI'
                )
            """)
            conn.execute("INSERT INTO wg_glossary_new SELECT * FROM wg_glossary")
            conn.execute("DROP TABLE wg_glossary")
            conn.execute("ALTER TABLE wg_glossary_new RENAME TO wg_glossary")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wg_glossary_term ON wg_glossary(term)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wg_glossary_type ON wg_glossary(term_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wg_glossary_scope ON wg_glossary(scope, scope_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wg_glossary_domain ON wg_glossary(domain)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wg_glossary_active ON wg_glossary(is_active)")
            conn.execute("COMMIT")
    except Exception as _wg_exc:
        # If migration fails (e.g., foreign keys), continue — schema still valid for new installs
        print(f"Note: wg_glossary migration skipped ({_wg_exc})")

    conn.commit()
    conn.close()
    print(f"ICDEV™ database initialized at {path}")

    # Seed system workflow templates (idempotent — INSERT OR IGNORE)
    try:
        from tools.db.seeds.seed_workflow_templates import run as _seed_wf
        _seed_wf(verbose=True)
    except Exception as _exc:
        print(f"Warning: workflow template seed skipped — {_exc}")

    # Seed E2E demo session (ME conflict intelligence) — idempotent INSERT OR IGNORE
    #
    # Two bugs used to live in this block. It bound `%s` placeholders on a RAW
    # sqlite3 connection, which does not go through storage.translate_sql, so every
    # run raised `near "%": syntax error` and the seed has never once been written —
    # the failure was invisible because it printed a warning and moved on. And
    # because the raise happened at .execute(), the `close()` below it was never
    # reached, leaking the handle: on Windows that leaked handle makes the
    # subsequent `--reset` unlink fail with WinError 32 (which is what broke
    # tests/test_init_icdev_db.py::TestMainFunction::test_main_with_reset_flag).
    # This is the SQLite-only monolithic init path, so `?` is the correct
    # placeholder here; the close now happens in a finally.
    _seed_conn = None
    try:
        _seed_conn = sqlite3.connect(str(path))
        _seed_conn.execute(
            "INSERT OR IGNORE INTO intake_sessions "
            "(id, customer_name, customer_org, session_status, classification, context_summary) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "sess-9cc6891cb548",
                "E2E Test User",
                "ICDEV CI",
                "active",
                "CUI",
                "{}",
            ),
        )
        _seed_conn.commit()
    except Exception as _exc:
        print(f"Warning: demo session seed skipped — {_exc}")
    finally:
        if _seed_conn is not None:
            _seed_conn.close()

    # Verify tables
    conn = sqlite3.connect(str(path))
    c = conn.cursor()
    # pg-portability: sqlite-only path — verifies created tables in the raw sqlite3
    # file at the end of the monolithic SQLite init; PG uses information_schema.
    c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in c.fetchall()]
    conn.close()
    print(f"Tables created ({len(tables)}): {', '.join(tables)}")
    return tables


def main():
    parser = argparse.ArgumentParser(description="Initialize ICDEV™ database")
    parser.add_argument("--db-path", type=Path, default=DB_PATH, help="Database file path")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate all tables")
    args = parser.parse_args()

    if args.reset and args.db_path.exists():
        args.db_path.unlink()
        print(f"Removed existing database: {args.db_path}")

    init_db(args.db_path)


if __name__ == "__main__":
    main()
