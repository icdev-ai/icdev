#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""DB Init Generator - generates standalone database init scripts for child apps.

Decision D27: Minimal DB + migration. Core tables first, expand as capabilities activate.

Consumes a blueprint dict (from tools/builder/app_blueprint.py) and generates a
self-contained Python script that initializes the child app's SQLite database.
The generated script has zero ICDEV™ imports and creates only the tables needed
for the child app's enabled capabilities.

CLI:
    python tools/builder/db_init_generator.py \\
        --blueprint /path/to/blueprint.json \\
        --output-dir /path/to/output \\
        --json
"""

import argparse
import json
import logging
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
logger = get_logger("icdev.db_init_generator")

try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:

    def audit_log_event(**kwargs):
        logger.debug("audit_logger unavailable — skipping audit event")


# ============================================================
# TABLE DEFINITIONS (used to generate child app's init script)
# ============================================================
# Each dict maps table_name -> CREATE TABLE SQL.
# The SQL is standalone and uses CREATE TABLE IF NOT EXISTS
# so re-running is idempotent.

CORE_TABLES: Dict[str, str] = {
    "projects": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT,
            status TEXT DEFAULT 'active',
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "agents": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            agent_type TEXT NOT NULL,
            port INTEGER,
            status TEXT DEFAULT 'inactive',
            last_health_check TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "a2a_tasks": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS a2a_tasks (
            id TEXT PRIMARY KEY,
            source_agent TEXT,
            target_agent TEXT,
            task_type TEXT NOT NULL,
            payload TEXT,
            status TEXT DEFAULT 'pending',
            result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );"""),
    "audit_trail": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS audit_trail (
            id TEXT PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            event_type TEXT NOT NULL,
            actor TEXT,
            action TEXT NOT NULL,
            project_id TEXT,
            details TEXT,
            classification TEXT DEFAULT 'CUI'
        );"""),
    "knowledge_patterns": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS knowledge_patterns (
            id TEXT PRIMARY KEY,
            pattern_type TEXT NOT NULL,
            pattern_signature TEXT NOT NULL,
            description TEXT,
            solution TEXT,
            confidence REAL DEFAULT 0.0,
            occurrences INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "self_healing_events": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS self_healing_events (
            id TEXT PRIMARY KEY,
            pattern_id TEXT REFERENCES knowledge_patterns(id),
            trigger_type TEXT NOT NULL,
            action_taken TEXT,
            result TEXT,
            confidence REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "tasks": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            priority TEXT DEFAULT 'medium',
            assigned_agent TEXT,
            project_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );"""),
    "deployments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS deployments (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            environment TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            artifacts TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "metric_snapshots": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS metric_snapshots (
            id TEXT PRIMARY KEY,
            metric_type TEXT NOT NULL,
            metric_value REAL,
            project_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "alerts": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            alert_type TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            message TEXT,
            project_id TEXT,
            acknowledged INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "code_reviews": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS code_reviews (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            reviewer TEXT,
            status TEXT DEFAULT 'pending',
            findings TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "maintenance_audits": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS maintenance_audits (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            audit_type TEXT NOT NULL,
            score REAL,
            findings TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
}


COMPLIANCE_TABLES: Dict[str, str] = {
    "compliance_controls": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS compliance_controls (
            id TEXT PRIMARY KEY,
            family TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            impact_level TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "project_controls": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS project_controls (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            control_id TEXT NOT NULL REFERENCES compliance_controls(id),
            implementation_status TEXT DEFAULT 'planned',
            implementation_description TEXT,
            evidence_path TEXT,
            last_assessed TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, control_id)
        );"""),
    "ssp_documents": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ssp_documents (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            version TEXT NOT NULL,
            system_name TEXT NOT NULL,
            content TEXT NOT NULL,
            file_path TEXT,
            classification TEXT DEFAULT 'CUI',
            status TEXT DEFAULT 'draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "poam_items": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS poam_items (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            weakness_id TEXT NOT NULL,
            weakness_description TEXT NOT NULL,
            severity TEXT NOT NULL,
            control_id TEXT REFERENCES compliance_controls(id),
            status TEXT DEFAULT 'open',
            corrective_action TEXT,
            milestone_date DATE,
            responsible_party TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "stig_findings": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS stig_findings (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            stig_id TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'Open',
            assessed_by TEXT,
            assessed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "sbom_records": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS sbom_records (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            version TEXT NOT NULL,
            format TEXT DEFAULT 'cyclonedx',
            file_path TEXT NOT NULL,
            component_count INTEGER,
            vulnerability_count INTEGER,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "fedramp_assessments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS fedramp_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            baseline TEXT NOT NULL,
            control_id TEXT NOT NULL,
            status TEXT DEFAULT 'not_assessed',
            evidence_description TEXT,
            evidence_path TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, baseline, control_id)
        );"""),
    "cmmc_assessments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS cmmc_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            level INTEGER NOT NULL,
            practice_id TEXT NOT NULL,
            domain TEXT NOT NULL,
            status TEXT DEFAULT 'not_assessed',
            evidence_description TEXT,
            evidence_path TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, practice_id)
        );"""),
    "oscal_artifacts": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS oscal_artifacts (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            oscal_version TEXT DEFAULT '1.1.2',
            format TEXT DEFAULT 'json',
            file_path TEXT NOT NULL,
            file_hash TEXT,
            schema_valid INTEGER DEFAULT 0,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            classification TEXT DEFAULT 'CUI',
            UNIQUE(project_id, artifact_type, format)
        );"""),
    "cato_evidence": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS cato_evidence (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            control_id TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            evidence_source TEXT NOT NULL,
            evidence_path TEXT,
            evidence_hash TEXT,
            collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            is_fresh INTEGER DEFAULT 1,
            status TEXT DEFAULT 'current',
            UNIQUE(project_id, control_id, evidence_type, evidence_source)
        );"""),
    "cssp_assessments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS cssp_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            functional_area TEXT NOT NULL,
            requirement_id TEXT NOT NULL,
            status TEXT DEFAULT 'not_assessed',
            evidence_description TEXT,
            evidence_path TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, requirement_id)
        );"""),
    "ivv_assessments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ivv_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            process_area TEXT NOT NULL,
            verification_type TEXT NOT NULL,
            requirement_id TEXT NOT NULL,
            status TEXT DEFAULT 'not_assessed',
            evidence_description TEXT,
            evidence_path TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, requirement_id)
        );"""),
    "sbd_assessments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS sbd_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            domain TEXT NOT NULL,
            requirement_id TEXT NOT NULL,
            status TEXT DEFAULT 'not_assessed',
            evidence_description TEXT,
            evidence_path TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, requirement_id)
        );"""),
    "control_crosswalk": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS control_crosswalk (
            id TEXT PRIMARY KEY,
            nist_800_53_id TEXT NOT NULL,
            framework_id TEXT NOT NULL,
            framework_control_id TEXT NOT NULL,
            mapping_type TEXT DEFAULT 'equivalent',
            notes TEXT,
            UNIQUE(nist_800_53_id, framework_id)
        );"""),
    "pi_compliance_tracking": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS pi_compliance_tracking (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
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
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, pi_number)
        );"""),
}


