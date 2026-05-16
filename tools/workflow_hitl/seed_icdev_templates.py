# CUI // SP-CTI
"""Seed ICDEV™ system workflow templates, doc templates, and team templates.

Run once (idempotent — skips rows that already exist by name+is_system):
    python tools/workflow_hitl/seed_icdev_templates.py
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ── Workflow Templates ────────────────────────────────────────────────────────

WORKFLOW_TEMPLATES: list[dict] = [
    # ── 1. Program Manager: Feature Epic Lifecycle ────────────────────────────
    {
        "id": "sys-wft-pm-epic",
        "name": "PM: Feature Epic Lifecycle",
        "canvas_type": None,
        "approval_policy": "any_one",
        "kickback_limit": 3,
        "is_default": 0,
        "stages": [
            {"name": "Intake & Scope",     "step_type": "manual",    "role": "pm",
             "description": "Define epic scope, acceptance criteria, and initial story map."},
            {"name": "Decompose to Tasks", "step_type": "automated",
             "description": "AI decomposes epic into atomic kanban tasks (FORGE plan-decompose)."},
            {"name": "Architecture Review","step_type": "manual",    "role": "architect",
             "description": "Solution architect validates design decisions and ADRs."},
            {"name": "Assign & Kick Off",  "step_type": "manual",    "role": "pm",
             "description": "PM assigns tasks, sets priorities, and opens the sprint."},
            {"name": "Execute (Build)",    "step_type": "automated",
             "description": "Engineers build in worktree-isolated tasks; kanban scheduler tracks progress."},
            {"name": "Code Review",        "step_type": "manual",    "role": "reviewer",
             "description": "Peer code review with CodeLens quality gate (min 80 score)."},
            {"name": "QA Sign-off",        "step_type": "manual",    "role": "qa_lead",
             "description": "E2E test suite passes; coverage gate ≥ 80%."},
            {"name": "PM Close",           "step_type": "manual",    "role": "pm",
             "description": "PM validates acceptance criteria, closes epic, publishes feature doc."},
        ],
        "roles": {
            "Intake & Scope": "pm",
            "Architecture Review": "architect",
            "Assign & Kick Off": "pm",
            "Code Review": "reviewer",
            "QA Sign-off": "qa_lead",
            "PM Close": "pm",
        },
    },

    # ── 2. DevSecOps Engineer: CI/CD Security Gate ───────────────────────────
    {
        "id": "sys-wft-devsecops-cicd",
        "name": "DevSecOps: CI/CD Security Gate",
        "canvas_type": "PDC",
        "approval_policy": "all_must",
        "kickback_limit": 2,
        "is_default": 1,
        "stages": [
            {"name": "Code Commit",        "step_type": "automated",
             "description": "Developer pushes feature branch; pre-commit hook runs lint + bandit."},
            {"name": "SAST Scan",          "step_type": "automated",
             "description": "Bandit + Semgrep scan; blocks on CAT1 STIG / critical findings."},
            {"name": "Dependency Audit",   "step_type": "automated",
             "description": "pip-audit + SBOM regeneration; blocks on CVSS ≥ 9.0."},
            {"name": "Container Scan",     "step_type": "automated",
             "description": "Trivy image scan; non-root, read-only rootfs enforced."},
            {"name": "Integration Tests",  "step_type": "automated",
             "description": "pytest + behave BDD suite against real DB (no mocks)."},
            {"name": "Security Review",    "step_type": "manual",    "role": "security_analyst",
             "description": "Security analyst validates scan results and signs off."},
            {"name": "Deploy Gate",        "step_type": "manual",    "role": "system_owner",
             "description": "System owner approves production deployment."},
            {"name": "Post-Deploy Monitor","step_type": "automated",
             "description": "Health check + Prometheus alert baseline check (5-min soak)."},
        ],
        "roles": {
            "Security Review": "security_analyst",
            "Deploy Gate": "system_owner",
        },
    },

    # ── 3. Compliance Officer: RMF ATO Sprint ────────────────────────────────
    {
        "id": "sys-wft-compliance-rmf",
        "name": "Compliance: RMF ATO Sprint",
        "canvas_type": "SDC",
        "approval_policy": "sequential",
        "kickback_limit": 5,
        "is_default": 1,
        "stages": [
            {"name": "FIPS 199 Categorize","step_type": "automated",
             "description": "Run security_categorization tool — derive CIA impact levels, high-watermark baseline."},
            {"name": "Select Controls",    "step_type": "manual",    "role": "isso",
             "description": "ISSO tailors NIST 800-53 baseline; crosswalk engine populates FedRAMP/CMMC."},
            {"name": "Implement Controls", "step_type": "manual",    "role": "engineer",
             "description": "Engineers implement technical controls; evidence artifacts generated."},
            {"name": "Assess",             "step_type": "manual",    "role": "security_analyst",
             "description": "Security assessor validates control implementation against SSP."},
            {"name": "ISSM Review",        "step_type": "manual",    "role": "issm",
             "description": "ISSM reviews POAM, residual risk, and authorizes package submission."},
            {"name": "AO Authorize",       "step_type": "manual",    "role": "authorizing_official",
             "description": "Authorizing Official grants ATO; sets next assessment window."},
            {"name": "ConMon Baseline",    "step_type": "automated",
             "description": "Establish continuous monitoring baseline in drift_detector."},
        ],
        "roles": {
            "Select Controls": "isso",
            "Implement Controls": "engineer",
            "Assess": "security_analyst",
            "ISSM Review": "issm",
            "AO Authorize": "authorizing_official",
        },
    },

    # ── 4. Solution Architect: ADR Review ────────────────────────────────────
    {
        "id": "sys-wft-architect-adr",
        "name": "Architect: ADR Review & Approval",
        "canvas_type": None,
        "approval_policy": "majority",
        "kickback_limit": 3,
        "is_default": 0,
        "stages": [
            {"name": "Draft ADR",          "step_type": "manual",    "role": "architect",
             "description": "Author drafts ADR with context, options, pros/cons per Karpathy principles."},
            {"name": "Peer Technical Review","step_type": "manual",  "role": "peer_reviewer",
             "description": "Peer engineers review technical feasibility and assumptions."},
            {"name": "Security Review",    "step_type": "manual",    "role": "security_analyst",
             "description": "Security analyst evaluates OWASP/ATLAS threat surface of decision."},
            {"name": "Architecture Board", "step_type": "manual",    "role": "arch_board",
             "description": "Architecture board votes (majority required); records dissent."},
            {"name": "Record & Publish",   "step_type": "automated",
             "description": "ADR committed to docs/reference/adrs.md; coherence_checker updated."},
        ],
        "roles": {
            "Draft ADR": "architect",
            "Peer Technical Review": "peer_reviewer",
            "Security Review": "security_analyst",
            "Architecture Board": "arch_board",
        },
    },

    # ── 5. Engineer: TDD Feature Build ───────────────────────────────────────
    {
        "id": "sys-wft-engineer-tdd",
        "name": "Engineer: TDD Feature Build (RED-GREEN-REFACTOR)",
        "canvas_type": None,
        "approval_policy": "all_must",
        "kickback_limit": 3,
        "is_default": 1,
        "stages": [
            {"name": "RED — Failing Tests","step_type": "manual",    "role": "engineer",
             "description": "Write Gherkin feature file + pytest/behave step definitions. CI must be RED."},
            {"name": "GREEN — Implement",  "step_type": "manual",    "role": "engineer",
             "description": "Implement minimum code to make tests pass. No premature abstraction."},
            {"name": "REFACTOR",           "step_type": "manual",    "role": "engineer",
             "description": "Clean up; run CodeLens quality gate (cyclomatic complexity ≤ 10)."},
            {"name": "Peer Code Review",   "step_type": "manual",    "role": "reviewer",
             "description": "Peer review: correctness, security, naming, no dead code."},
            {"name": "Security Review",    "step_type": "manual",    "role": "security_analyst",
             "description": "SAST + dependency audit; security analyst signs off."},
            {"name": "Merge Gate",         "step_type": "automated",
             "description": "All CI checks green; companion sync runs; coherence check passes."},
        ],
        "roles": {
            "RED — Failing Tests": "engineer",
            "GREEN — Implement": "engineer",
            "REFACTOR": "engineer",
            "Peer Code Review": "reviewer",
            "Security Review": "security_analyst",
        },
    },

    # ── 6. Security Analyst: Vulnerability Remediation ───────────────────────
    {
        "id": "sys-wft-security-vuln",
        "name": "Security: Vulnerability Remediation",
        "canvas_type": "SDC",
        "approval_policy": "sequential",
        "kickback_limit": 2,
        "is_default": 0,
        "stages": [
            {"name": "Detect & Alert",     "step_type": "automated",
             "description": "pip-audit / Trivy / Semgrep detects finding; auto-creates kanban task."},
            {"name": "Triage",             "step_type": "manual",    "role": "security_analyst",
             "description": "Security analyst classifies CVSS severity, exploitability, and blast radius."},
            {"name": "Assign Fix",         "step_type": "manual",    "role": "isso",
             "description": "ISSO assigns fix to engineer with SLA (CAT1: 24h, CAT2: 72h, CAT3: 30d)."},
            {"name": "Implement Fix",      "step_type": "manual",    "role": "engineer",
             "description": "Engineer patches dependency or code; runs targeted regression tests."},
            {"name": "Verify",             "step_type": "manual",    "role": "security_analyst",
             "description": "Security analyst re-scans and confirms finding is resolved."},
            {"name": "ISSO Close",         "step_type": "manual",    "role": "isso",
             "description": "ISSO closes POAM item; updates continuous monitoring evidence."},
        ],
        "roles": {
            "Triage": "security_analyst",
            "Assign Fix": "isso",
            "Implement Fix": "engineer",
            "Verify": "security_analyst",
            "ISSO Close": "isso",
        },
    },

    # ── 7. QA Engineer: E2E Test Signoff ─────────────────────────────────────
    {
        "id": "sys-wft-qa-e2e",
        "name": "QA: E2E Test Signoff",
        "canvas_type": None,
        "approval_policy": "any_one",
        "kickback_limit": 3,
        "is_default": 0,
        "stages": [
            {"name": "Test Plan",          "step_type": "manual",    "role": "qa_lead",
             "description": "QA lead authors test plan: scope, personas, happy path + edge cases."},
            {"name": "Author Test Suite",  "step_type": "manual",    "role": "engineer",
             "description": "Engineer writes Selenium/Playwright E2E specs against feature acceptance criteria."},
            {"name": "Execute Suite",      "step_type": "automated",
             "description": "e2e_runner.py runs all specs in headless Chrome; captures screenshots."},
            {"name": "Defect Review",      "step_type": "manual",    "role": "qa_lead",
             "description": "QA lead reviews failures; triages bugs vs. test issues."},
            {"name": "Coverage Gate",      "step_type": "automated",
             "description": "Coverage ≥ 80%; critical paths 100% covered. Blocks on failure."},
            {"name": "QA Signoff",         "step_type": "manual",    "role": "qa_lead",
             "description": "QA lead signs off; feature marked ready for PM close."},
        ],
        "roles": {
            "Test Plan": "qa_lead",
            "Author Test Suite": "engineer",
            "Defect Review": "qa_lead",
            "QA Signoff": "qa_lead",
        },
    },

    # ── 8. GovCon Capture Manager: Proposal Response ─────────────────────────
    {
        "id": "sys-wft-govcon-proposal",
        "name": "GovCon: Proposal Response Lifecycle",
        "canvas_type": "BDC",
        "approval_policy": "all_must",
        "kickback_limit": 4,
        "is_default": 1,
        "stages": [
            {"name": "SAM.gov Qualify",    "step_type": "automated",
             "description": "Scanner identifies opportunity; NAICS match, set-aside check, ICDEV™ fit score ≥ 70."},
            {"name": "Bid / No-Bid",       "step_type": "manual",    "role": "capture_manager",
             "description": "Capture manager runs B/N-B matrix; documents go/no-go rationale."},
            {"name": "Solution Design",    "step_type": "manual",    "role": "technical_sme",
             "description": "Technical SME maps ICDEV™ capabilities to PWS requirements; gap analysis."},
            {"name": "Draft Proposal",     "step_type": "manual",    "role": "proposal_writer",
             "description": "Writer drafts Technical, Management, and Price volumes per RFP format."},
            {"name": "Technical Review",   "step_type": "manual",    "role": "technical_sme",
             "description": "SME validates technical accuracy; compliance matrix complete."},
            {"name": "Compliance Review",  "step_type": "manual",    "role": "compliance_officer",
             "description": "Compliance officer validates CUI markings, CMMC flow-down, FAR clauses."},
            {"name": "PM Final Approve",   "step_type": "manual",    "role": "pm",
             "description": "PM approves submission package; triggers SAM.gov/email delivery."},
        ],
        "roles": {
            "Bid / No-Bid": "capture_manager",
            "Solution Design": "technical_sme",
            "Draft Proposal": "proposal_writer",
            "Technical Review": "technical_sme",
            "Compliance Review": "compliance_officer",
            "PM Final Approve": "pm",
        },
    },

    # ── 9. ML/Data Engineer: AI Model Governance ─────────────────────────────
    {
        "id": "sys-wft-ai-governance",
        "name": "AI/ML: Model Governance & Production Gate",
        "canvas_type": "DDC",
        "approval_policy": "sequential",
        "kickback_limit": 3,
        "is_default": 0,
        "stages": [
            {"name": "Requirements",       "step_type": "manual",    "role": "ml_engineer",
             "description": "Define model purpose, data sources, fairness constraints, NIST AI 600-1 scope."},
            {"name": "Data Quality Gate",  "step_type": "automated",
             "description": "Data profiler validates completeness, bias indicators, lineage provenance."},
            {"name": "Train & Evaluate",   "step_type": "manual",    "role": "ml_engineer",
             "description": "Train model; log metrics, hyperparams, and evaluation results."},
            {"name": "Bias Assessment",    "step_type": "manual",    "role": "ai_safety_reviewer",
             "description": "Run fairness evaluation; document demographic parity, equalized odds."},
            {"name": "Confabulation Check","step_type": "automated",
             "description": "Run confabulation_detector on model outputs against ground truth."},
            {"name": "Model Card",         "step_type": "manual",    "role": "ml_engineer",
             "description": "Author NIST AI 600-1 model card; AI inventory entry created."},
            {"name": "Production Gate",    "step_type": "manual",    "role": "system_owner",
             "description": "System owner approves deployment; ATLAS threat model reviewed."},
        ],
        "roles": {
            "Requirements": "ml_engineer",
            "Train & Evaluate": "ml_engineer",
            "Bias Assessment": "ai_safety_reviewer",
            "Model Card": "ml_engineer",
            "Production Gate": "system_owner",
        },
    },

    # ── 10. System Owner: cATO Continuous Monitoring ─────────────────────────
    {
        "id": "sys-wft-cato-conmon",
        "name": "System Owner: cATO Continuous Monitoring Cycle",
        "canvas_type": "SDC",
        "approval_policy": "sequential",
        "kickback_limit": 2,
        "is_default": 0,
        "stages": [
            {"name": "Baseline Snapshot",  "step_type": "automated",
             "description": "drift_detector.py captures component baseline; stores in awareness graph."},
            {"name": "Automated Scan",     "step_type": "automated",
             "description": "pip-audit + Trivy + Semgrep nightly scan; findings written to POAM."},
            {"name": "Drift Detection",    "step_type": "automated",
             "description": "Compares current state vs baseline; flags regressions → kanban tasks."},
            {"name": "Remediate",          "step_type": "manual",    "role": "engineer",
             "description": "Engineer fixes flagged drift items within SLA windows."},
            {"name": "Evidence Package",   "step_type": "manual",    "role": "isso",
             "description": "ISSO compiles evidence artifacts (logs, scan reports, test results)."},
            {"name": "AO Re-Authorize",    "step_type": "manual",    "role": "authorizing_official",
             "description": "AO reviews delta evidence; re-affirms cATO or escalates to full ATO."},
        ],
        "roles": {
            "Remediate": "engineer",
            "Evidence Package": "isso",
            "AO Re-Authorize": "authorizing_official",
        },
    },
]


# ── Document Templates ────────────────────────────────────────────────────────

DOC_TEMPLATES: list[dict] = [
    {
        "id": "sys-dt-peer-review-checklist",
        "name": "Peer Code Review Checklist",
        "doc_type": "checklist",
        "canvas_type": None,
        "stage_scope": "Code Review",
        "is_ai_reference": False,
        "is_human_required": True,
        "schema": [
            {"item": "Logic correctness verified", "required": True},
            {"item": "No hardcoded secrets or credentials", "required": True},
            {"item": "All new functions have docstrings or clear naming", "required": False},
            {"item": "No unused imports or dead code", "required": True},
            {"item": "Tests cover happy path and at least one edge case", "required": True},
            {"item": "No SQL injection / XSS / command injection risk", "required": True},
            {"item": "Error handling only at system boundaries", "required": False},
            {"item": "Companion sync and coherence check passed", "required": True},
        ],
    },
    {
        "id": "sys-dt-security-sign-off",
        "name": "Security Gate Sign-Off Form",
        "doc_type": "form",
        "canvas_type": "SDC",
        "stage_scope": "Security Review",
        "is_ai_reference": False,
        "is_human_required": True,
        "schema": [
            {"item": "SAST scan: no CAT1 STIG findings", "required": True},
            {"item": "Dependency audit: no CVSS ≥ 9.0 unmitigated", "required": True},
            {"item": "Container scan: non-root, read-only rootfs", "required": True},
            {"item": "OWASP Top 10 checklist reviewed", "required": True},
            {"item": "ATLAS AI threat surface assessed (if AI component)", "required": False},
            {"item": "Security analyst signature", "required": True},
            {"item": "Date of review", "required": True},
        ],
    },
    {
        "id": "sys-dt-nist-control-evidence",
        "name": "NIST 800-53 Control Implementation Evidence",
        "doc_type": "checklist",
        "canvas_type": "SDC",
        "stage_scope": "Assess",
        "is_ai_reference": True,
        "is_human_required": True,
        "schema": [
            {"item": "Control ID and name documented", "required": True},
            {"item": "Implementation description written", "required": True},
            {"item": "Test procedure documented", "required": True},
            {"item": "Test result (pass/fail/partial)", "required": True},
            {"item": "Evidence artifact path or reference", "required": True},
            {"item": "Assessor name and date", "required": True},
        ],
    },
    {
        "id": "sys-dt-adr-template",
        "name": "Architectural Decision Record (ADR)",
        "doc_type": "sop_reference",
        "canvas_type": None,
        "stage_scope": "Draft ADR",
        "is_ai_reference": True,
        "is_human_required": False,
        "schema": [
            {"item": "Title and ADR number", "required": True},
            {"item": "Status (proposed/accepted/deprecated)", "required": True},
            {"item": "Context: problem statement and constraints", "required": True},
            {"item": "Options considered (≥ 2)", "required": True},
            {"item": "Decision and rationale", "required": True},
            {"item": "Consequences (positive and negative)", "required": True},
            {"item": "Security/compliance implications noted", "required": False},
        ],
    },
    {
        "id": "sys-dt-e2e-test-report",
        "name": "E2E Test Execution Report",
        "doc_type": "form",
        "canvas_type": None,
        "stage_scope": "QA Signoff",
        "is_ai_reference": False,
        "is_human_required": True,
        "schema": [
            {"item": "Test suite name and version", "required": True},
            {"item": "Total tests: pass / fail / skip counts", "required": True},
            {"item": "Coverage percentage (overall)", "required": True},
            {"item": "Critical path coverage (must be 100%)", "required": True},
            {"item": "Screenshots directory reference", "required": False},
            {"item": "Known defects list (P1/P2/P3)", "required": True},
            {"item": "QA lead sign-off", "required": True},
        ],
    },
    {
        "id": "sys-dt-vuln-triage",
        "name": "Vulnerability Triage Form",
        "doc_type": "form",
        "canvas_type": "SDC",
        "stage_scope": "Triage",
        "is_ai_reference": False,
        "is_human_required": True,
        "schema": [
            {"item": "CVE ID or finding reference", "required": True},
            {"item": "CVSS score and vector", "required": True},
            {"item": "Affected component and version", "required": True},
            {"item": "Exploitability assessment (network accessible?)", "required": True},
            {"item": "Blast radius (systems impacted)", "required": True},
            {"item": "Assigned SLA tier (CAT1/CAT2/CAT3)", "required": True},
            {"item": "Assigned engineer and due date", "required": True},
        ],
    },
    {
        "id": "sys-dt-proposal-review",
        "name": "Proposal Review Checklist (GovCon)",
        "doc_type": "checklist",
        "canvas_type": "BDC",
        "stage_scope": "Technical Review",
        "is_ai_reference": True,
        "is_human_required": True,
        "schema": [
            {"item": "All PWS requirements addressed in technical volume", "required": True},
            {"item": "Compliance matrix completed", "required": True},
            {"item": "CUI markings on all sensitive pages", "required": True},
            {"item": "CMMC flow-down requirements documented", "required": True},
            {"item": "FAR/DFARS clauses cited where applicable", "required": True},
            {"item": "No backend tooling or pricing disclosed", "required": True},
            {"item": "Page count and format compliance verified", "required": True},
        ],
    },
    {
        "id": "sys-dt-ai-model-card",
        "name": "AI Model Card (NIST AI 600-1)",
        "doc_type": "standard",
        "canvas_type": None,
        "stage_scope": "Model Card",
        "is_ai_reference": True,
        "is_human_required": False,
        "schema": [
            {"item": "Model name, version, and intended use", "required": True},
            {"item": "Training data sources and lineage", "required": True},
            {"item": "Performance metrics on evaluation set", "required": True},
            {"item": "Fairness metrics (demographic parity, equalized odds)", "required": True},
            {"item": "Known limitations and out-of-scope uses", "required": True},
            {"item": "Confabulation risk assessment", "required": True},
            {"item": "Human oversight requirements", "required": True},
            {"item": "AI inventory entry created", "required": True},
        ],
    },
]


# ── Team Templates ────────────────────────────────────────────────────────────

TEAM_TEMPLATES: list[dict] = [
    {
        "id": "sys-team-forge-sprint",
        "name": "FORGE Sprint Team",
        "description": "Full SDLC team for FORGE framework feature delivery. Covers intake through deployment using ANVIL workflow.",
        "canvas_type": None,
        "suggested_roles": [
            {"role_label": "pm",               "description": "Program Manager — owns epic scope, sprint cadence, and stakeholder comms"},
            {"role_label": "architect",         "description": "Solution Architect — FORGE layer decisions, ADRs, system design"},
            {"role_label": "engineer",          "description": "Full-Stack Engineer — TDD implementation across Python/Go/TypeScript"},
            {"role_label": "engineer",          "description": "Full-Stack Engineer — second engineer for pair review and parallel tasks"},
            {"role_label": "devops_engineer",   "description": "DevSecOps Engineer — CI/CD pipeline, security gates, container hardening"},
            {"role_label": "qa_lead",           "description": "QA Lead — E2E test suite, coverage gates, release sign-off"},
        ],
        "suggested_templates": ["sys-wft-pm-epic", "sys-wft-engineer-tdd"],
    },
    {
        "id": "sys-team-ato-tiger",
        "name": "ATO Tiger Team",
        "description": "Rapid ATO pursuit team. Focused on RMF acceleration — from FIPS 199 categorization to ATO package submission.",
        "canvas_type": "SDC",
        "suggested_roles": [
            {"role_label": "isso",                  "description": "ISSO — owns RMF process, SSP, POAM, and control selection"},
            {"role_label": "issm",                  "description": "ISSM — senior oversight, risk acceptance, AO interface"},
            {"role_label": "security_analyst",      "description": "Security Assessor — independent control validation and test execution"},
            {"role_label": "engineer",              "description": "Control Implementation Engineer — technical control implementation"},
            {"role_label": "authorizing_official",  "description": "AO Representative — final authorization decision"},
        ],
        "suggested_templates": ["sys-wft-compliance-rmf", "sys-wft-security-vuln"],
    },
    {
        "id": "sys-team-devsecops",
        "name": "DevSecOps Core Team",
        "description": "Security-first development operations team. Owns the CI/CD pipeline, vulnerability management, and continuous monitoring.",
        "canvas_type": "PDC",
        "suggested_roles": [
            {"role_label": "devops_engineer",  "description": "DevSecOps Lead — pipeline architecture, SAST/DAST toolchain, SBOM"},
            {"role_label": "security_analyst", "description": "Security Analyst — vulnerability triage, STIG review, OWASP assessment"},
            {"role_label": "qa_lead",          "description": "QA Lead — integration and regression test coverage"},
            {"role_label": "pm",               "description": "PM — sprint planning, SLA tracking, stakeholder reporting"},
        ],
        "suggested_templates": ["sys-wft-devsecops-cicd", "sys-wft-security-vuln", "sys-wft-cato-conmon"],
    },
    {
        "id": "sys-team-govcon",
        "name": "GovCon Pursuit Team",
        "description": "Capture-to-delivery team for federal proposal pursuits. Handles SAM.gov opportunity qualification through proposal submission.",
        "canvas_type": "BDC",
        "suggested_roles": [
            {"role_label": "capture_manager",   "description": "Capture Manager — opportunity qualification, bid/no-bid, pipeline management"},
            {"role_label": "technical_sme",     "description": "Technical SME — ICDEV™ capability mapping, PWS solution design"},
            {"role_label": "proposal_writer",   "description": "Proposal Writer — Technical, Management, and Price volumes"},
            {"role_label": "compliance_officer","description": "Compliance Officer — CUI markings, CMMC flow-down, FAR compliance"},
            {"role_label": "pm",                "description": "PM — schedule, resource allocation, final submission approval"},
        ],
        "suggested_templates": ["sys-wft-govcon-proposal"],
    },
    {
        "id": "sys-team-modernization",
        "name": "Modernization Team",
        "description": "Legacy application modernization team applying the 7Rs framework. From assessment through re-platform or re-architect delivery.",
        "canvas_type": None,
        "suggested_roles": [
            {"role_label": "architect",        "description": "Lead Architect — 7Rs assessment, strangler-fig pattern, ATO bridge"},
            {"role_label": "engineer",         "description": "Migration Engineer — code translation, API adapter, test harness"},
            {"role_label": "engineer",         "description": "Migration Engineer — parallel implementation and regression coverage"},
            {"role_label": "security_analyst", "description": "Security Analyst — vulnerability debt assessment, remediation gating"},
            {"role_label": "qa_lead",          "description": "QA Lead — migration parity testing, E2E regression suite"},
            {"role_label": "pm",               "description": "PM — milestone tracking, stakeholder comms, ATO timeline"},
        ],
        "suggested_templates": ["sys-wft-architect-adr", "sys-wft-qa-e2e"],
    },
    {
        "id": "sys-team-ai-governance",
        "name": "AI Governance Team",
        "description": "Responsible AI team ensuring NIST AI 600-1, OMB M-25-21, and ATLAS compliance for all AI/ML components in production.",
        "canvas_type": "DDC",
        "suggested_roles": [
            {"role_label": "ml_engineer",       "description": "ML Engineer — model development, evaluation, model card authoring"},
            {"role_label": "data_engineer",     "description": "Data Engineer — lineage, quality gates, provenance tracking"},
            {"role_label": "ai_safety_reviewer","description": "AI Safety Reviewer — bias assessment, confabulation detection, ATLAS review"},
            {"role_label": "isso",              "description": "ISSO — AI system ATO, continuous monitoring, incident response"},
            {"role_label": "system_owner",      "description": "System Owner — production deployment approval, risk acceptance"},
        ],
        "suggested_templates": ["sys-wft-ai-governance"],
    },
]


# ── Seed Logic ────────────────────────────────────────────────────────────────

def _upsert_workflow_template(conn, t: dict) -> None:
    existing = conn.execute(
        "SELECT id FROM wf_templates WHERE id=%s", (t["id"],)
    ).fetchone()
    if existing:
        print(f"  [skip] workflow template '{t['name']}' already exists")
        return

    stages = json.dumps([
        {"name": s["name"], "step_type": s["step_type"],
         **({"role": s["role"]} if "role" in s else {}),
         **({"description": s["description"]} if "description" in s else {})}
        for s in t["stages"]
    ])
    roles = json.dumps(t.get("roles", {}))
    now = _now()

    conn.execute(
        """INSERT INTO wf_templates
           (id, name, canvas_type, stages_json, roles_json, approval_policy,
            kickback_limit, is_default, is_system, created_by, created_at, updated_at)
           VALUES (%s,?,?,?,?,?,?,?,1,'system',?,%s)""",
        (t["id"], t["name"], t.get("canvas_type"), stages, roles,
         t["approval_policy"], t["kickback_limit"], t.get("is_default", 0), now, now),
    )
    print(f"  [+] workflow template '{t['name']}'")


def _upsert_doc_template(conn, d: dict) -> None:
    existing = conn.execute(
        "SELECT id FROM wf_document_templates WHERE id=%s", (d["id"],)
    ).fetchone()
    if existing:
        print(f"  [skip] doc template '{d['name']}' already exists")
        return

    schema = json.dumps(d.get("schema", []))
    now = _now()
    conn.execute(
        """INSERT INTO wf_document_templates
           (id, name, doc_type, canvas_type, stage_scope,
            schema_json, is_ai_reference, is_human_required, is_system,
            created_by, created_at)
           VALUES (%s,?,?,?,?,?,?,?,1,'system',%s)""",
        (d["id"], d["name"], d["doc_type"], d.get("canvas_type"),
         d.get("stage_scope"), schema,
         1 if d.get("is_ai_reference") else 0,
         1 if d.get("is_human_required") else 0,
         now),
    )
    print(f"  [+] doc template '{d['name']}'")


def _upsert_team(conn, t: dict) -> None:
    existing = conn.execute(
        "SELECT id FROM wf_teams WHERE id=%s", (t["id"],)
    ).fetchone()
    if existing:
        print(f"  [skip] team '{t['name']}' already exists")
        return

    now = _now()
    conn.execute(
        """INSERT INTO wf_teams
           (id, name, description, canvas_type, created_by, created_at)
           VALUES (%s,?,?,?,?,%s)""",
        (t["id"], t["name"], t.get("description"), t.get("canvas_type"), "system", now),
    )
    # Add suggested roles as placeholder members (user_id = role placeholder)
    for sr in t.get("suggested_roles", []):
        conn.execute(
            """INSERT OR IGNORE INTO wf_team_members
               (id, team_id, user_id, role_label, created_at)
               VALUES (%s,?,?,?,%s)""",
            (_id("wtm"), t["id"], f"<{sr['role_label']}>", sr["role_label"], now),
        )
    print(f"  [+] team '{t['name']}' ({len(t.get('suggested_roles', []))} roles)")


def seed() -> None:
    conn = get_connection()
    try:
        print("\n-- Workflow Templates --")
        for t in WORKFLOW_TEMPLATES:
            _upsert_workflow_template(conn, t)

        print("\n-- Document Templates --")
        for d in DOC_TEMPLATES:
            _upsert_doc_template(conn, d)

        print("\n-- Team Templates --")
        for t in TEAM_TEMPLATES:
            _upsert_team(conn, t)

        try:
            conn.commit()
        except Exception:
            pass
        print("\nSeed complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    seed()
