"""ICDEV™ Studio — Workflow Editor Backend.

CRUD operations for user-created workflows.  Visual DAG editor frontend
serialises to the same YAML format as args/workflow_templates/*.yaml so
workflows can be executed by tools/orchestration/workflow_composer.py.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

# ── Tool catalog ───────────────────────────────────────────

TOOL_CATEGORIES: dict[str, dict[str, Any]] = {
    # ── 1. Compliance & ATO ────────────────────────────────
    "compliance": {
        "label": "Compliance & ATO",
        "icon": "shield",
        "color": "compliance",
        "tools": [
            {
                "id": "fips199_categorizer",
                "name": "FIPS 199 Categorize",
                "tool": "tools/compliance/fips199_categorizer.py",
                "description": "Security categorization per FIPS 199 / SP 800-60",
            },
            {
                "id": "fips200_validator",
                "name": "FIPS 200 Validate",
                "tool": "tools/compliance/fips200_validator.py",
                "description": "FIPS 200 minimum security requirements validation",
            },
            {
                "id": "multi_regime_assessor",
                "name": "Multi-Framework Assess",
                "tool": "tools/compliance/multi_regime_assessor.py",
                "description": "Assess against all compliance frameworks",
            },
            {
                "id": "compliance_detector",
                "name": "Framework Detect",
                "tool": "tools/compliance/compliance_detector.py",
                "description": "Auto-detect applicable compliance frameworks",
            },
            {
                "id": "ssp_generator",
                "name": "Generate SSP",
                "tool": "tools/compliance/ssp_generator.py",
                "description": "System Security Plan generation",
            },
            {
                "id": "poam_generator",
                "name": "Generate POA&M",
                "tool": "tools/compliance/poam_generator.py",
                "description": "Plan of Action & Milestones",
            },
            {
                "id": "stig_checker",
                "name": "STIG Check",
                "tool": "tools/compliance/stig_checker.py",
                "description": "DISA STIG compliance check",
            },
            {
                "id": "oscal_generator",
                "name": "OSCAL Export",
                "tool": "tools/compliance/oscal_generator.py",
                "description": "OSCAL-format compliance export",
            },
            {
                "id": "oscal_tools",
                "name": "OSCAL Tools",
                "tool": "tools/compliance/oscal_tools.py",
                "description": "OSCAL validation, conversion, profile resolution",
            },
            {
                "id": "sbom_generator",
                "name": "Generate SBOM",
                "tool": "tools/compliance/sbom_generator.py",
                "description": "Software Bill of Materials (CycloneDX)",
            },
            {
                "id": "slsa_attestation",
                "name": "SLSA Attestation",
                "tool": "tools/compliance/slsa_attestation_generator.py",
                "description": "SLSA v1.0 provenance attestation",
            },
            {
                "id": "swft_bundler",
                "name": "SWFT Evidence Bundle",
                "tool": "tools/compliance/swft_evidence_bundler.py",
                "description": "DoD SWFT evidence package (SLSA+SBOM+VEX)",
            },
            {
                "id": "cmmc_assessor",
                "name": "CMMC Assess",
                "tool": "tools/compliance/cmmc_assessor.py",
                "description": "CMMC Level 2/3 assessment",
            },
            {
                "id": "fedramp_assessor",
                "name": "FedRAMP Assess",
                "tool": "tools/compliance/fedramp_assessor.py",
                "description": "FedRAMP Moderate/High assessment",
            },
            {
                "id": "fedramp_ksi",
                "name": "FedRAMP 20x KSI",
                "tool": "tools/compliance/fedramp_ksi_generator.py",
                "description": "FedRAMP 20x Key Security Indicators",
            },
            {
                "id": "fedramp_packager",
                "name": "FedRAMP Package",
                "tool": "tools/compliance/fedramp_authorization_packager.py",
                "description": "FedRAMP 20x authorization package",
            },
            {
                "id": "cjis_assessor",
                "name": "CJIS Assess",
                "tool": "tools/compliance/cjis_assessor.py",
                "description": "FBI CJIS Security Policy assessment",
            },
            {
                "id": "hipaa_assessor",
                "name": "HIPAA Assess",
                "tool": "tools/compliance/hipaa_assessor.py",
                "description": "HIPAA Security Rule assessment",
            },
            {
                "id": "hitrust_assessor",
                "name": "HITRUST Assess",
                "tool": "tools/compliance/hitrust_assessor.py",
                "description": "HITRUST CSF v11 assessment",
            },
            {
                "id": "soc2_assessor",
                "name": "SOC 2 Assess",
                "tool": "tools/compliance/soc2_assessor.py",
                "description": "SOC 2 Type II Trust Service Criteria",
            },
            {
                "id": "pci_dss_assessor",
                "name": "PCI DSS Assess",
                "tool": "tools/compliance/pci_dss_assessor.py",
                "description": "PCI DSS v4.0 assessment",
            },
            {
                "id": "iso27001_assessor",
                "name": "ISO 27001 Assess",
                "tool": "tools/compliance/iso27001_assessor.py",
                "description": "ISO/IEC 27001:2022 assessment",
            },
            {
                "id": "crosswalk_engine",
                "name": "Crosswalk Engine",
                "tool": "tools/compliance/crosswalk_engine.py",
                "description": "Dual-hub crosswalk across 10+ frameworks",
            },
            {
                "id": "cato_monitor",
                "name": "cATO Monitor",
                "tool": "tools/compliance/cato_monitor.py",
                "description": "Continuous ATO evidence freshness monitoring",
            },
            {
                "id": "cato_scheduler",
                "name": "cATO Scheduler",
                "tool": "tools/compliance/cato_scheduler.py",
                "description": "Schedule-based evidence collection",
            },
            {
                "id": "evidence_collector",
                "name": "Evidence Collector",
                "tool": "tools/compliance/evidence_collector.py",
                "description": "Universal evidence auto-collection (14 frameworks)",
            },
            {
                "id": "narrative_generator",
                "name": "Narrative Generator",
                "tool": "tools/compliance/narrative_generator.py",
                "description": "Compliance narrative workflow",
            },
            {
                "id": "compliance_exporter",
                "name": "Compliance Export",
                "tool": "tools/compliance/compliance_exporter.py",
                "description": "Multi-format compliance artifact export",
            },
            {
                "id": "cssp_assessor",
                "name": "CSSP Assess",
                "tool": "tools/compliance/cssp_assessor.py",
                "description": "DI 8530.01 CSSP assessment (5 areas)",
            },
            {
                "id": "sbd_assessor",
                "name": "Secure by Design",
                "tool": "tools/compliance/sbd_assessor.py",
                "description": "CISA Secure by Design assessment",
            },
            {
                "id": "ivv_assessor",
                "name": "IV&V Assess",
                "tool": "tools/compliance/ivv_assessor.py",
                "description": "IEEE 1012 Independent V&V assessment",
            },
            {
                "id": "mosa_assessor",
                "name": "MOSA Assess",
                "tool": "tools/compliance/mosa_assessor.py",
                "description": "DoD MOSA compliance assessment",
            },
            {
                "id": "eu_ai_act",
                "name": "EU AI Act Classify",
                "tool": "tools/compliance/eu_ai_act_classifier.py",
                "description": "EU AI Act risk classification",
            },
            {
                "id": "nist_800_207",
                "name": "NIST 800-207 ZTA",
                "tool": "tools/compliance/nist_800_207_assessor.py",
                "description": "NIST SP 800-207 ZTA compliance",
            },
            {
                "id": "production_audit",
                "name": "Production Audit",
                "tool": "tools/testing/production_audit.py",
                "description": "30-check pre-production readiness audit",
            },
        ],
    },
    # ── 2. AI Security & Trust ─────────────────────────────
    "ai_security": {
        "label": "AI Security & Trust",
        "icon": "brain",
        "color": "ai_security",
        "tools": [
            {
                "id": "atlas_assessor",
                "name": "MITRE ATLAS Assess",
                "tool": "tools/compliance/atlas_assessor.py",
                "description": "MITRE ATLAS v5.4.0 compliance (34 mitigations)",
            },
            {
                "id": "owasp_llm",
                "name": "OWASP LLM Top 10",
                "tool": "tools/compliance/owasp_llm_assessor.py",
                "description": "OWASP LLM Top 10 v2025 assessment",
            },
            {
                "id": "owasp_agentic",
                "name": "OWASP Agentic",
                "tool": "tools/compliance/owasp_agentic_assessor.py",
                "description": "OWASP Agentic AI security (17 checks)",
            },
            {
                "id": "owasp_asi",
                "name": "OWASP ASI",
                "tool": "tools/compliance/owasp_asi_assessor.py",
                "description": "OWASP ASI01-ASI10 agentic risk assessment",
            },
            {
                "id": "nist_ai_rmf",
                "name": "NIST AI RMF",
                "tool": "tools/compliance/nist_ai_rmf_assessor.py",
                "description": "NIST AI RMF 1.0 (Govern/Map/Measure/Manage)",
            },
            {
                "id": "nist_ai_600_1",
                "name": "NIST AI 600-1",
                "tool": "tools/compliance/nist_ai_600_1_assessor.py",
                "description": "NIST AI 600-1 GenAI Profile assessment",
            },
            {
                "id": "iso42001_assessor",
                "name": "ISO 42001",
                "tool": "tools/compliance/iso42001_assessor.py",
                "description": "ISO/IEC 42001:2023 AI Management System",
            },
            {
                "id": "xai_assessor",
                "name": "XAI Assess",
                "tool": "tools/compliance/xai_assessor.py",
                "description": "Explainable AI compliance (10 checks)",
            },
            {
                "id": "fairness_assessor",
                "name": "Fairness Assess",
                "tool": "tools/compliance/fairness_assessor.py",
                "description": "AI fairness compliance assessment",
            },
            {
                "id": "gao_ai_assessor",
                "name": "GAO AI Assess",
                "tool": "tools/compliance/gao_ai_assessor.py",
                "description": "GAO-21-519SP AI accountability",
            },
            {
                "id": "omb_m25_21",
                "name": "OMB M-25-21",
                "tool": "tools/compliance/omb_m25_21_assessor.py",
                "description": "OMB High-Impact AI assessment",
            },
            {
                "id": "omb_m26_04",
                "name": "OMB M-26-04",
                "tool": "tools/compliance/omb_m26_04_assessor.py",
                "description": "OMB Unbiased AI assessment",
            },
            {
                "id": "prompt_injection_detector",
                "name": "Prompt Injection Scan",
                "tool": "tools/security/prompt_injection_detector.py",
                "description": "5-category prompt injection detection",
            },
            {
                "id": "ai_bom_generator",
                "name": "AI BOM",
                "tool": "tools/security/ai_bom_generator.py",
                "description": "AI Bill of Materials generation",
            },
            {
                "id": "agent_trust_scorer",
                "name": "Trust Score",
                "tool": "tools/security/agent_trust_scorer.py",
                "description": "Dynamic inter-agent trust scoring",
            },
            {
                "id": "ai_telemetry",
                "name": "AI Telemetry",
                "tool": "tools/security/ai_telemetry_logger.py",
                "description": "AI interaction logging + anomaly detection",
            },
            {
                "id": "atlas_red_team",
                "name": "ATLAS Red Team",
                "tool": "tools/security/atlas_red_team.py",
                "description": "Adversarial ATLAS + behavioral testing",
            },
            {
                "id": "red_team_registry",
                "name": "Red Team Registry",
                "tool": "tools/security/red_team_registry.py",
                "description": "YAML-driven adversarial testing (6 plugins)",
            },
            {
                "id": "tool_chain_validator",
                "name": "Tool Chain Validate",
                "tool": "tools/security/tool_chain_validator.py",
                "description": "Tool-call-sequence validation",
            },
            {
                "id": "agent_output_validator",
                "name": "Output Validate",
                "tool": "tools/security/agent_output_validator.py",
                "description": "Post-tool output content safety",
            },
            {
                "id": "mcp_tool_authorizer",
                "name": "MCP RBAC",
                "tool": "tools/security/mcp_tool_authorizer.py",
                "description": "Per-tool RBAC for MCP servers",
            },
            {
                "id": "confabulation_detector",
                "name": "Confabulation Detect",
                "tool": "tools/security/confabulation_detector.py",
                "description": "Deterministic confabulation detection",
            },
            {
                "id": "model_card_generator",
                "name": "Model Card",
                "tool": "tools/compliance/model_card_generator.py",
                "description": "Google-format model cards",
            },
            {
                "id": "ai_inventory",
                "name": "AI Inventory",
                "tool": "tools/compliance/ai_inventory_manager.py",
                "description": "OMB M-25-21 AI system inventory",
            },
            {
                "id": "ai_transparency_audit",
                "name": "AI Transparency",
                "tool": "tools/compliance/ai_transparency_audit.py",
                "description": "Cross-framework AI transparency audit",
            },
            {
                "id": "ai_accountability_audit",
                "name": "AI Accountability",
                "tool": "tools/compliance/ai_accountability_audit.py",
                "description": "Cross-framework AI accountability audit",
            },
        ],
    },
    # ── 3. Security Scanning ───────────────────────────────
    "security": {
        "label": "Security Scanning",
        "icon": "lock",
        "color": "security",
        "tools": [
            {
                "id": "sast_runner",
                "name": "SAST Scan",
                "tool": "tools/security/sast_runner.py",
                "description": "Multi-language static analysis (Bandit, SpotBugs, gosec)",
            },
            {
                "id": "dependency_auditor",
                "name": "Dependency Audit",
                "tool": "tools/security/dependency_auditor.py",
                "description": "Multi-language dependency vulnerability audit",
            },
            {
                "id": "secret_detector",
                "name": "Secret Detection",
                "tool": "tools/security/secret_detector.py",
                "description": "Detect hardcoded secrets and credentials",
            },
            {
                "id": "container_scanner",
                "name": "Container Scan",
                "tool": "tools/security/container_scanner.py",
                "description": "Trivy container vulnerability scanning",
            },
            {
                "id": "vuln_scanner",
                "name": "Vulnerability Scan",
                "tool": "tools/security/vuln_scanner.py",
                "description": "Vulnerability scanning orchestrator",
            },
            {
                "id": "code_pattern_scanner",
                "name": "Dangerous Patterns",
                "tool": "tools/security/code_pattern_scanner.py",
                "description": "Dangerous pattern detection across 6 languages",
            },
            {
                "id": "credential_broker",
                "name": "Credential Check",
                "tool": "tools/security/credential_broker.py",
                "description": "Credential rotation and audit",
            },
            {
                "id": "egress_policy",
                "name": "Egress Policy",
                "tool": "tools/security/egress_policy_manager.py",
                "description": "Per-agent network egress policies",
            },
            {
                "id": "blueprint_verifier",
                "name": "Blueprint Verify",
                "tool": "tools/security/blueprint_verifier.py",
                "description": "SHA-256 recursive directory digest verification",
            },
            {
                "id": "endpoint_scanner",
                "name": "Endpoint Scan",
                "tool": "tools/security/endpoint_security_scanner.py",
                "description": "API endpoint security assessment",
            },
            {
                "id": "sandbox_executor",
                "name": "Sandbox Execute",
                "tool": "tools/security/sandbox_executor.py",
                "description": "Container-isolated code execution",
            },
        ],
    },
    # ── 4. Build & Code ────────────────────────────────────
    "build": {
        "label": "Build & Code",
        "icon": "hammer",
        "color": "build",
        "tools": [
            {
                "id": "code_generator",
                "name": "Generate Code",
                "tool": "tools/builder/code_generator.py",
                "description": "AI-assisted code generation (6 languages)",
            },
            {
                "id": "test_writer",
                "name": "Write Tests",
                "tool": "tools/builder/test_writer.py",
                "description": "BDD test generation (Gherkin + step defs)",
            },
            {
                "id": "scaffolder",
                "name": "Scaffold Project",
                "tool": "tools/builder/scaffolder.py",
                "description": "Project scaffolding from templates",
            },
            {
                "id": "linter",
                "name": "Lint Code",
                "tool": "tools/builder/linter.py",
                "description": "Multi-language linting",
            },
            {
                "id": "formatter",
                "name": "Format Code",
                "tool": "tools/builder/formatter.py",
                "description": "Multi-language formatting",
            },
            {
                "id": "agentic_fitness",
                "name": "Agentic Fitness",
                "tool": "tools/builder/agentic_fitness.py",
                "description": "6-dimension agentic architecture scoring",
            },
            {
                "id": "app_blueprint",
                "name": "App Blueprint",
                "tool": "tools/builder/app_blueprint.py",
                "description": "Deployment blueprint from fitness scorecard",
            },
            {
                "id": "child_app_generator",
                "name": "Child App Gen",
                "tool": "tools/builder/child_app_generator.py",
                "description": "Generate mini-ICDEV™ child applications",
            },
            {
                "id": "forge_validator",
                "name": "FORGE Validate",
                "tool": "tools/builder/forge_validator.py",
                "description": "FORGE framework compliance validation",
            },
            {
                "id": "language_support",
                "name": "Language Support",
                "tool": "tools/builder/language_support.py",
                "description": "Unified language registry and detection",
            },
        ],
    },
    # ── 5. Testing & Quality ───────────────────────────────
    "testing": {
        "label": "Testing & Quality",
        "icon": "check",
        "color": "testing",
        "tools": [
            {
                "id": "test_orchestrator",
                "name": "Run Tests",
                "tool": "tools/testing/test_orchestrator.py",
                "description": "Full test suite (unit + BDD + E2E)",
            },
            {
                "id": "e2e_runner",
                "name": "E2E Tests",
                "tool": "tools/testing/e2e_runner.py",
                "description": "End-to-end browser test execution",
            },
            {
                "id": "stub_detector",
                "name": "Detect Stubs",
                "tool": "tools/testing/stub_detector.py",
                "description": "4-level stub/placeholder detection",
            },
            {
                "id": "health_check",
                "name": "Health Check",
                "tool": "tools/testing/health_check.py",
                "description": "System validation (env, DB, deps, tools)",
            },
            {
                "id": "acceptance_validator",
                "name": "Acceptance Validate",
                "tool": "tools/testing/acceptance_validator.py",
                "description": "V&V gate: plan criteria vs test evidence",
            },
            {
                "id": "smoke_test",
                "name": "Smoke Test",
                "tool": "tools/testing/smoke_test.py",
                "description": "Verify all CLI tools importable and --help works",
            },
            {
                "id": "fuzz_cli",
                "name": "Fuzz CLI",
                "tool": "tools/testing/fuzz_cli.py",
                "description": "Fuzz CLI tools with malformed inputs",
            },
            {
                "id": "production_remediate",
                "name": "Production Remediate",
                "tool": "tools/testing/production_remediate.py",
                "description": "Auto-fix audit blockers (3-tier confidence)",
            },
            {
                "id": "goveval_benchmark",
                "name": "GovEval Benchmark",
                "tool": "tools/testing/goveval.py",
                "description": "7-dimension Gov/DoD compliance benchmark",
            },
            {
                "id": "agent_benchmark",
                "name": "Agent Benchmark",
                "tool": "tools/evaluation/agent_benchmark.py",
                "description": "Scenario-based agent evaluation (12 scenarios)",
            },
            {
                "id": "llm_judge",
                "name": "LLM Judge",
                "tool": "tools/writing/llm_judge.py",
                "description": "Rubric-based semantic evaluation (Prometheus-2)",
            },
        ],
    },
    # ── 6. Code Intelligence ───────────────────────────────
    "code_intel": {
        "label": "Code Intelligence",
        "icon": "microscope",
        "color": "code_intel",
        "tools": [
            {
                "id": "code_analyzer",
                "name": "Code Analyzer",
                "tool": "tools/analysis/code_analyzer.py",
                "description": "Cyclomatic/cognitive complexity, smells",
            },
            {
                "id": "runtime_feedback",
                "name": "Runtime Feedback",
                "tool": "tools/analysis/runtime_feedback.py",
                "description": "Test-to-source correlation and health scoring",
            },
            {
                "id": "formal_verifier",
                "name": "Formal Verify",
                "tool": "tools/analysis/formal_verifier.py",
                "description": "Property-based security checks",
            },
            {
                "id": "verify_loop",
                "name": "Verify Loop",
                "tool": "tools/analysis/verify_loop.py",
                "description": "Compiler-in-the-loop verification",
            },
            {
                "id": "api_surface_extractor",
                "name": "API Surface",
                "tool": "tools/testing/api_surface_extractor.py",
                "description": "AST-based public API surface extraction",
            },
            {
                "id": "coherence_checker",
                "name": "Coherence Check",
                "tool": "tools/workflow/coherence_checker.py",
                "description": "Implementation coherence (7 checks + auto-fix)",
            },
            {
                "id": "impact_analyzer",
                "name": "Impact Analyze",
                "tool": "tools/workflow/impact_analyzer.py",
                "description": "Cross-subsystem integration gap detection",
            },
            {
                "id": "modular_design",
                "name": "Modular Design",
                "tool": "tools/mosa/modular_design_analyzer.py",
                "description": "Coupling, cohesion, interface coverage",
            },
            {
                "id": "mosa_enforcer",
                "name": "MOSA Enforce",
                "tool": "tools/mosa/mosa_code_enforcer.py",
                "description": "MOSA violation scanner",
            },
        ],
    },
    # ── 7. Deploy & Infrastructure ─────────────────────────
    "deploy": {
        "label": "Deploy & Infrastructure",
        "icon": "cloud",
        "color": "deploy",
        "tools": [
            {
                "id": "terraform_aws",
                "name": "Terraform AWS",
                "tool": "tools/infra/terraform_generator.py",
                "description": "AWS GovCloud Terraform generation",
            },
            {
                "id": "terraform_azure",
                "name": "Terraform Azure",
                "tool": "tools/infra/terraform_generator_azure.py",
                "description": "Azure Government Terraform generation",
            },
            {
                "id": "terraform_gcp",
                "name": "Terraform GCP",
                "tool": "tools/infra/terraform_generator_gcp.py",
                "description": "GCP Government Terraform generation",
            },
            {
                "id": "terraform_oci",
                "name": "Terraform OCI",
                "tool": "tools/infra/terraform_generator_oci.py",
                "description": "OCI Government Terraform generation",
            },
            {
                "id": "terraform_ibm",
                "name": "Terraform IBM",
                "tool": "tools/infra/terraform_generator_ibm.py",
                "description": "IBM Cloud Terraform generation",
            },
            {
                "id": "terraform_onprem",
                "name": "Terraform On-Prem",
                "tool": "tools/infra/terraform_generator_onprem.py",
                "description": "On-premises Terraform / Docker Compose",
            },
            {
                "id": "ansible_generator",
                "name": "Ansible Playbooks",
                "tool": "tools/infra/ansible_generator.py",
                "description": "Ansible playbook generation",
            },
            {
                "id": "k8s_generator",
                "name": "K8s Manifests",
                "tool": "tools/infra/k8s_generator.py",
                "description": "Kubernetes manifest generation",
            },
            {
                "id": "dockerfile_generator",
                "name": "Dockerfile",
                "tool": "tools/infra/dockerfile_generator.py",
                "description": "STIG-hardened Dockerfile generation",
            },
            {
                "id": "pipeline_generator",
                "name": "CI/CD Pipeline",
                "tool": "tools/infra/pipeline_generator.py",
                "description": "GitLab CI pipeline generation",
            },
            {
                "id": "pipeline_config",
                "name": "Pipeline Config",
                "tool": "tools/ci/pipeline_config_generator.py",
                "description": "GitHub Actions / GitLab CI from icdev.yaml",
            },
            {
                "id": "ironbank_generator",
                "name": "Iron Bank",
                "tool": "tools/infra/ironbank_metadata_generator.py",
                "description": "Platform One / Iron Bank hardening manifest",
            },
            {
                "id": "infra_status",
                "name": "Infra Status",
                "tool": "tools/infra/infra_status.py",
                "description": "Infrastructure health check",
            },
            {
                "id": "rollback",
                "name": "Rollback",
                "tool": "tools/infra/rollback.py",
                "description": "Deployment rollback",
            },
        ],
    },
    # ── 8. DevSecOps & ZTA ─────────────────────────────────
    "devsecops": {
        "label": "DevSecOps & ZTA",
        "icon": "shield-lock",
        "color": "devsecops",
        "tools": [
            {
                "id": "devsecops_profile",
                "name": "DevSecOps Profile",
                "tool": "tools/devsecops/profile_manager.py",
                "description": "DevSecOps profile and maturity assessment",
            },
            {
                "id": "zta_maturity",
                "name": "ZTA Maturity",
                "tool": "tools/devsecops/zta_maturity_scorer.py",
                "description": "7-pillar ZTA maturity scoring",
            },
            {
                "id": "pipeline_security",
                "name": "Pipeline Security",
                "tool": "tools/devsecops/pipeline_security_generator.py",
                "description": "GitLab CI security stage generation",
            },
            {
                "id": "policy_generator",
                "name": "Policy Generator",
                "tool": "tools/devsecops/policy_generator.py",
                "description": "Kyverno/OPA policy-as-code generation",
            },
            {
                "id": "attestation_manager",
                "name": "Attestation Mgr",
                "tool": "tools/devsecops/attestation_manager.py",
                "description": "Image signing + SBOM attestation (SLSA L3)",
            },
            {
                "id": "service_mesh",
                "name": "Service Mesh",
                "tool": "tools/devsecops/service_mesh_generator.py",
                "description": "Istio/Linkerd service mesh config",
            },
            {
                "id": "zta_terraform",
                "name": "ZTA Terraform",
                "tool": "tools/devsecops/zta_terraform_generator.py",
                "description": "ZTA security modules (GuardDuty, WAF)",
            },
            {
                "id": "network_segmentation",
                "name": "Network Segment",
                "tool": "tools/devsecops/network_segmentation_generator.py",
                "description": "Namespace isolation + microsegmentation",
            },
            {
                "id": "pdp_config",
                "name": "PDP Config",
                "tool": "tools/devsecops/pdp_config_generator.py",
                "description": "PDP/PEP config (Zscaler, Palo Alto, ICAM)",
            },
        ],
    },
    # ── 9. Requirements & Intake ───────────────────────────
    "requirements": {
        "label": "Requirements & Intake",
        "icon": "clipboard",
        "color": "requirements",
        "tools": [
            {
                "id": "intake_engine",
                "name": "Intake Engine",
                "tool": "tools/requirements/intake_engine.py",
                "description": "Conversational requirements intake",
            },
            {
                "id": "decomposition_engine",
                "name": "SAFe Decompose",
                "tool": "tools/requirements/decomposition_engine.py",
                "description": "SAFe hierarchy (Epic > Feature > Story)",
            },
            {
                "id": "gap_detector",
                "name": "Gap Detector",
                "tool": "tools/requirements/gap_detector.py",
                "description": "AI-powered gap/ambiguity detection",
            },
            {
                "id": "document_extractor",
                "name": "Document Extract",
                "tool": "tools/requirements/document_extractor.py",
                "description": "Extract requirements from SOW/CDD/CONOPS",
            },
            {
                "id": "readiness_scorer",
                "name": "Readiness Score",
                "tool": "tools/requirements/readiness_scorer.py",
                "description": "5-dimension readiness scoring",
            },
            {
                "id": "spec_quality",
                "name": "Spec Quality",
                "tool": "tools/requirements/spec_quality_checker.py",
                "description": "Unit tests for English — spec validation",
            },
            {
                "id": "consistency_analyzer",
                "name": "Consistency Check",
                "tool": "tools/requirements/consistency_analyzer.py",
                "description": "Cross-artifact consistency validation",
            },
            {
                "id": "clarification_engine",
                "name": "Clarification",
                "tool": "tools/requirements/clarification_engine.py",
                "description": "Prioritized clarification questions",
            },
            {
                "id": "boundary_analyzer",
                "name": "ATO Boundary",
                "tool": "tools/requirements/boundary_analyzer.py",
                "description": "4-tier ATO boundary impact assessment",
            },
            {
                "id": "traceability_builder",
                "name": "Traceability RTM",
                "tool": "tools/requirements/traceability_builder.py",
                "description": "Full RTM: req > model > code > test > control",
            },
            {
                "id": "complexity_scorer",
                "name": "Complexity Score",
                "tool": "tools/requirements/complexity_scorer.py",
                "description": "Scale-adaptive complexity scoring",
            },
            {
                "id": "prd_generator",
                "name": "PRD Generate",
                "tool": "tools/requirements/prd_generator.py",
                "description": "Product Requirements Document generation",
            },
        ],
    },
    # ── 10. MBSE & Digital Engineering ─────────────────────
    "mbse": {
        "label": "MBSE & Digital Engineering",
        "icon": "diagram",
        "color": "mbse",
        "tools": [
            {
                "id": "xmi_parser",
                "name": "SysML Import",
                "tool": "tools/mbse/xmi_parser.py",
                "description": "Parse Cameo SysML v1.6 XMI exports",
            },
            {
                "id": "reqif_parser",
                "name": "DOORS Import",
                "tool": "tools/mbse/reqif_parser.py",
                "description": "Parse DOORS NG ReqIF 1.2 exports",
            },
            {
                "id": "digital_thread",
                "name": "Digital Thread",
                "tool": "tools/mbse/digital_thread.py",
                "description": "End-to-end traceability engine",
            },
            {
                "id": "model_code_generator",
                "name": "Model-to-Code",
                "tool": "tools/mbse/model_code_generator.py",
                "description": "Generate code from SysML models",
            },
            {
                "id": "sync_engine",
                "name": "Model-Code Sync",
                "tool": "tools/mbse/sync_engine.py",
                "description": "Bidirectional model-code sync",
            },
            {
                "id": "des_assessor",
                "name": "DES Assess",
                "tool": "tools/mbse/des_assessor.py",
                "description": "DoDI 5000.87 Digital Engineering compliance",
            },
            {
                "id": "model_control_mapper",
                "name": "Model-NIST Map",
                "tool": "tools/mbse/model_control_mapper.py",
                "description": "Map SysML elements to NIST 800-53 controls",
            },
            {
                "id": "icd_generator",
                "name": "ICD Generate",
                "tool": "tools/mosa/icd_generator.py",
                "description": "Interface Control Document generation",
            },
            {
                "id": "tsp_generator",
                "name": "TSP Generate",
                "tool": "tools/mosa/tsp_generator.py",
                "description": "Technical Standard Profile generation",
            },
        ],
    },
    # ── 11. Modernization & Translation ────────────────────
    "modernization": {
        "label": "Modernization & Translation",
        "icon": "refresh",
        "color": "modernization",
        "tools": [
            {
                "id": "legacy_analyzer",
                "name": "Legacy Analyze",
                "tool": "tools/modernization/legacy_analyzer.py",
                "description": "Static analysis of legacy applications",
            },
            {
                "id": "architecture_extractor",
                "name": "Architecture Extract",
                "tool": "tools/modernization/architecture_extractor.py",
                "description": "Reverse-engineer app architecture",
            },
            {
                "id": "seven_r_assessor",
                "name": "7R Assess",
                "tool": "tools/modernization/seven_r_assessor.py",
                "description": "7Rs migration strategy scoring",
            },
            {
                "id": "version_migrator",
                "name": "Version Migrate",
                "tool": "tools/modernization/version_migrator.py",
                "description": "Transform code to newer versions",
            },
            {
                "id": "framework_migrator",
                "name": "Framework Migrate",
                "tool": "tools/modernization/framework_migrator.py",
                "description": "Transform frameworks (Struts→Spring, etc.)",
            },
            {
                "id": "monolith_decomposer",
                "name": "Monolith Decompose",
                "tool": "tools/modernization/monolith_decomposer.py",
                "description": "Bounded context detection and decomposition",
            },
            {
                "id": "db_migration_planner",
                "name": "DB Migration Plan",
                "tool": "tools/modernization/db_migration_planner.py",
                "description": "DDL scripts and data migration SQL",
            },
            {
                "id": "strangler_fig",
                "name": "Strangler Fig",
                "tool": "tools/modernization/strangler_fig_manager.py",
                "description": "Incremental migration coexistence",
            },
            {
                "id": "compliance_bridge",
                "name": "Compliance Bridge",
                "tool": "tools/modernization/compliance_bridge.py",
                "description": "ATO-aware migration control inheritance",
            },
            {
                "id": "translation_manager",
                "name": "Code Translate",
                "tool": "tools/translation/translation_manager.py",
                "description": "Cross-language translation pipeline",
            },
            {
                "id": "test_translator",
                "name": "Test Translate",
                "tool": "tools/translation/test_translator.py",
                "description": "Translate tests between frameworks",
            },
        ],
    },
    # ── 12. Maintenance & Supply Chain ─────────────────────
    "maintenance": {
        "label": "Maintenance & Supply Chain",
        "icon": "wrench",
        "color": "maintenance",
        "tools": [
            {
                "id": "dependency_scanner",
                "name": "Dependency Scan",
                "tool": "tools/maintenance/dependency_scanner.py",
                "description": "Inventory deps across 6 languages",
            },
            {
                "id": "vulnerability_checker",
                "name": "Vuln Check",
                "tool": "tools/maintenance/vulnerability_checker.py",
                "description": "CVE vulnerability scanning + SLA",
            },
            {
                "id": "maintenance_auditor",
                "name": "Maintenance Audit",
                "tool": "tools/maintenance/maintenance_auditor.py",
                "description": "Full audit: scan + check + score + SLA",
            },
            {
                "id": "remediation_engine",
                "name": "Remediate",
                "tool": "tools/maintenance/remediation_engine.py",
                "description": "Auto-implement dependency fixes",
            },
            {
                "id": "dependency_graph",
                "name": "Dependency Graph",
                "tool": "tools/supply_chain/dependency_graph.py",
                "description": "Supply chain graph + impact propagation",
            },
            {
                "id": "isa_manager",
                "name": "ISA Manager",
                "tool": "tools/supply_chain/isa_manager.py",
                "description": "ISA/MOU lifecycle tracking",
            },
            {
                "id": "scrm_assessor",
                "name": "SCRM Assess",
                "tool": "tools/supply_chain/scrm_assessor.py",
                "description": "NIST 800-161 supply chain risk assessment",
            },
            {
                "id": "cve_triager",
                "name": "CVE Triage",
                "tool": "tools/supply_chain/cve_triager.py",
                "description": "CVE triage with blast radius + SLA",
            },
        ],
    },
    # ── 13. Monitoring & SRE ───────────────────────────────
    "monitoring": {
        "label": "Monitoring & SRE",
        "icon": "pulse",
        "color": "monitoring",
        "tools": [
            {
                "id": "log_analyzer",
                "name": "Log Analyze",
                "tool": "tools/monitor/log_analyzer.py",
                "description": "ELK/Splunk log analysis",
            },
            {
                "id": "metric_collector",
                "name": "Metric Collect",
                "tool": "tools/monitor/metric_collector.py",
                "description": "Prometheus metric collection",
            },
            {
                "id": "alert_correlator",
                "name": "Alert Correlate",
                "tool": "tools/monitor/alert_correlator.py",
                "description": "Correlate alerts across sources",
            },
            {
                "id": "health_checker",
                "name": "Health Check",
                "tool": "tools/monitor/health_checker.py",
                "description": "Application health check",
            },
            {
                "id": "heartbeat_daemon",
                "name": "Heartbeat",
                "tool": "tools/monitor/heartbeat_daemon.py",
                "description": "Proactive daemon: 7 configurable checks",
            },
            {
                "id": "auto_resolver",
                "name": "Auto-Resolve",
                "tool": "tools/monitor/auto_resolver.py",
                "description": "Webhook-triggered auto-resolution",
            },
            {
                "id": "slo_manager",
                "name": "SLO Manager",
                "tool": "tools/sre/slo_manager.py",
                "description": "SLO definition, burn rate, dashboard",
            },
            {
                "id": "runbook_executor",
                "name": "Runbook Execute",
                "tool": "tools/sre/runbook_executor.py",
                "description": "Risk-tiered runbook execution",
            },
            {
                "id": "incident_commander",
                "name": "Incident Command",
                "tool": "tools/sre/incident_commander.py",
                "description": "Incident lifecycle, MTTR, postmortem",
            },
            {
                "id": "model_drift_monitor",
                "name": "Model Drift",
                "tool": "tools/llm/model_monitor.py",
                "description": "Quality scoring + statistical drift detection",
            },
        ],
    },
    # ── 14. Research & Innovation ──────────────────────────
    "analysis": {
        "label": "Research & Innovation",
        "icon": "chart",
        "color": "analysis",
        "tools": [
            {
                "id": "research_engine",
                "name": "Research Engine",
                "tool": "tools/research/research_engine.py",
                "description": "8-stage industry research pipeline",
            },
            {
                "id": "innovation_manager",
                "name": "Innovation Engine",
                "tool": "tools/innovation/innovation_manager.py",
                "description": "Full innovation pipeline + daemon",
            },
            {
                "id": "web_scanner",
                "name": "Web Scanner",
                "tool": "tools/innovation/web_scanner.py",
                "description": "Scan GitHub, NVD, SO, HN for signals",
            },
            {
                "id": "signal_ranker",
                "name": "Signal Ranker",
                "tool": "tools/innovation/signal_ranker.py",
                "description": "5-dimension innovation scoring",
            },
            {
                "id": "triage_engine",
                "name": "Triage Engine",
                "tool": "tools/innovation/triage_engine.py",
                "description": "5-stage compliance-first triage",
            },
            {
                "id": "solution_generator",
                "name": "Solution Generate",
                "tool": "tools/innovation/solution_generator.py",
                "description": "Auto-generate solution specs",
            },
            {
                "id": "competitive_intel",
                "name": "Competitive Intel",
                "tool": "tools/innovation/competitive_intel.py",
                "description": "Competitor feature monitoring + gap analysis",
            },
            {
                "id": "standards_monitor",
                "name": "Standards Monitor",
                "tool": "tools/innovation/standards_monitor.py",
                "description": "NIST/CISA/DoD/FedRAMP change tracking",
            },
            {
                "id": "introspective_analyzer",
                "name": "Introspective",
                "tool": "tools/innovation/introspective_analyzer.py",
                "description": "Internal telemetry mining",
            },
            {
                "id": "creative_engine",
                "name": "Creative Engine",
                "tool": "tools/creative/creative_engine.py",
                "description": "Customer-centric feature discovery",
            },
            {
                "id": "knowledge_ingest",
                "name": "Knowledge Ingest",
                "tool": "tools/knowledge/knowledge_ingest.py",
                "description": "Ingest knowledge artifacts",
            },
            {
                "id": "pattern_detector",
                "name": "Pattern Detect",
                "tool": "tools/knowledge/pattern_detector.py",
                "description": "Detect code and process patterns",
            },
            {
                "id": "self_heal",
                "name": "Self-Heal Analyze",
                "tool": "tools/knowledge/self_heal_analyzer.py",
                "description": "Analyze failures and auto-correct",
            },
            {
                "id": "forecast_generator",
                "name": "Forecast",
                "tool": "tools/research/forecast_generator.py",
                "description": "Cross-engine prediction + surprise scoring",
            },
        ],
    },
    # ── 15. Knowledge, RAG & Simulation ────────────────────
    "knowledge": {
        "label": "Knowledge, RAG & Simulation",
        "icon": "database",
        "color": "knowledge",
        "tools": [
            {
                "id": "rag_retriever",
                "name": "RAG Search",
                "tool": "tools/rag/retriever.py",
                "description": "Two-stage RAG retrieval (vector + BM25)",
            },
            {
                "id": "rag_ingestion",
                "name": "RAG Ingest",
                "tool": "tools/rag/ingestion_manager.py",
                "description": "Real-time + batch RAG ingestion",
            },
            {
                "id": "corrective_rag",
                "name": "Corrective RAG",
                "tool": "tools/rag/corrective_rag.py",
                "description": "Parallel multi-strategy retrieval",
            },
            {
                "id": "crag_evaluator",
                "name": "CRAG Benchmark",
                "tool": "tools/rag/crag_evaluator.py",
                "description": "CRAG hallucination-penalizing evaluation",
            },
            {
                "id": "codebase_indexer",
                "name": "Codebase Index",
                "tool": "tools/rag/codebase_indexer.py",
                "description": "AST-based codebase indexer",
            },
            {
                "id": "graph_rag",
                "name": "Graph RAG",
                "tool": "tools/knowledge_graph/graph_rag.py",
                "description": "GraphRAG retrieval with scoring profiles",
            },
            {
                "id": "kg_ingester",
                "name": "KG Ingest",
                "tool": "tools/knowledge_graph/ingester.py",
                "description": "Knowledge graph document ingestion",
            },
            {
                "id": "compliance_graph",
                "name": "Compliance Graph",
                "tool": "tools/knowledge_graph/compliance_graph.py",
                "description": "Compliance crosswalk as knowledge graph",
            },
            {
                "id": "simulation_engine",
                "name": "Simulation",
                "tool": "tools/simulation/simulation_engine.py",
                "description": "6-dimension what-if simulation",
            },
            {
                "id": "monte_carlo",
                "name": "Monte Carlo",
                "tool": "tools/simulation/monte_carlo.py",
                "description": "PERT schedule/cost/risk estimation",
            },
            {
                "id": "coa_generator",
                "name": "COA Generate",
                "tool": "tools/simulation/coa_generator.py",
                "description": "Generate 3 COAs + RED alternatives",
            },
            {
                "id": "workflow_composer",
                "name": "Workflow Compose",
                "tool": "tools/orchestration/workflow_composer.py",
                "description": "Declarative cross-phase workflow engine",
            },
        ],
    },
    # ── 16. GovCon & Proposals ─────────────────────────────
    "govcon": {
        "label": "GovCon & Proposals",
        "icon": "briefcase",
        "color": "govcon",
        "tools": [
            {
                "id": "sam_scanner",
                "name": "SAM.gov Scan",
                "tool": "tools/govcon/sam_scanner.py",
                "description": "Poll SAM.gov Opportunities API",
            },
            {
                "id": "requirement_extractor",
                "name": "RFP Extract",
                "tool": "tools/govcon/requirement_extractor.py",
                "description": "Extract shall/must statements from RFPs",
            },
            {
                "id": "capability_mapper_govcon",
                "name": "Capability Map",
                "tool": "tools/govcon/capability_mapper.py",
                "description": "Map requirements to ICDEV™ capabilities",
            },
            {
                "id": "gap_analyzer",
                "name": "Gap Analyze",
                "tool": "tools/govcon/gap_analyzer.py",
                "description": "Identify unmet requirements + recommendations",
            },
            {
                "id": "response_drafter",
                "name": "Response Draft",
                "tool": "tools/govcon/response_drafter.py",
                "description": "Two-tier LLM proposal drafting",
            },
            {
                "id": "compliance_populator",
                "name": "Compliance Matrix",
                "tool": "tools/govcon/compliance_populator.py",
                "description": "Auto-populate L/M/N compliance matrix",
            },
            {
                "id": "govcon_knowledge_base",
                "name": "Knowledge Base",
                "tool": "tools/govcon/knowledge_base.py",
                "description": "Reusable content blocks (11 categories)",
            },
            {
                "id": "question_generator",
                "name": "RFP Questions",
                "tool": "tools/govcon/question_generator.py",
                "description": "Strategic RFP question generation",
            },
            {
                "id": "amendment_tracker",
                "name": "Amendment Track",
                "tool": "tools/govcon/amendment_tracker.py",
                "description": "RFP amendment version tracking + diff",
            },
            {
                "id": "award_tracker",
                "name": "Award Track",
                "tool": "tools/govcon/award_tracker.py",
                "description": "SAM.gov award notice tracking",
            },
            {
                "id": "competitor_profiler",
                "name": "Competitor Profile",
                "tool": "tools/govcon/competitor_profiler.py",
                "description": "Competitor intelligence aggregation",
            },
            {
                "id": "govcon_engine",
                "name": "GovCon Pipeline",
                "tool": "tools/govcon/govcon_engine.py",
                "description": "DISCOVER → EXTRACT → MAP → DRAFT pipeline",
            },
            {
                "id": "contract_manager",
                "name": "Contract Manage",
                "tool": "tools/govcon/contract_manager.py",
                "description": "CPMP contracts, CLINs, WBS, deliverables",
            },
            {
                "id": "evm_engine",
                "name": "EVM Engine",
                "tool": "tools/govcon/evm_engine.py",
                "description": "ANSI/EIA-748 EVM calculations + forecast",
            },
            {
                "id": "cpars_predictor",
                "name": "CPARS Predict",
                "tool": "tools/govcon/cpars_predictor.py",
                "description": "Deterministic CPARS prediction",
            },
            {
                "id": "cdrl_generator",
                "name": "CDRL Generate",
                "tool": "tools/govcon/cdrl_generator.py",
                "description": "Auto-generate CDRLs via ICDEV™ tools",
            },
        ],
    },
    # ── 17. Telecom & ISP ──────────────────────────────────────
    "telecom": {
        "label": "Telecom & ISP",
        "icon": "satellite",
        "color": "telecom",
        "tools": [
            {
                "id": "network_topology_mapper",
                "name": "Network Topology Map",
                "tool": "tools/telecom/network_topology_mapper.py",
                "description": "Discover and render ISP/carrier topology (BGP, OSPF, IS-IS)",
            },
            {
                "id": "bgp_route_analyzer",
                "name": "BGP Route Analyze",
                "tool": "tools/telecom/bgp_route_analyzer.py",
                "description": "Parse BGP table, detect anomalies, path-prepend analysis",
            },
            {
                "id": "ipam_allocator",
                "name": "IPAM Allocate",
                "tool": "tools/telecom/ipam_allocator.py",
                "description": "IP address block allocation and ARIN/RIPE registry lookup",
            },
            {
                "id": "asn_tracker",
                "name": "ASN Tracker",
                "tool": "tools/telecom/asn_tracker.py",
                "description": "Autonomous System Number inventory and peering policy tracking",
            },
            {
                "id": "dns_whois_lookup",
                "name": "DNS / WHOIS Lookup",
                "tool": "tools/telecom/dns_whois_lookup.py",
                "description": "Bulk DNS record resolution and WHOIS enrichment",
            },
            {
                "id": "bandwidth_profiler",
                "name": "Bandwidth Profile",
                "tool": "tools/telecom/bandwidth_profiler.py",
                "description": "QoS / traffic-shaping profile generation and validation",
            },
            {
                "id": "sip_voip_validator",
                "name": "SIP/VoIP Validate",
                "tool": "tools/telecom/sip_voip_validator.py",
                "description": "SIP trunk config and RTP media path validation",
            },
            {
                "id": "mpls_sdwan_template",
                "name": "MPLS / SD-WAN Template",
                "tool": "tools/telecom/mpls_sdwan_template.py",
                "description": "Generate MPLS/SD-WAN provisioning templates for CPE and PE routers",
            },
            {
                "id": "snmp_monitor_config",
                "name": "SNMP Monitor Config",
                "tool": "tools/telecom/snmp_monitor_config.py",
                "description": "SNMP MIB walk, trap receiver config, device health polling setup",
            },
            {
                "id": "latency_jitter_test",
                "name": "Latency / Jitter Test",
                "tool": "tools/telecom/latency_jitter_test.py",
                "description": "ICMP/TCP ping battery, jitter, and packet-loss measurement",
            },
            {
                "id": "cdn_config_generator",
                "name": "CDN Config Generate",
                "tool": "tools/telecom/cdn_config_generator.py",
                "description": "CDN origin policy and cache-control rule generation",
            },
            {
                "id": "fcc_compliance_check",
                "name": "FCC Compliance Check",
                "tool": "tools/telecom/fcc_compliance_check.py",
                "description": "FCC Part 47, CPNI, and CALEA readiness gate",
            },
            {
                "id": "calea_intercept_audit",
                "name": "CALEA Intercept Audit",
                "tool": "tools/telecom/calea_intercept_audit.py",
                "description": "Lawful intercept capability audit per 47 U.S.C. §1002",
            },
            {
                "id": "5g_network_planner",
                "name": "5G Network Plan",
                "tool": "tools/telecom/5g_network_planner.py",
                "description": "5G NR cell-site capacity and coverage planning",
            },
            {
                "id": "noc_runbook_generator",
                "name": "NOC Runbook Generate",
                "tool": "tools/telecom/noc_runbook_generator.py",
                "description": "Auto-generate Tier-1/2/3 NOC runbooks from topology data",
            },
            {
                "id": "telecom_sbom",
                "name": "Telecom SBOM",
                "tool": "tools/telecom/telecom_sbom.py",
                "description": "Firmware/software BOM for CPE, routers, and managed switches",
            },
            {
                "id": "ss7_hardening_audit",
                "name": "SS7 Hardening Audit",
                "tool": "tools/telecom/ss7_hardening_audit.py",
                "description": "SS7/Diameter/SCTP protocol security audit and hardening guide",
            },
            {
                "id": "isp_peering_analyzer",
                "name": "ISP Peering Analyze",
                "tool": "tools/telecom/isp_peering_analyzer.py",
                "description": "Peering policy analysis and Internet Exchange route audit",
            },
            # ── Routing ──
            {
                "id": "router_config_generator",
                "name": "Router Config Generate",
                "tool": "tools/telecom/router_config_generator.py",
                "description": "Baseline router configuration generation (Cisco IOS, Junos, Arista EOS)",
            },
            {
                "id": "route_table_auditor",
                "name": "Route Table Audit",
                "tool": "tools/telecom/route_table_auditor.py",
                "description": "Audit routing tables for anomalies, duplicate routes, and black holes",
            },
            # ── Switching ──
            {
                "id": "switch_config_generator",
                "name": "Switch Config Generate",
                "tool": "tools/telecom/switch_config_generator.py",
                "description": "L2/L3 switch configurations — VLANs, STP, LACP, port-security",
            },
            {
                "id": "vlan_topology_mapper",
                "name": "VLAN Topology Map",
                "tool": "tools/telecom/vlan_topology_mapper.py",
                "description": "Map VLAN propagation across switch fabric with STP root analysis",
            },
            # ── Routing Protocols ──
            {
                "id": "ospf_area_designer",
                "name": "OSPF Area Design",
                "tool": "tools/telecom/ospf_area_designer.py",
                "description": "OSPF area/LSA design, summarization, stub/NSSA configuration",
            },
            {
                "id": "isis_topology_designer",
                "name": "IS-IS Topology Design",
                "tool": "tools/telecom/isis_topology_designer.py",
                "description": "IS-IS Level 1/2 topology design and route leaking policy",
            },
            {
                "id": "bgp_policy_generator",
                "name": "BGP Policy Generate",
                "tool": "tools/telecom/bgp_policy_generator.py",
                "description": "BGP route-policy, prefix-list, and community generation for eBGP/iBGP",
            },
            # ── Installation ──
            {
                "id": "device_install_workflow",
                "name": "Device Install Workflow",
                "tool": "tools/telecom/device_install_workflow.py",
                "description": "Step-by-step installation workflow for CPE, edge routers, and switches",
            },
            {
                "id": "rack_unit_planner",
                "name": "Rack Unit Plan",
                "tool": "tools/telecom/rack_unit_planner.py",
                "description": "Data center rack layout, power draw, and cooling calculation",
            },
            # ── Replacement / Upgrade (EOL) ──
            {
                "id": "eol_asset_scanner",
                "name": "EOL Asset Scan",
                "tool": "tools/telecom/eol_asset_scanner.py",
                "description": "Identify end-of-life/end-of-support network equipment and firmware",
            },
            {
                "id": "upgrade_migration_planner",
                "name": "Upgrade Migration Plan",
                "tool": "tools/telecom/upgrade_migration_planner.py",
                "description": "Change-window planning and rollback procedures for EOL replacements",
            },
            {
                "id": "device_replacement_runbook",
                "name": "Device Replacement Runbook",
                "tool": "tools/telecom/device_replacement_runbook.py",
                "description": "Auto-generate device replacement runbook with pre/post validation steps",
            },
            # ── Fiber Optics ──
            {
                "id": "fiber_plant_designer",
                "name": "Fiber Plant Design",
                "tool": "tools/telecom/fiber_plant_designer.py",
                "description": "Outside plant (OSP) fiber route design, conduit fill, and bill of materials",
            },
            {
                "id": "otdr_trace_analyzer",
                "name": "OTDR Trace Analyze",
                "tool": "tools/telecom/otdr_trace_analyzer.py",
                "description": "Parse OTDR trace files, detect splice loss, reflections, and fiber breaks",
            },
            {
                "id": "optical_link_budget",
                "name": "Optical Link Budget",
                "tool": "tools/telecom/optical_link_budget.py",
                "description": "End-to-end optical power budget calculation for DWDM/CWDM/dark-fiber spans",
            },
            # ── Crypto KG (COMSEC Key Management) ──
            {
                "id": "comsec_kg_configurator",
                "name": "COMSEC KG Configure",
                "tool": "tools/telecom/comsec_kg_configurator.py",
                "description": "NSA Type 1 KG device config and key-fill planning (KG-175 TACLANE, KG-250)",
            },
            {
                "id": "crypto_key_lifecycle",
                "name": "Crypto Key Lifecycle",
                "tool": "tools/telecom/crypto_key_lifecycle.py",
                "description": "Key generation, OTAR scheduling, distribution tracking, and destruction audit",
            },
            # ── Peering Agreements ──
            {
                "id": "peering_agreement_generator",
                "name": "Peering Agreement Draft",
                "tool": "tools/telecom/peering_agreement_generator.py",
                "description": "Draft settlement-free or paid peering agreement templates with NOC contacts",
            },
            {
                "id": "peering_registry_sync",
                "name": "Peering Registry Sync",
                "tool": "tools/telecom/peering_registry_sync.py",
                "description": "Sync peering policies with PeeringDB and Euro-IX MLPA registry",
            },
        ],
    },
}


# ── MCP palette section (dwo-mcp-03-d3) ────────────────────
#
# A `node_type: mcp` step (dwo-mcp-03) dispatches a tool from
# tools/mcp/tool_registry.py through MCP_EXECUTOR instead of naming a script.
# That registry exposes 500+ tools, most of which gate MCP-WF-001 refuses, so
# the palette lists only the tools a step may dispatch unattended: offering a
# tool the executor will refuse at runtime is worse than not offering it.

#: Script every `node_type: mcp` step runs. Mirrors workflow_runner.MCP_EXECUTOR
#: — duplicated rather than imported to keep the palette free of a runner import.
MCP_EXECUTOR = "tools/studio/executors/mcp_executor.py"

#: Gate file holding the allowlist (dwo-mcp-02), and the section inside it.
GATES_FILENAME = "security_gates.yaml"
GATE_POLICY_KEY = "mcp_workflow_tools"

#: JSON-Schema type → the form widget the editor should render for it.
_SCHEMA_WIDGETS: dict[str, str] = {
    "string": "text",
    "integer": "number",
    "number": "number",
    "boolean": "checkbox",
    "array": "textarea",
    "object": "textarea",
}

#: Schema types whose form value is typed as JSON text, not as a scalar.
_JSON_SCHEMA_TYPES = ("array", "object")

#: Built section, keyed by nothing — there is one policy per process.
_MCP_SECTION_CACHE: dict | None = None


def _gate_policy_paths() -> list[Path]:
    """Gate-file locations to probe, nearest ancestor first.

    Probes both ``<root>/args/`` and ``<root>/data/args/`` at every level so the
    policy resolves from the repo checkout, from the ``icdev/`` package mirror,
    and from a pip-installed wheel where it ships as package data. Same strategy
    as the executor that enforces the allowlist.
    """
    paths: list[Path] = []
    for parent in Path(__file__).resolve().parents:
        for rel in (("args",), ("data", "args")):
            candidate = parent.joinpath(*rel, GATES_FILENAME)
            if candidate not in paths:
                paths.append(candidate)
    return paths


def load_mcp_allowlist(path: str | Path | None = None) -> list[str]:
    """Return the tools a workflow step may dispatch with no human gate.

    Reads ``mcp_workflow_tools.allowed`` from the gate file, preserving the
    order the policy declares. ``requires_approval`` tools are deliberately
    excluded: the executor refuses them until the human gate is wired, so
    listing them in the palette would only offer a step that cannot run.

    Unlike the executor, a missing or unreadable policy is not an error here —
    the palette simply shows no MCP tools, which is the same default-deny
    outcome. Enforcement stays with the executor.

    Args:
        path: Read this gate file instead of probing for one.

    Returns:
        Allowlisted tool names, or ``[]`` when no usable policy was found.
    """
    for candidate in [Path(path)] if path else _gate_policy_paths():
        try:
            data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError, yaml.YAMLError):
            continue
        policy = data.get(GATE_POLICY_KEY) if isinstance(data, dict) else None
        if not isinstance(policy, dict):
            continue
        # A policy that is no longer default-deny models something this palette
        # does not: show nothing rather than guess what the edit permits.
        if str(policy.get("default", "")).strip().lower() != "deny":
            return []
        seen: set[str] = set()
        allowed: list[str] = []
        for name in policy.get("allowed") or []:
            name = str(name).strip()
            if name and name not in seen:
                seen.add(name)
                allowed.append(name)
        return allowed
    return []


def _params_field(name: str, spec: Any, *, required: bool) -> dict:
    """Translate one JSON-Schema property into a palette form field."""
    spec = spec if isinstance(spec, dict) else {}
    schema_type = str(spec.get("type", "string") or "string")
    widget = _SCHEMA_WIDGETS.get(schema_type, "text")

    field: dict[str, Any] = {
        "name": name,
        "label": name.replace("_", " ").title(),
        "type": schema_type,
        "widget": widget,
        "required": required,
        "description": str(spec.get("description", "") or ""),
    }
    if schema_type in _JSON_SCHEMA_TYPES:
        # The editor must parse this field's value as JSON before it reaches
        # mcp_params, where the executor expects the schema's own type.
        field["json"] = True
    if isinstance(spec.get("enum"), list) and spec["enum"]:
        field["widget"] = "select"
        field["options"] = [str(o) for o in spec["enum"]]
    if "default" in spec:
        field["default"] = spec["default"]
    return field


def build_params_form(input_schema: Any) -> list[dict]:
    """Build the params form for one tool from its registry ``input_schema``.

    Required fields come first so the form leads with what the tool cannot run
    without; within each group the schema's own property order is kept.

    Args:
        input_schema: A tool's JSON-Schema entry. A non-object schema, or one
            with no properties, yields an empty form — the tool takes no params.

    Returns:
        Field descriptors carrying the widget, requiredness and help text the
        editor needs to render an input for each property.
    """
    if not isinstance(input_schema, dict):
        return []
    properties = input_schema.get("properties")
    if not isinstance(properties, dict):
        return []

    required = {str(r) for r in (input_schema.get("required") or [])}
    fields = [
        _params_field(str(name), spec, required=str(name) in required)
        for name, spec in properties.items()
    ]
    return sorted(fields, key=lambda f: not f["required"])


def build_mcp_palette_section(*, refresh: bool = False) -> dict:
    """Build the palette's MCP section: allowlisted registry tools only.

    Each entry carries the `node_type: mcp` step fields the editor serialises
    (``node_type``, ``mcp_tool``), the registry description as help text, and a
    ``params_form`` generated from the tool's ``input_schema``.

    An allowlisted name the registry does not know is dropped rather than shown
    — that is gate MCP-WF-001's ``mcp_tool_unknown_to_registry`` condition, and
    a palette entry for it would only produce a step that fails at dispatch.

    Args:
        refresh: Rebuild instead of returning the cached section. The policy and
            the registry are both read from disk, so a long-lived dashboard
            process otherwise keeps the section it built on first request.

    Returns:
        A catalog section, or ``{}`` when no allowlisted tool resolved.
    """
    global _MCP_SECTION_CACHE
    if _MCP_SECTION_CACHE is not None and not refresh:
        return _MCP_SECTION_CACHE

    tools: list[dict] = []
    allowlist = load_mcp_allowlist()
    if allowlist:
        try:
            from tools.mcp.tool_registry import (  # noqa: PLC0415
                RESOURCE_REGISTRY,
                TOOL_REGISTRY,
                list_tools,
            )
            registered = set(list_tools())
        except ImportError:
            registered = set()
            RESOURCE_REGISTRY = TOOL_REGISTRY = {}

        for name in allowlist:
            if name not in registered:
                continue
            entry = TOOL_REGISTRY.get(name) or RESOURCE_REGISTRY.get(name) or {}
            tools.append(
                {
                    "id": f"mcp_{name}",
                    # The registry key verbatim: it is what goes in `mcp_tool`,
                    # so a friendlier label would hide the value that matters.
                    "name": name,
                    "tool": MCP_EXECUTOR,
                    "description": str(entry.get("description", "") or ""),
                    "node_type": "mcp",
                    "mcp_tool": name,
                    "params_form": build_params_form(entry.get("input_schema")),
                }
            )

    section = (
        {
            "label": "MCP Tools",
            "icon": "plug",
            # Reuses an existing palette colour so the section renders styled.
            "color": "code_intel",
            "node_type": "mcp",
            "tools": tools,
        }
        if tools
        else {}
    )
    _MCP_SECTION_CACHE = section
    return section


def get_tool_catalog(*, refresh: bool = False) -> dict:
    """Return the full tool catalog for the palette UI.

    Args:
        refresh: Rebuild the MCP section from the allowlist and registry rather
            than serving the cached one. The static categories never change.
    """
    mcp_section = build_mcp_palette_section(refresh=refresh)
    if not mcp_section:
        return TOOL_CATEGORIES
    # A new mapping — TOOL_CATEGORIES is module state and callers iterate it.
    return {**TOOL_CATEGORIES, "mcp": mcp_section}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── CRUD ───────────────────────────────────────────────────


def list_workflows(*, shared_only: bool = False) -> list[dict]:
    """List all studio workflows."""
    import sqlite3 as _sqlite3
    conn = get_connection()
    try:
        sql = "SELECT * FROM studio_workflows"
        if shared_only:
            sql += " WHERE shared = 1"
        sql += " ORDER BY updated_at DESC"
        try:
            rows = conn.execute(sql).fetchall()
        except _sqlite3.OperationalError:
            # Table not yet initialized; return empty list gracefully
            return []
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_workflow(workflow_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM studio_workflows WHERE workflow_id = %s",
            (workflow_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_workflow(
    name: str,
    template_yaml: str,
    *,
    description: str = "",
    category: str = "custom",
    created_by: str = "studio",
) -> dict:
    """Create a new workflow from YAML template string."""
    # Validate YAML
    try:
        parsed = yaml.safe_load(template_yaml)
        if not isinstance(parsed, dict) or "steps" not in parsed:
            return {"status": "error", "error": "YAML must contain 'steps' key"}
    except yaml.YAMLError as exc:
        return {"status": "error", "error": f"Invalid YAML: {exc}"}

    wf_id = f"wf-{uuid.uuid4().hex[:12]}"
    now = _now_iso()

    conn = get_connection()
    try:
        # Stamp the row with the caller's classification so the exact-match RLS
        # predicate lets the same caller read/update/delete it afterwards. Without
        # this the column falls back to the schema default ('CUI'), which is
        # invisible to a higher-clearance caller (e.g. the TOP SECRET//SCI E2E
        # auth-bypass context) and makes the create->delete roundtrip 404.
        ctx = getattr(conn, "_security_context", None)
        classification = getattr(ctx, "classification", None) or "CUI"
        conn.execute(
            """INSERT INTO studio_workflows
               (workflow_id, name, description, template_yaml, category,
                created_by, created_at, updated_at, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (wf_id, name, description, template_yaml, category, created_by, now, now,
             classification),
        )
        conn.commit()
        return {"status": "ok", "workflow_id": wf_id}
    finally:
        conn.close()