MBSE_TABLES: Dict[str, str] = {
    "sysml_elements": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS sysml_elements (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            xmi_id TEXT NOT NULL,
            element_type TEXT NOT NULL,
            name TEXT NOT NULL,
            qualified_name TEXT,
            parent_id TEXT REFERENCES sysml_elements(id),
            stereotype TEXT,
            description TEXT,
            properties TEXT,
            source_file TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, xmi_id)
        );"""),
    "sysml_relationships": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS sysml_relationships (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            source_element_id TEXT NOT NULL REFERENCES sysml_elements(id),
            target_element_id TEXT NOT NULL REFERENCES sysml_elements(id),
            relationship_type TEXT NOT NULL,
            name TEXT,
            properties TEXT,
            source_file TEXT,
            UNIQUE(project_id, source_element_id, target_element_id, relationship_type)
        );"""),
    "doors_requirements": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS doors_requirements (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            doors_id TEXT NOT NULL,
            module_name TEXT,
            requirement_type TEXT,
            title TEXT NOT NULL,
            description TEXT,
            priority TEXT,
            status TEXT DEFAULT 'active',
            source_file TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, doors_id)
        );"""),
    "digital_thread_links": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS digital_thread_links (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            link_type TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            evidence TEXT,
            created_by TEXT DEFAULT 'system',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, source_type, source_id, target_type, target_id, link_type)
        );"""),
    "model_imports": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS model_imports (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            import_type TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            elements_imported INTEGER DEFAULT 0,
            relationships_imported INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            error_details TEXT,
            status TEXT DEFAULT 'completed',
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "model_snapshots": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS model_snapshots (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            pi_number TEXT,
            snapshot_type TEXT NOT NULL,
            element_count INTEGER DEFAULT 0,
            relationship_count INTEGER DEFAULT 0,
            requirement_count INTEGER DEFAULT 0,
            thread_link_count INTEGER DEFAULT 0,
            content_hash TEXT NOT NULL,
            snapshot_data TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, pi_number, snapshot_type)
        );"""),
    "model_code_mappings": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS model_code_mappings (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            sysml_element_id TEXT NOT NULL REFERENCES sysml_elements(id),
            code_path TEXT NOT NULL,
            code_type TEXT NOT NULL,
            mapping_direction TEXT DEFAULT 'model_to_code',
            sync_status TEXT DEFAULT 'synced',
            last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            model_hash TEXT,
            code_hash TEXT,
            UNIQUE(project_id, sysml_element_id, code_path)
        );"""),
    "des_compliance": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS des_compliance (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            requirement_id TEXT NOT NULL,
            requirement_title TEXT NOT NULL,
            category TEXT NOT NULL,
            status TEXT DEFAULT 'not_assessed',
            evidence TEXT,
            automation_result TEXT,
            assessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            UNIQUE(project_id, requirement_id)
        );"""),
}


# ============================================================
# D-CHILD-1: RICOAS TABLES
# ============================================================

RICOAS_TABLES: Dict[str, str] = {
    "intake_sessions": textwrap.dedent("""\
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
        );"""),
    "intake_requirements": textwrap.dedent("""\
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
        );"""),
    "safe_decomposition": textwrap.dedent("""\
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
        );"""),
    "readiness_scores": textwrap.dedent("""\
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
        );"""),
    "ato_system_registry": textwrap.dedent("""\
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
            impact_level TEXT CHECK(impact_level IN ('IL2', 'IL4', 'IL5', 'IL6')),
            data_types TEXT,
            interconnections TEXT,
            baseline_controls TEXT,
            component_inventory TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(project_id, system_name)
        );"""),
    "boundary_impact_assessments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS boundary_impact_assessments (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES intake_sessions(id),
            project_id TEXT NOT NULL REFERENCES projects(id),
            system_id TEXT NOT NULL REFERENCES ato_system_registry(id),
            requirement_id TEXT REFERENCES intake_requirements(id),
            impact_tier TEXT NOT NULL CHECK(impact_tier IN ('GREEN', 'YELLOW', 'ORANGE', 'RED')),
            impact_category TEXT NOT NULL,
            impact_description TEXT NOT NULL,
            affected_controls TEXT,
            affected_components TEXT,
            remediation_required TEXT,
            alternative_approach TEXT,
            risk_score REAL DEFAULT 0.0,
            assessed_by TEXT DEFAULT 'icdev-requirements-analyst',
            assessed_at TEXT DEFAULT (datetime('now'))
        );"""),
    "supply_chain_vendors": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS supply_chain_vendors (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id),
            vendor_name TEXT NOT NULL,
            vendor_type TEXT CHECK(vendor_type IN ('cots', 'gots', 'oss', 'saas', 'paas', 'iaas', 'contractor', 'subcontractor')),  # noqa: E501
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
        );"""),
    "supply_chain_dependencies": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS supply_chain_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL REFERENCES projects(id),
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            dependency_type TEXT NOT NULL,
            criticality TEXT DEFAULT 'medium'
                CHECK(criticality IN ('critical', 'high', 'medium', 'low')),
            isa_id TEXT,
            metadata TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );"""),
    "isa_agreements": textwrap.dedent("""\
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
            review_cadence_days INTEGER DEFAULT 365,
            next_review_date TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );"""),
    "scrm_assessments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS scrm_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id),
            vendor_id TEXT REFERENCES supply_chain_vendors(id),
            package_name TEXT,
            assessment_type TEXT NOT NULL
                CHECK(assessment_type IN ('vendor', 'component', 'aggregate', 'supply_chain_event')),
            risk_score REAL DEFAULT 0.0,
            likelihood TEXT CHECK(likelihood IN ('very_low', 'low', 'moderate', 'high', 'very_high')),
            impact TEXT CHECK(impact IN ('very_low', 'low', 'moderate', 'high', 'very_high')),
            mitigations TEXT,
            residual_risk TEXT CHECK(residual_risk IN ('low', 'moderate', 'high', 'critical')),
            assessed_by TEXT DEFAULT 'icdev-supply-chain-agent',
            assessed_at TEXT DEFAULT (datetime('now'))
        );"""),
    "cve_triage": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS cve_triage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL REFERENCES projects(id),
            cve_id TEXT NOT NULL,
            package_name TEXT NOT NULL,
            package_version TEXT,
            severity TEXT CHECK(severity IN ('critical', 'high', 'medium', 'low')),
            cvss_score REAL,
            triage_decision TEXT CHECK(triage_decision IN ('remediate', 'mitigate', 'accept_risk', 'defer', 'false_positive', 'not_applicable')),  # noqa: E501
            triage_rationale TEXT,
            sla_deadline TEXT,
            triaged_by TEXT,
            triaged_at TEXT DEFAULT (datetime('now')),
            remediated_at TEXT,
            UNIQUE(project_id, cve_id, package_name)
        );"""),
    "simulation_scenarios": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS simulation_scenarios (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id),
            session_id TEXT REFERENCES intake_sessions(id),
            scenario_name TEXT NOT NULL,
            scenario_type TEXT DEFAULT 'what_if'
                CHECK(scenario_type IN ('what_if', 'trade_study', 'risk_analysis', 'optimization', 'baseline')),
            modifications TEXT,
            status TEXT DEFAULT 'draft'
                CHECK(status IN ('draft', 'running', 'completed', 'failed', 'archived')),
            results TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );"""),
    "coa_records": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS coa_records (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES intake_sessions(id),
            project_id TEXT REFERENCES projects(id),
            coa_type TEXT NOT NULL
                CHECK(coa_type IN ('speed', 'balanced', 'comprehensive', 'alternative')),
            title TEXT NOT NULL,
            description TEXT,
            scope TEXT,
            estimated_pis TEXT,
            estimated_cost TEXT,
            risk_level TEXT CHECK(risk_level IN ('low', 'moderate', 'high', 'very_high')),
            simulation_results TEXT,
            selected INTEGER DEFAULT 0,
            selected_by TEXT,
            selection_rationale TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT (datetime('now'))
        );"""),
}


