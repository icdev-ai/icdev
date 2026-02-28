#!/usr/bin/env python3
# CUI // SP-CTI
"""Initialize the ICDEV operational database with full schema."""

import sqlite3
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        'project_created', 'project_updated',
        'code_generated', 'code_reviewed', 'code_approved', 'code_rejected',
        'test_written', 'test_executed', 'test_passed', 'test_failed',
        'security_scan', 'vulnerability_found', 'vulnerability_resolved',
        'compliance_check', 'ssp_generated', 'poam_generated', 'stig_checked', 'sbom_generated',
        'deployment_initiated', 'deployment_succeeded', 'deployment_failed', 'rollback_executed',
        'decision_made', 'approval_granted', 'approval_denied',
        'agent_task_submitted', 'agent_task_completed', 'agent_task_failed',
        'self_heal_triggered', 'pattern_detected', 'knowledge_recorded',
        'config_changed', 'secret_rotated',
        'cssp_assessed', 'cssp_report_generated', 'cssp_evidence_collected',
        'ir_plan_generated', 'siem_config_generated',
        'xacta_sync', 'xacta_sync_completed', 'xacta_export',
        'maintenance_audit', 'dependency_scanned', 'vulnerability_checked',
        'remediation_applied', 'sla_violation',
        'fedramp_assessed', 'cmmc_assessed', 'oscal_generated',
        'emass_sync', 'emass_push', 'emass_pull',
        'cato_evidence_collected', 'classification_changed',
        'crosswalk_mapped', 'pi_compliance_updated',
        'model_imported', 'model_synced', 'model_snapshot',
        'digital_thread_linked', 'des_assessed',
        'reqif_imported', 'xmi_imported',
        'code_from_model', 'model_from_code',
        'legacy_analyzed', 'migration_assessed', 'migration_planned',
        'migration_task_completed', 'migration_code_generated',
        'schema_migrated', 'service_extracted', 'strangler_fig_cutover',
        'intake_session_created', 'intake_session_resumed', 'intake_session_completed',
        'requirement_captured', 'requirement_refined', 'requirement_approved',
        'gap_detected', 'ambiguity_detected',
        'readiness_scored', 'decomposition_generated',
        'document_uploaded', 'document_extracted',
        'bdd_criteria_generated',
        'boundary_assessed', 'boundary_impact_red', 'boundary_alternative_generated',
        'ato_system_registered', 'isa_created', 'isa_expired', 'isa_renewed',
        'scrm_assessed', 'cve_triaged', 'cve_impact_propagated',
        'supply_chain_risk_escalated',
        'simulation_created', 'simulation_completed', 'monte_carlo_completed',
        'coa_generated', 'coa_alternative_generated', 'coa_compared',
        'coa_selected', 'coa_rejected', 'coa_presented',
        'integration_configured', 'integration_sync_push', 'integration_sync_pull',
        'integration_sync_error', 'reqif_exported',
        'approval_submitted', 'approval_reviewed', 'approval_approved',
        'approval_rejected', 'approval_escalated',
        'rtm_generated', 'rtm_gap_detected',
        'hook_event_logged', 'agent_execution_started', 'agent_execution_completed',
        'agent_execution_failed', 'agent_execution_retried',
        'nlq_query_executed', 'nlq_query_blocked',
        'worktree_created', 'worktree_cleaned',
        'gitlab_task_claimed', 'gitlab_task_completed', 'gitlab_task_failed',
        'agentic_fitness_assessed', 'child_app_generated',
        'agentic_scaffolded', 'agentic_code_generated',
        'governance_validated', 'agentic_test_generated',
        'fips199_categorized', 'fips200_assessed',
        'security_categorization_completed', 'baseline_selected',
        'marketplace_asset_published', 'marketplace_asset_installed',
        'marketplace_asset_uninstalled', 'marketplace_asset_updated',
        'marketplace_asset_deprecated', 'marketplace_asset_revoked',
        'marketplace_review_submitted', 'marketplace_review_completed',
        'marketplace_scan_completed', 'marketplace_federation_sync',
        'marketplace_rating_submitted',
        'compliance_detected', 'compliance_confirmed',
        'multi_regime_assessed', 'multi_regime_gate_evaluated',
        'data_category_assigned', 'data_category_detected',
        'framework_applicability_set', 'iso_bridge_mapped',
        'cjis_assessed', 'hipaa_assessed', 'hitrust_assessed',
        'soc2_assessed', 'pci_dss_assessed', 'iso27001_assessed',
        'remote_binding_created', 'remote_binding_provisioned', 'remote_binding_revoked',
        'remote_command_received', 'remote_command_rejected', 'remote_command_completed',
        'remote_response_filtered',
        'spec_quality_check', 'spec_consistency_check',
        'constitution_added', 'constitution_removed', 'constitution_defaults_loaded',
        'clarification_analyzed',
        'spec.init', 'spec.register',
        'heartbeat_check_warning', 'heartbeat_check_critical',
        'auto_resolution_started', 'auto_resolution_completed',
        'auto_resolution_failed', 'auto_resolution_escalated'
    )),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    affected_files TEXT,
    classification TEXT DEFAULT 'CUI',
    ip_address TEXT,
    session_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_project ON audit_trail(project_id);
CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_trail(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_trail(actor);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_trail(created_at);

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
    description TEXT NOT NULL,
    root_cause TEXT,
    remediation TEXT,
    confidence REAL DEFAULT 0.0,
    occurrence_count INTEGER DEFAULT 1,
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
    artifact_type TEXT NOT NULL CHECK(artifact_type IN ('ssp', 'poam', 'assessment_results', 'component_definition', 'catalog', 'profile')),
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
    authorizing_official TEXT,
    last_sync TEXT,
    sync_status TEXT DEFAULT 'never' CHECK(sync_status IN ('never', 'success', 'partial', 'failed')),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- eMASS sync history log
CREATE TABLE IF NOT EXISTS emass_sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    sync_direction TEXT NOT NULL CHECK(sync_direction IN ('push', 'pull', 'bidirectional')),
    artifact_type TEXT,
    status TEXT NOT NULL CHECK(status IN ('started', 'success', 'partial', 'failed')),
    items_synced INTEGER DEFAULT 0,
    items_failed INTEGER DEFAULT 0,
    error_details TEXT,
    sync_duration_ms INTEGER,
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
            'schedule', 'cost', 'risk')),
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
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_token_usage_agent ON agent_token_usage(agent_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_project ON agent_token_usage(project_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_created ON agent_token_usage(created_at);

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
    child_path TEXT NOT NULL,
    blueprint_hash TEXT,
    fitness_assessment_id TEXT REFERENCES agentic_fitness_assessments(id),
    capabilities TEXT NOT NULL,
    agent_count INTEGER DEFAULT 5,
    cloud_provider TEXT DEFAULT 'aws',
    callback_url TEXT,
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
-- MARKETPLACE — Federated GOTCHA Asset Registry (Phase 22)
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
    maturity_level TEXT CHECK(maturity_level IN ('traditional', 'advanced', 'optimal')),
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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

-- Bound identities: channel user <-> ICDEV user
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
        CHECK(status IN ('pending', 'ok', 'warning', 'critical', 'error')),
    result_summary TEXT,
    items_found INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_hb_check_type ON heartbeat_checks(check_type);
CREATE INDEX IF NOT EXISTS idx_hb_status ON heartbeat_checks(status);
CREATE INDEX IF NOT EXISTS idx_hb_next_run ON heartbeat_checks(next_run);

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
    role TEXT NOT NULL DEFAULT 'developer'
        CHECK(role IN ('admin', 'pm', 'developer', 'isso', 'co')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'suspended')),
    created_by TEXT,
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
    url TEXT,
    metadata TEXT,
    community_score REAL DEFAULT 0.0,
    content_hash TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK(status IN ('new', 'scored', 'triaged', 'approved', 'suggested',
                         'blocked', 'logged', 'solution_generated', 'published')),
    category TEXT,
    innovation_score REAL,
    score_breakdown TEXT,
    triage_result TEXT
        CHECK(triage_result IS NULL OR triage_result IN ('approved', 'suggested', 'blocked', 'logged')),
    gotcha_layer TEXT
        CHECK(gotcha_layer IS NULL OR gotcha_layer IN ('goal', 'tool', 'arg', 'context', 'hardprompt')),
    boundary_tier TEXT
        CHECK(boundary_tier IS NULL OR boundary_tier IN ('GREEN', 'YELLOW', 'ORANGE', 'RED')),
    classification TEXT DEFAULT 'CUI'
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
        CHECK(stage_name IN ('classify', 'gotcha_fit', 'boundary_impact',
                              'compliance_check', 'dedup_license')),
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
    findings TEXT,
    project_id TEXT,
    user_id TEXT,
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
    latency_ms INTEGER DEFAULT 0,
    agent_id TEXT,
    user_id TEXT,
    project_id TEXT,
    function TEXT,
    classification TEXT DEFAULT 'CUI',
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
    provider TEXT NOT NULL,
    version TEXT,
    purpose TEXT,
    risk_classification TEXT,
    data_categories TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    technique_name TEXT,
    test_name TEXT NOT NULL,
    result TEXT CHECK(result IN ('pass', 'fail', 'partial', 'error')),
    severity TEXT CHECK(severity IN ('critical', 'high', 'medium', 'low', 'info')),
    details TEXT,
    evidence TEXT,
    remediation TEXT,
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
    genome_version TEXT,
    uptime_hours REAL DEFAULT 0.0,
    error_rate REAL DEFAULT 0.0,
    compliance_scores_json TEXT,
    learned_behaviors_json TEXT,
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
    target_children_json TEXT,
    status TEXT CHECK(status IN ('prepared', 'approved', 'executing', 'completed', 'failed', 'rolled_back')) DEFAULT 'prepared',
    genome_version_before TEXT,
    genome_version_after TEXT,
    rollback_plan TEXT,
    prepared_by TEXT,
    approved_by TEXT,
    approved_at TEXT,
    executed_by TEXT,
    executed_at TEXT,
    completed_at TEXT,
    rollback_reason TEXT,
    rolled_back_at TEXT,
    rolled_back_by TEXT,
    execution_results_json TEXT,
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
    kind TEXT NOT NULL CHECK(kind IN ('function','class','interface','enum','struct','trait','module')),
    file_path TEXT,
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
    content_type TEXT DEFAULT 'text' CHECK(content_type IN ('text','tool_result','error','intervention','summary')),
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

-- ── System Cards (ICDEV system-level AI documentation) ──
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
    assessment_data TEXT NOT NULL,
    overall_score REAL DEFAULT 0.0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fairness_project ON fairness_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_fairness_created ON fairness_assessments(created_at);

-- ============================================================
-- AI ACCOUNTABILITY (Phase 49, D316-D321)
-- ============================================================

-- ── AI Oversight Plans (M25-OVR-1, GAO accountability, append-only) ──
CREATE TABLE IF NOT EXISTS ai_oversight_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    plan_name TEXT NOT NULL,
    plan_data TEXT NOT NULL,
    approval_status TEXT DEFAULT 'draft'
        CHECK(approval_status IN ('draft', 'submitted', 'approved', 'rejected')),
    approved_by TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_oversight_project ON ai_oversight_plans(project_id);

-- ── AI Accountability Appeals (M25-OVR-3, M26-REV-2, FAIR-7, append-only) ──
CREATE TABLE IF NOT EXISTS ai_accountability_appeals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    appellant TEXT NOT NULL,
    ai_system TEXT NOT NULL,
    decision_contested TEXT,
    appeal_status TEXT DEFAULT 'submitted'
        CHECK(appeal_status IN ('submitted', 'under_review', 'resolved', 'dismissed')),
    resolution TEXT,
    resolved_by TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_appeals_project ON ai_accountability_appeals(project_id);
CREATE INDEX IF NOT EXISTS idx_ai_appeals_status ON ai_accountability_appeals(appeal_status);

-- ── AI CAIO Registry (M25-OVR-4, Chief AI Officer tracking) ──
CREATE TABLE IF NOT EXISTS ai_caio_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    official_name TEXT NOT NULL,
    official_role TEXT NOT NULL DEFAULT 'CAIO',
    organization TEXT,
    designation_date TEXT,
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
    findings TEXT,
    opt_out_policy INTEGER DEFAULT 0,
    legal_compliance_matrix INTEGER DEFAULT 0,
    pre_deployment_review INTEGER DEFAULT 0,
    reviewer TEXT,
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
    smells_json TEXT DEFAULT '[]',
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

-- Phase 53: OWASP ASI01-ASI10 Assessments (D339)
CREATE TABLE IF NOT EXISTS owasp_asi_assessments (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    assessment_date TEXT DEFAULT (datetime('now')),
    results_json TEXT,
    total_controls INTEGER DEFAULT 0,
    satisfied_count INTEGER DEFAULT 0,
    not_satisfied_count INTEGER DEFAULT 0,
    coverage_pct REAL DEFAULT 0.0,
    assessor_version TEXT DEFAULT '1.0',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_owasp_asi_project ON owasp_asi_assessments(project_id);

-- Phase 57: EU AI Act Assessments (D349)
CREATE TABLE IF NOT EXISTS eu_ai_act_assessments (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    assessment_date TEXT DEFAULT (datetime('now')),
    results_json TEXT,
    total_controls INTEGER DEFAULT 0,
    satisfied_count INTEGER DEFAULT 0,
    not_satisfied_count INTEGER DEFAULT 0,
    coverage_pct REAL DEFAULT 0.0,
    assessor_version TEXT DEFAULT '1.0',
    created_at TEXT DEFAULT (datetime('now'))
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
    classification TEXT DEFAULT 'CUI',
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
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
    title TEXT NOT NULL,
    description TEXT,
    page_limit INTEGER,
    word_limit INTEGER,
    sort_order INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'not_started' CHECK(status IN (
        'not_started', 'in_progress', 'review', 'final')),
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
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
    updated_at TEXT DEFAULT (datetime('now'))
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
    created_at TEXT DEFAULT (datetime('now'))
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
    updated_at TEXT DEFAULT (datetime('now'))
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
        'pink_team', 'red_team', 'gold_team', 'white_glove', 'internal')),
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
    created_at TEXT DEFAULT (datetime('now'))
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
        'missing_content', 'other')),
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
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_prop_find_review ON proposal_review_findings(review_id);
CREATE INDEX IF NOT EXISTS idx_prop_find_section ON proposal_review_findings(section_id);
CREATE INDEX IF NOT EXISTS idx_prop_find_status ON proposal_review_findings(status);
CREATE INDEX IF NOT EXISTS idx_prop_find_severity ON proposal_review_findings(severity);