def save_workflow(
    workflow_id: str,
    name: str,
    template_yaml: str,
    *,
    category: str = "general",
    description: str = "",
    created_by: str = "studio",
) -> None:
    """Persist a workflow with a caller-supplied workflow_id."""
    now = _now_iso()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_workflows
               (workflow_id, name, description, template_yaml, category,
                created_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (workflow_id, name, description, template_yaml, category, created_by, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def update_workflow(
    workflow_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    template_yaml: str | None = None,
    category: str | None = None,
    shared: bool | None = None,
) -> dict:
    """Update an existing workflow."""
    existing = get_workflow(workflow_id)
    if not existing:
        return {"status": "error", "error": "Workflow not found"}

    if template_yaml is not None:
        try:
            parsed = yaml.safe_load(template_yaml)
            if not isinstance(parsed, dict) or "steps" not in parsed:
                return {"status": "error", "error": "YAML must contain 'steps' key"}
        except yaml.YAMLError as exc:
            return {"status": "error", "error": f"Invalid YAML: {exc}"}

    sets: list[str] = []
    vals: list[Any] = []
    if name is not None:
        sets.append("name = ?")
        vals.append(name)
    if description is not None:
        sets.append("description = ?")
        vals.append(description)
    if template_yaml is not None:
        sets.append("template_yaml = ?")
        vals.append(template_yaml)
        sets.append("version = version + 1")
    if category is not None:
        sets.append("category = ?")
        vals.append(category)
    if shared is not None:
        sets.append("shared = ?")
        vals.append(1 if shared else 0)

    if not sets:
        return {"status": "ok", "workflow_id": workflow_id, "message": "Nothing to update"}

    sets.append("updated_at = ?")
    vals.append(_now_iso())
    vals.append(workflow_id)

    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE studio_workflows SET {', '.join(sets)} WHERE workflow_id = %s",  # noqa: S608  # nosec B608 — column names are hardcoded, not user input
            vals,
        )
        conn.commit()
        return {"status": "ok", "workflow_id": workflow_id}
    finally:
        conn.close()


def delete_workflow(workflow_id: str) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT workflow_id FROM studio_workflows WHERE workflow_id = %s",
            (workflow_id,),
        ).fetchone()
        if not row:
            return {"status": "error", "error": "Workflow not found"}
        conn.execute(
            "DELETE FROM studio_workflows WHERE workflow_id = %s",
            (workflow_id,),
        )
        conn.commit()
        return {"status": "ok", "deleted": workflow_id}
    finally:
        conn.close()