# ============================================================
# D-CHILD-1: AI SECURITY TABLES
# ============================================================

AI_SECURITY_TABLES: Dict[str, str] = {
    "prompt_injection_log": textwrap.dedent("""\
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
        );"""),
    "ai_telemetry": textwrap.dedent("""\
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
        );"""),
    "ai_bom": textwrap.dedent("""\
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
        );"""),
    "atlas_assessments": textwrap.dedent("""\
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
        );"""),
    "atlas_red_team_results": textwrap.dedent("""\
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
        );"""),
    "owasp_llm_assessments": textwrap.dedent("""\
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
        );"""),
}


# ============================================================
# D-CHILD-1: AI GOVERNANCE TABLES
# ============================================================

AI_GOVERNANCE_TABLES: Dict[str, str] = {
    "model_cards": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS model_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            model_name TEXT NOT NULL,
            card_data TEXT NOT NULL,
            card_hash TEXT,
            version INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(project_id, model_name, version)
        );"""),
    "system_cards": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS system_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            card_data TEXT NOT NULL,
            card_hash TEXT,
            version INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );"""),
    "ai_use_case_inventory": textwrap.dedent("""\
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
            last_assessed TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(project_id, name)
        );"""),
    "fairness_assessments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS fairness_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            assessment_data TEXT NOT NULL,
            overall_score REAL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now'))
        );"""),
    "ai_oversight_plans": textwrap.dedent("""\
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
        );"""),
    "ai_caio_registry": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ai_caio_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            official_name TEXT NOT NULL,
            official_role TEXT NOT NULL DEFAULT 'CAIO',
            organization TEXT,
            designation_date TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );"""),
    "ai_incident_log": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ai_incident_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            incident_type TEXT NOT NULL,
            ai_system TEXT,
            severity TEXT DEFAULT 'medium'
                CHECK(severity IN ('critical', 'high', 'medium', 'low')),
            description TEXT NOT NULL,
            corrective_action TEXT,
            status TEXT DEFAULT 'open'
                CHECK(status IN ('open', 'investigating', 'mitigated', 'resolved', 'closed')),
            reported_by TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );"""),
    "ai_ethics_reviews": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ai_ethics_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            review_type TEXT NOT NULL,
            ai_system TEXT,
            findings TEXT,
            opt_out_policy INTEGER DEFAULT 0,
            legal_compliance_matrix INTEGER DEFAULT 0,
            pre_deployment_review INTEGER DEFAULT 0,
            reviewer TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );"""),
    "ai_reassessment_schedule": textwrap.dedent("""\
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
        );"""),
}


# ============================================================
# D-CHILD-1: OBSERVABILITY & XAI TABLES
# ============================================================

OBSERVABILITY_TABLES: Dict[str, str] = {
    "otel_spans": textwrap.dedent("""\
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
        );"""),
    "prov_entities": textwrap.dedent("""\
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
        );"""),
    "prov_activities": textwrap.dedent("""\
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
        );"""),
    "prov_relations": textwrap.dedent("""\
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
        );"""),
    "shap_attributions": textwrap.dedent("""\
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
        );"""),
    "xai_assessments": textwrap.dedent("""\
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
        );"""),
}