-- Status change audit trail (append-only — NIST AU)
CREATE TABLE IF NOT EXISTS proposal_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK(entity_type IN (
        'opportunity', 'volume', 'section', 'review', 'finding', 'compliance_item')),
    entity_id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_by TEXT,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_prop_hist_entity ON proposal_status_history(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_prop_hist_created ON proposal_status_history(created_at);
"""


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


def _has_migration_system(path):
    """Check if the database is managed by the migration framework (D150)."""
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(str(path))
        c = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        has_table = c.fetchone() is not None
        conn.close()
        return has_table
    except Exception:
        return False


def init_db(db_path=None):
    """Initialize the ICDEV database with full schema.

    If the migration system (schema_migrations table) is detected, redirects
    to the migration runner instead of re-running the monolithic init script.
    """
    path = db_path or DB_PATH

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
    conn.commit()
    conn.close()
    print(f"ICDEV database initialized at {path}")

    # Verify tables
    conn = sqlite3.connect(str(path))
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in c.fetchall()]
    conn.close()
    print(f"Tables created ({len(tables)}): {', '.join(tables)}")
    return tables


def main():
    parser = argparse.ArgumentParser(description="Initialize ICDEV database")
    parser.add_argument("--db-path", type=Path, default=DB_PATH, help="Database file path")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate all tables")
    args = parser.parse_args()

    if args.reset and args.db_path.exists():
        args.db_path.unlink()
        print(f"Removed existing database: {args.db_path}")

    init_db(args.db_path)


if __name__ == "__main__":
    main()