def workflow_to_composer_format(workflow_id: str) -> dict | None:
    """Convert a studio workflow to the format expected by workflow_composer.py."""
    wf = get_workflow(workflow_id)
    if not wf:
        return None
    try:
        return yaml.safe_load(wf["template_yaml"])
    except yaml.YAMLError:
        return None


# ── Built-in template catalog ──────────────────────────────


def list_builtin_templates() -> list[dict]:
    """List YAML templates from args/workflow_templates/."""
    templates_dir = _ROOT / "args" / "workflow_templates"
    if not templates_dir.exists():
        return []

    results = []
    for f in sorted(templates_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            results.append(
                {
                    "id": f.stem,
                    "name": data.get("description", f.stem),
                    "category": data.get("category", "general"),
                    "steps_count": len(data.get("steps", [])),
                    "file_path": str(f),
                    "source": "builtin",
                }
            )
        except Exception:
            continue
    return results


def get_builtin_template(template_id: str) -> dict | None:
    """Return full template data (including steps) for a given template stem ID."""
    templates_dir = _ROOT / "args" / "workflow_templates"
    f = templates_dir / f"{template_id}.yaml"
    if not f.exists():
        return None
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        return {
            "id": f.stem,
            "name": data.get("description", f.stem),
            "category": data.get("category", "general"),
            "steps": data.get("steps", []),
            "source": "builtin",
        }
    except Exception:
        return None


# ── Triggers panel (dwo-evt-04-d5) ─────────────────────────


def build_triggers_panel(workflow_id: str) -> dict:
    """Everything the editor's Triggers tab needs, in one call.

    Sources and triggers are fetched together because the panel is useless with
    either half missing: binding a trigger requires choosing a source, and the
    bound list has to name its source rather than show a bare id.

    Degrades to empty lists rather than raising. The event tables arrive with
    migration 304, so an editor opened against a database that has not been
    migrated yet should render an empty panel, not a 500 that looks like the
    workflow itself is broken.
    """
    try:
        from tools.studio.event_sources import list_event_sources, list_workflow_triggers
    except Exception as exc:
        return {
            "workflow_id": workflow_id, "sources": [], "triggers": [],
            "available": False, "error": f"event sources unavailable: {exc}",
        }

    # Failures degrade to an empty panel but are *reported*. An empty list and a
    # broken query look identical in the UI, and a panel that silently shows
    # "no sources" when the query actually raised sends you looking for missing
    # data instead of the exception.
    errors: list[str] = []
    try:
        sources = list_event_sources()
    except Exception as exc:
        sources, _ = [], errors.append(f"sources: {exc}")
    try:
        triggers = list_workflow_triggers(workflow_id)
    except Exception as exc:
        triggers, _ = [], errors.append(f"triggers: {exc}")

    by_id = {s.get("source_id"): s for s in sources}
    for trig in triggers:
        source = by_id.get(trig.get("source_id")) or {}
        trig["source_name"] = source.get("name") or trig.get("source_id") or ""
        trig["source_channel"] = source.get("channel") or ""

    return {
        "workflow_id": workflow_id,
        "sources": sources,
        "triggers": triggers,
        # False only when the module itself is unavailable — an empty but
        # working panel is a different state from a missing subsystem, and the
        # UI says so rather than showing "no triggers" for both.
        "available": True,
        "error": "; ".join(errors),
    }


# ── CLI ────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Studio Workflow Editor")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("catalog", help="Show tool catalog")
    sub.add_parser("templates", help="List built-in templates")
    sub.add_parser("list", help="List studio workflows")

    p_get = sub.add_parser("get", help="Get workflow by ID")
    p_get.add_argument("workflow_id")

    args = parser.parse_args()

    result: Any = None
    if args.command == "catalog":
        result = get_tool_catalog()
    elif args.command == "templates":
        result = list_builtin_templates()
    elif args.command == "list":
        result = list_workflows()
    elif args.command == "get":
        result = get_workflow(args.workflow_id)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