# ============================================================
# D-CHILD-1: CODE INTELLIGENCE TABLES
# ============================================================

CODE_INTELLIGENCE_TABLES: Dict[str, str] = {
    "code_quality_metrics": textwrap.dedent("""\
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
        );"""),
    "runtime_feedback": textwrap.dedent("""\
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
        );"""),
}


# ============================================================
# D-CHILD-1: DEVSECOPS/ZTA TABLES
# ============================================================

DEVSECOPS_ZTA_TABLES: Dict[str, str] = {
    "devsecops_profiles": textwrap.dedent("""\
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
        );"""),
    "zta_maturity_scores": textwrap.dedent("""\
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
        );"""),
    "zta_posture_evidence": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS zta_posture_evidence (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id),
            evidence_type TEXT NOT NULL,
            evidence_data TEXT,
            status TEXT CHECK(status IN ('current', 'stale', 'expired', 'not_collected')),
            collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        );"""),
    "nist_800_207_assessments": textwrap.dedent("""\
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
        );"""),
    "devsecops_pipeline_audit": textwrap.dedent("""\
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
        );"""),
}


# ============================================================
# D-EPSEC-7: SECURITY FRAMEWORK TABLES
# ============================================================

SECURITY_TABLES: Dict[str, str] = {
    "security_policies": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS security_policies (
            id TEXT PRIMARY KEY,
            policy_type TEXT NOT NULL,
            classification TEXT DEFAULT 'CUI',
            clearance_ceiling TEXT DEFAULT 'SECRET',
            default_classification TEXT DEFAULT 'CUI',
            required_markings TEXT,
            mfa_required INTEGER DEFAULT 1,
            session_timeout_minutes INTEGER DEFAULT 30,
            encryption_at_rest INTEGER DEFAULT 1,
            encryption_in_transit INTEGER DEFAULT 1,
            immutable_audit INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(policy_type)
        );"""),
    "user_clearances": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS user_clearances (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            user_name TEXT,
            clearance_level TEXT NOT NULL DEFAULT 'CUI',
            effective_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            granted_by TEXT,
            project_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, project_id)
        );"""),
    "security_framework_status": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS security_framework_status (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            framework TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            inherited_from_parent INTEGER DEFAULT 1,
            last_verified TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, framework)
        );"""),
}


# ============================================================
# D-RAG-13: RAG TABLES (Phase 64)
# ============================================================

RAG_TABLES: Dict[str, str] = {
    "rag_chunks": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS rag_chunks (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            embedding BLOB,
            source_type TEXT NOT NULL,
            source_id TEXT,
            source_table TEXT,
            chunk_index INTEGER DEFAULT 0,
            total_chunks INTEGER DEFAULT 1,
            metadata TEXT DEFAULT '{}',
            tier TEXT DEFAULT 'hot' CHECK(tier IN ('hot', 'warm', 'cold')),
            tenant_id TEXT DEFAULT '',
            project_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "rag_ingestion_log": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS rag_ingestion_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_id TEXT,
            source_table TEXT,
            chunks_created INTEGER DEFAULT 0,
            chunks_skipped INTEGER DEFAULT 0,
            ingestion_mode TEXT DEFAULT 'batch',
            tenant_id TEXT DEFAULT '',
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "rag_retrieval_log": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS rag_retrieval_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_hash TEXT NOT NULL,
            results_count INTEGER DEFAULT 0,
            top_score REAL,
            rerank_used INTEGER DEFAULT 0,
            filters TEXT,
            agent_id TEXT DEFAULT '',
            duration_ms INTEGER,
            tenant_id TEXT DEFAULT '',
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "rag_parent_cache": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS rag_parent_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_hash TEXT UNIQUE,
            results TEXT,
            retrieved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            source TEXT DEFAULT 'parent'
        );"""),
    "rag_queries": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS rag_queries (
            id TEXT PRIMARY KEY,
            query_text TEXT NOT NULL,
            lens TEXT DEFAULT 'default',
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'running', 'done', 'failed')),
            agent_id TEXT,
            tenant_id TEXT DEFAULT '',
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );"""),
    "rag_citations": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS rag_citations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_id TEXT NOT NULL REFERENCES rag_queries(id),
            source_doc TEXT NOT NULL,
            citation_text TEXT,
            confidence REAL DEFAULT 0.0,
            tenant_id TEXT DEFAULT '',
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
}


# ============================================================
# Phase 61: ORCHESTRATION TABLES (ANVIL critique, prompt chains,
# dispatcher mode, session purpose)
# ============================================================

ORCHESTRATION_TABLES: Dict[str, str] = {
    "anvil_critique_sessions": textwrap.dedent("""\
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
        );"""),
    "anvil_critique_findings": textwrap.dedent("""\
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
        );"""),
    "prompt_chain_executions": textwrap.dedent("""\
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
        );"""),
    "dispatcher_mode_overrides": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS dispatcher_mode_overrides (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1,
            custom_dispatch_tools TEXT DEFAULT '[]',
            custom_blocked_tools TEXT DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            created_by TEXT NOT NULL DEFAULT 'system'
        );"""),
    "session_purposes": textwrap.dedent("""\
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
        );"""),
}


# ============================================================
# GENESIS TABLES (D-GEN-6, D-GEN-10)
# ============================================================

GENESIS_TABLES: Dict[str, str] = {
    "genesis_audit": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS genesis_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reflex TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            risk_tier TEXT DEFAULT 'GREEN'
                CHECK(risk_tier IN ('GREEN', 'YELLOW', 'ORANGE')),
            status TEXT DEFAULT 'completed'
                CHECK(status IN ('started', 'completed', 'failed', 'skipped')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "genesis_reflex_state": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS genesis_reflex_state (
            reflex TEXT PRIMARY KEY,
            enabled INTEGER DEFAULT 1,
            last_run TIMESTAMP,
            next_run TIMESTAMP,
            run_count INTEGER DEFAULT 0,
            consecutive_failures INTEGER DEFAULT 0,
            circuit_state TEXT DEFAULT 'closed'
                CHECK(circuit_state IN ('closed', 'open', 'half_open')),
            last_error TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "genesis_gkp": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS genesis_gkp (
            id TEXT PRIMARY KEY,
            reflex TEXT NOT NULL,
            gkp_type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            status TEXT DEFAULT 'pending_review'
                CHECK(status IN ('pending_review', 'auto_promoted', 'promoted',
                    'rejected', 'expired')),
            risk_tier TEXT DEFAULT 'GREEN',
            promoted_at TIMESTAMP,
            promoted_by TEXT,
            rejection_reason TEXT,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
}


# ============================================================
# KNOWLEDGE GRAPH TABLES (D-KARL-1 through D-KARL-4)
# ============================================================

KNOWLEDGE_GRAPH_TABLES: Dict[str, str] = {
    "kg_graphs": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS kg_graphs (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            name TEXT NOT NULL,
            description TEXT,
            entity_count INTEGER DEFAULT 0,
            edge_count INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "kg_nodes": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            label TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            properties TEXT DEFAULT '{}',
            embedding BLOB,
            centrality REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "kg_edges": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS kg_edges (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            source_id TEXT NOT NULL REFERENCES kg_nodes(id),
            target_id TEXT NOT NULL REFERENCES kg_nodes(id),
            relationship TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            properties TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "kg_retrieval_log": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS kg_retrieval_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            query TEXT NOT NULL,
            query_hash TEXT NOT NULL,
            profile TEXT DEFAULT 'exploratory',
            nodes_returned INTEGER DEFAULT 0,
            edges_returned INTEGER DEFAULT 0,
            compression_applied INTEGER DEFAULT 0,
            retrieval_ms INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
}


# ============================================================
# FINE-TUNING TABLES (D-FT-1 through D-FT-22)
# ============================================================

FINE_TUNING_TABLES: Dict[str, str] = {
    "ft_datasets": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ft_datasets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            purpose TEXT DEFAULT 'general',
            version INTEGER DEFAULT 1,
            example_count INTEGER DEFAULT 0,
            content_hash TEXT,
            status TEXT DEFAULT 'active'
                CHECK(status IN ('active', 'archived', 'deleted')),
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "ft_dataset_examples": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ft_dataset_examples (
            id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL REFERENCES ft_datasets(id),
            input_text TEXT NOT NULL,
            output_text TEXT NOT NULL,
            source_type TEXT,
            source_id TEXT,
            quality_score REAL,
            compliance_score REAL,
            relevance_score REAL,
            label TEXT DEFAULT 'unlabeled'
                CHECK(label IN ('unlabeled', 'approved', 'rejected', 'needs_review')),
            labeled_by TEXT,
            labeled_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "ft_training_jobs": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ft_training_jobs (
            id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL REFERENCES ft_datasets(id),
            provider TEXT NOT NULL,
            base_model TEXT NOT NULL,
            status TEXT DEFAULT 'pending'
                CHECK(status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
            hyperparams TEXT DEFAULT '{}',
            metrics TEXT DEFAULT '{}',
            error_message TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "ft_training_job_events": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ft_training_job_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES ft_training_jobs(id),
            event_type TEXT NOT NULL,
            detail TEXT,
            metrics TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "ft_model_versions": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ft_model_versions (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES ft_training_jobs(id),
            model_name TEXT NOT NULL,
            version INTEGER DEFAULT 1,
            adapter_path TEXT,
            gguf_path TEXT,
            eval_scores TEXT DEFAULT '{}',
            status TEXT DEFAULT 'created'
                CHECK(status IN ('created', 'evaluating', 'promoted', 'deprecated', 'archived')),
            promoted_at TIMESTAMP,
            promoted_function TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "ft_active_models": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ft_active_models (
            id TEXT PRIMARY KEY,
            function_name TEXT NOT NULL UNIQUE,
            model_version_id TEXT NOT NULL REFERENCES ft_model_versions(id),
            ollama_model_tag TEXT,
            activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            activated_by TEXT DEFAULT 'system'
        );"""),
    "ft_evaluations": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ft_evaluations (
            id TEXT PRIMARY KEY,
            model_version_id TEXT NOT NULL REFERENCES ft_model_versions(id),
            eval_type TEXT DEFAULT 'standard'
                CHECK(eval_type IN ('standard', 'ab_comparison', 'regression')),
            test_count INTEGER DEFAULT 0,
            bleu_score REAL,
            rouge_l_score REAL,
            perplexity REAL,
            custom_metrics TEXT DEFAULT '{}',
            passed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "ft_promotion_log": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ft_promotion_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_version_id TEXT NOT NULL REFERENCES ft_model_versions(id),
            action TEXT NOT NULL,
            function_name TEXT,
            previous_model_id TEXT,
            reason TEXT,
            promoted_by TEXT DEFAULT 'system',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
    "ft_hyperparam_results": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ft_hyperparam_results (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES ft_training_jobs(id),
            search_type TEXT DEFAULT 'grid'
                CHECK(search_type IN ('grid', 'random')),
            hyperparams TEXT NOT NULL,
            eval_score REAL,
            is_best INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
}


# ============================================================
# CAPABILITY → TABLE GROUP MAPPING
# ============================================================

CAPABILITY_TABLE_MAP: Dict[str, Dict[str, str]] = {
    "compliance": COMPLIANCE_TABLES,
    "mbse": MBSE_TABLES,
    # D-CHILD-1: Enterprise capability table groups
    "ricoas": RICOAS_TABLES,
    "supply_chain": RICOAS_TABLES,  # Supply chain uses RICOAS tables (shared schema)
    "simulation": RICOAS_TABLES,  # Simulation uses RICOAS tables (shared schema)
    "ai_security": AI_SECURITY_TABLES,
    "ai_governance": AI_GOVERNANCE_TABLES,
    "observability": OBSERVABILITY_TABLES,
    "code_intelligence": CODE_INTELLIGENCE_TABLES,
    "devsecops_zta": DEVSECOPS_ZTA_TABLES,
    # D-EPSEC-7: Security framework tables (always available)
    "security": SECURITY_TABLES,
    # D-RAG-13: RAG tables (Phase 64)
    "rag": RAG_TABLES,
    # D-FT-19: Fine-tuning tables (Phase 64 Extension)
    "fine_tuning": FINE_TUNING_TABLES,
    # D-GEN-6: Genesis tables
    "genesis": GENESIS_TABLES,
    # D-KARL-1: Knowledge Graph tables
    "knowledge_graph": KNOWLEDGE_GRAPH_TABLES,
    # Phase 61: Orchestration tables (always included)
    "orchestration": ORCHESTRATION_TABLES,
}


# ============================================================
# HELPER FUNCTIONS
# ============================================================


def _sanitize_name(name: str) -> str:
    """Sanitize app name for use as a Python identifier and filename."""
    return re.sub(r"[^a-z0-9_]", "_", name.lower().replace("-", "_")).strip("_")


def _build_sql_block(tables: Dict[str, str], block_comment: str) -> str:
    """Join table DDL statements into a single SQL string with a section comment."""
    lines = [f"-- {'=' * 60}", f"-- {block_comment}", f"-- {'=' * 60}"]
    for _table_name, ddl in tables.items():
        lines.append(ddl)
        lines.append("")
    return "\n".join(lines)


def _indent(text: str, prefix: str = "    ") -> str:
    """Indent every line of *text* by *prefix*."""
    return "\n".join(prefix + line if line.strip() else line for line in text.splitlines())


# ============================================================
# MAIN GENERATOR
# ============================================================


def generate_init_script(blueprint: Dict[str, Any]) -> str:
    """Generate a complete, standalone Python init script for a child app.

    Args:
        blueprint: Blueprint dict produced by app_blueprint.py.  Expected keys:
            - app_name (str)
            - classification (str, e.g. 'CUI')
            - capabilities (dict[str, bool])

    Returns:
        The full Python source code of the generated init script.
    """
    app_name: str = blueprint.get("app_name", "child_app")
    classification: str = blueprint.get("classification", "CUI")
    capabilities: Dict[str, bool] = blueprint.get("capabilities", {})
    safe_name = _sanitize_name(app_name)

    # --- Determine which capability SQL blocks to include -----------------
    enabled_caps: List[str] = sorted(
        cap for cap, enabled in capabilities.items() if enabled and cap in CAPABILITY_TABLE_MAP
    )

    # --- Build the SQL constant strings that will live in the generated file
    core_sql = _build_sql_block(CORE_TABLES, "CORE TABLES")

    capability_sql_constants: List[str] = []  # Python source fragments
    capability_init_calls: List[str] = []  # Lines inside init_db()
    migrate_cases: List[str] = []  # Cases for migrate_add_capability()

    for cap_name in CAPABILITY_TABLE_MAP:
        var_name = f"{cap_name.upper()}_SQL"
        sql_block = _build_sql_block(CAPABILITY_TABLE_MAP[cap_name], f"{cap_name.upper()} TABLES")
        # Always emit the constant so migrate_add_capability can reference it
        capability_sql_constants.append(f'{var_name} = """\n{sql_block}\n"""')
        migrate_cases.append(f'    "{cap_name}": {var_name},')
        # Only call it in init_db if this capability is currently enabled
        if cap_name in enabled_caps:
            capability_init_calls.append(f"    conn.executescript({var_name})")

    capability_constants_src = "\n\n".join(capability_sql_constants)
    "\n".join(
        capability_init_calls
    ) if capability_init_calls else "    pass  # No optional capabilities enabled at init time"
    migrate_map_src = "\n".join(migrate_cases) if migrate_cases else "    # No optional table groups defined"

    # --- Enabled capabilities comment for the header ----------------------
    caps_comment = ", ".join(enabled_caps) if enabled_caps else "none"

    # --- Classification banner --------------------------------------------
    if classification == "SECRET":
        cui_banner = (
            "# SECRET // NOFORN\n# Classified by: Department of Defense\n# Reason: 1.4(c)\n# Declassify on: 25X1"
        )
    else:
        cui_banner = (
            f"# {classification} // SP-CTI\n"
            "# Controlled by: Department of Defense\n"
            "# CUI Category: CTI\n"
            "# Distribution: D\n"
            "# POC: System Administrator"
        )

    # --- Assemble the generated script ------------------------------------
    generated_at = datetime.now(tz=timezone.utc).isoformat()
    cap_names_literal = repr(list(CAPABILITY_TABLE_MAP.keys()))

    parts: List[str] = []
    parts.append("#!/usr/bin/env python3")
    parts.append(cui_banner)
    parts.append(f'"""Initialize the {app_name} database.')
    parts.append("")
    parts.append(f"Auto-generated by ICDEV™ db_init_generator on {generated_at}.")
    parts.append("Decision D27: Minimal DB + migration -- core tables first, expand as capabilities activate.")
    parts.append("")
    parts.append(f"Enabled capabilities at generation time: {caps_comment}")
    parts.append("")
    parts.append("Usage:")
    parts.append(f"    python init_{safe_name}_db.py [--db-path DATA/{safe_name}.db] [--reset]")
    parts.append('"""')
    parts.append("")
    parts.append("import argparse")
    parts.append("import sys")
    parts.append("from pathlib import Path")
    parts.append("")
    parts.append(f'DB_PATH = Path(__file__).resolve().parent / "data" / "{safe_name}.db"')
    parts.append("")
    parts.append("")
    # PG-portable connection helper (PGP project): PostgreSQL-primary via the
    # vendored ICDEV storage layer, with an init-only SQLite fallback. Passing
    # this child's own .db path keeps get_connection() on SQLite for that file
    # with RLS skipped (child tables carry no tenant_id/classification columns),
    # while executescript() / sqlite_master verification are handled portably by
    # the StorageConnection wrapper (translate_sql). Degrades to a direct sqlite3
    # connection when the storage layer is not vendored (standalone child).
    parts.append("def _get_db_connection(db_path):")
    parts.append('    """Backend-agnostic connection (PG-primary, SQLite init-fallback)."""')
    parts.append("    try:")
    parts.append("        from tools.db.storage import get_connection")
    parts.append("        conn = get_connection(str(db_path))")
    parts.append("        # No RLS predicate on a child app's own tables. The global")
    parts.append("        # security context injects tenant_id/classification filters and")
    parts.append("        # these tables have neither column, so every query would raise")
    parts.append("        # UndefinedColumn — the same reason canvases use")
    parts.append("        # get_canvas_connection().")
    parts.append("        try:")
    # This is emitted source, not a bypass taken here: the generated child app detaches
    # the global predicate from its own tables, which carry no tenant_id/classification
    # column — the reason is spelled out in the comment block emitted directly above.
    parts.append("            conn.set_security_context(None)")  # rls-bypass: emitted source, reason emitted above — required for task-kax-conflict-02, which found this gate red on main
    parts.append("        except AttributeError:")
    parts.append("            pass  # bare DBAPI connection: nothing to detach")
    parts.append("        return conn")
    parts.append("    except Exception:")
    parts.append("        import sqlite3")
    parts.append("        return sqlite3.connect(str(db_path))  # pg-ok: guarded standalone fallback")
    parts.append("")
    parts.append("")
    parts.append("# " + "-" * 60)
    parts.append("# PATTERN: Define CHECK constraint values as Python constants")
    parts.append("# so SQL and Python stay in sync.  Example:")
    parts.append("#")
    parts.append("#   ENTITY_TYPES = ('person', 'organization', 'location')")
    parts.append("#   _entity_check = ','.join(repr(t) for t in ENTITY_TYPES)")
    parts.append("#")
    parts.append("#   Then in SQL:")
    parts.append("#   CHECK (entity_type IN ({_entity_check}))")
    parts.append("#")
    parts.append("# This avoids CHECK constraint mismatches when adding new types.")
    parts.append("# " + "-" * 60)
    parts.append("")
    parts.append("")
    parts.append("# " + "=" * 60)
    parts.append("# CORE SQL -- always created")
    parts.append("# " + "=" * 60)
    parts.append(f'CORE_SQL = """\n{core_sql}\n"""')
    parts.append("")
    parts.append("")
    parts.append("# " + "=" * 60)
    parts.append("# OPTIONAL CAPABILITY SQL BLOCKS")
    parts.append("# " + "=" * 60)
    parts.append(capability_constants_src)
    parts.append("")
    parts.append("")
    parts.append("# Mapping from capability name to SQL constant")
    parts.append("_CAPABILITY_SQL_MAP = {")
    parts.append(migrate_map_src)
    parts.append("}")
    parts.append("")
    parts.append("")

    # SCHEMA_SQL constant for test imports (BDD environment.py, conftest.py)
    parts.append("# Combined SQL for test setup (import this in features/environment.py)")
    if capability_init_calls:
        cap_refs = " + ".join(
            call_line.strip().replace("conn.executescript(", "").rstrip(")") for call_line in capability_init_calls
        )
        parts.append(f"SCHEMA_SQL = CORE_SQL + {cap_refs}")
    else:
        parts.append("SCHEMA_SQL = CORE_SQL")
    parts.append("")
    parts.append("")

    # init_db function
    parts.append("def init_db(db_path=None):")
    parts.append(f'    """Initialize the {app_name} database with core + enabled capability tables."""')
    parts.append("    path = Path(db_path) if db_path else DB_PATH")
    parts.append("    path.parent.mkdir(parents=True, exist_ok=True)")
    parts.append("")
    parts.append("    conn = _get_db_connection(path)")
    parts.append("    try:")
    parts.append("        # Core tables -- always present")
    parts.append("        conn.executescript(CORE_SQL)")
    parts.append("")
    parts.append("        # Capability tables enabled at generation time")
    if capability_init_calls:
        for call_line in capability_init_calls:
            parts.append(f"        {call_line.strip()}")
    else:
        parts.append("        pass  # No optional capabilities enabled at init time")
    parts.append("")
    parts.append("        conn.commit()")
    parts.append("    finally:")
    parts.append("        conn.close()")
    parts.append("")
    parts.append("    # Verify — backend-aware table listing: prefer the vendored ICDEV")
    parts.append("    # helper (works on PostgreSQL and SQLite, and is translation-independent")
    parts.append("    # so it does not depend on cursor-level placeholder handling); fall back")
    parts.append("    # to a direct catalog probe only when running standalone.")
    parts.append("    conn = _get_db_connection(path)")
    parts.append("    try:")
    parts.append("        try:")
    parts.append("            from tools.db.storage import list_tables")
    parts.append("            tables = list_tables(conn)")
    parts.append("        except Exception:")
    parts.append("            cur = conn.cursor()")
    parts.append("            cur.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")")
    parts.append("            tables = [row[0] for row in cur.fetchall()]")
    parts.append("    finally:")
    parts.append("        conn.close()")
    parts.append("")
    parts.append('    print(f"Database initialized at {path}")')
    parts.append("    print(f\"Tables created ({len(tables)}): {', '.join(tables)}\")")
    parts.append("    return tables")
    parts.append("")
    parts.append("")

    # migrate_add_capability function
    parts.append("def migrate_add_capability(db_path, capability_name):")
    parts.append('    """Add tables for a capability that was not enabled at init time.')
    parts.append("")
    parts.append("    Args:")
    parts.append("        db_path: Path to the SQLite database file.")
    parts.append(f"        capability_name: One of {cap_names_literal}.")
    parts.append("")
    parts.append("    Raises:")
    parts.append("        ValueError: If capability_name is not recognized.")
    parts.append('    """')
    parts.append("    if capability_name not in _CAPABILITY_SQL_MAP:")
    parts.append("        raise ValueError(")
    parts.append("            f\"Unknown capability '{capability_name}'. \"")
    parts.append('            f"Valid options: {list(_CAPABILITY_SQL_MAP.keys())}"')
    parts.append("        )")
    parts.append("")
    parts.append("    path = Path(db_path)")
    parts.append("    if not path.exists():")
    parts.append('        raise FileNotFoundError(f"Database not found: {path}")')
    parts.append("")
    parts.append("    sql = _CAPABILITY_SQL_MAP[capability_name]")
    parts.append("    conn = _get_db_connection(path)")
    parts.append("    try:")
    parts.append("        conn.executescript(sql)")
    parts.append("        conn.commit()")
    parts.append("    finally:")
    parts.append("        conn.close()")
    parts.append("")
    parts.append("    # Verify new tables — backend-aware listing (prefer vendored helper,")
    parts.append("    # fall back to a direct catalog probe when standalone).")
    parts.append("    conn = _get_db_connection(path)")
    parts.append("    try:")
    parts.append("        try:")
    parts.append("            from tools.db.storage import list_tables")
    parts.append("            tables = list_tables(conn)")
    parts.append("        except Exception:")
    parts.append("            cur = conn.cursor()")
    parts.append("            cur.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")")
    parts.append("            tables = [row[0] for row in cur.fetchall()]")
    parts.append("    finally:")
    parts.append("        conn.close()")
    parts.append("")
    parts.append("    print(f\"Capability '{capability_name}' tables added to {path}\")")
    parts.append("    print(f\"Total tables ({len(tables)}): {', '.join(tables)}\")")
    parts.append("    return tables")
    parts.append("")
    parts.append("")

    # main function
    parts.append("def main():")
    parts.append('    """CLI entry point."""')
    parts.append("    parser = argparse.ArgumentParser(")
    parts.append(f'        description="Initialize the {app_name} database"')
    parts.append("    )")
    parts.append("    parser.add_argument(")
    parts.append('        "--db-path", type=Path, default=DB_PATH,')
    parts.append('        help="Database file path (default: %(default)s)"')
    parts.append("    )")
    parts.append("    parser.add_argument(")
    parts.append('        "--reset", action="store_true",')
    parts.append('        help="Drop and recreate all tables"')
    parts.append("    )")
    parts.append("    parser.add_argument(")
    parts.append('        "--add-capability", type=str, default=None,')
    parts.append("        help=\"Add tables for a capability post-init (e.g. 'compliance', 'mbse')\"")
    parts.append("    )")
    parts.append("    args = parser.parse_args()")
    parts.append("")
    parts.append("    if args.add_capability:")
    parts.append("        migrate_add_capability(args.db_path, args.add_capability)")
    parts.append("        return")
    parts.append("")
    parts.append("    if args.reset and args.db_path.exists():")
    parts.append("        args.db_path.unlink()")
    parts.append('        print(f"Removed existing database: {args.db_path}")')
    parts.append("")
    parts.append("    init_db(args.db_path)")
    parts.append("")
    parts.append("")
    parts.append('if __name__ == "__main__":')
    parts.append("    main()")
    parts.append("")

    script = "\n".join(parts)

    return script


def write_init_script(blueprint: Dict[str, Any], output_dir: Path) -> Path:
    """Generate the init script and write it to *output_dir*.

    Args:
        blueprint: Blueprint dict from app_blueprint.py.
        output_dir: Directory where the generated script will be placed.

    Returns:
        Path to the written file.
    """
    app_name: str = blueprint.get("app_name", "child_app")
    safe_name = _sanitize_name(app_name)
    filename = f"init_{safe_name}_db.py"

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename

    source = generate_init_script(blueprint)
    output_path.write_text(source, encoding="utf-8", newline="")

    logger.info("Wrote init script: %s (%d bytes)", output_path, len(source))

    # Audit trail
    audit_log_event(
        event_type="code_generated",
        actor="icdev-db-init-generator",
        action=f"Generated DB init script for {app_name}",
        details=json.dumps(
            {
                "app_name": app_name,
                "output_path": str(output_path),
                "capabilities": {k: v for k, v in blueprint.get("capabilities", {}).items() if v},
                "classification": blueprint.get("classification", "CUI"),
            }
        ),
        project_id=blueprint.get("blueprint_id", "unknown"),
    )

    return output_path


# ============================================================
# CLI ENTRY POINT
# ============================================================


def main():
    """CLI entry point for the DB init generator."""
    parser = argparse.ArgumentParser(description="Generate a standalone database init script for a child app")
    parser.add_argument(
        "--blueprint", required=True, type=Path, help="Path to blueprint JSON file (from app_blueprint.py)"
    )
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory to write the generated init script")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output result as JSON")
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Load blueprint
    if not args.blueprint.exists():
        logger.error("Blueprint file not found: %s", args.blueprint)
        sys.exit(1)

    try:
        blueprint = json.loads(args.blueprint.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load blueprint: %s", exc)
        sys.exit(1)

    # Generate and write
    output_path = write_init_script(blueprint, args.output_dir)

    # Determine enabled capabilities for summary
    capabilities = blueprint.get("capabilities", {})
    enabled = sorted(k for k, v in capabilities.items() if v and k in CAPABILITY_TABLE_MAP)
    core_count = len(CORE_TABLES)
    cap_count = sum(len(CAPABILITY_TABLE_MAP[c]) for c in enabled)
    total_tables = core_count + cap_count

    result = {
        "status": "success",
        "output_path": str(output_path),
        "app_name": blueprint.get("app_name", "child_app"),
        "classification": blueprint.get("classification", "CUI"),
        "core_tables": core_count,
        "capability_tables": cap_count,
        "total_tables": total_tables,
        "enabled_capabilities": enabled,
        "available_migrations": sorted(CAPABILITY_TABLE_MAP.keys()),
    }

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(f"Generated: {output_path}")
        print(f"  App:            {result['app_name']}")
        print(f"  Classification: {result['classification']}")
        print(f"  Core tables:    {core_count}")
        print(f"  Cap tables:     {cap_count} ({', '.join(enabled) if enabled else 'none'})")
        print(f"  Total tables:   {total_tables}")
        print(f"  Migrations:     {', '.join(sorted(CAPABILITY_TABLE_MAP.keys()))}")


if __name__ == "__main__":
    main()
