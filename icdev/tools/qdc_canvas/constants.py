# CUI // SP-CTI
"""Quality Design Canvas — Constants, palette objects, compliance rules."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# SA-11 enhancement mapping — maps ICDEV gate IDs to NIST 800-53 controls
# ---------------------------------------------------------------------------
QDC_SA11_MAP: dict[str, dict[str, str]] = {
    "sast": {"control": "SA-11(1)", "title": "Static Code Analysis", "family": "SA"},
    "threat": {"control": "SA-11(2)", "title": "Threat Modeling / Vulnerability Analysis", "family": "SA"},
    "independent": {"control": "SA-11(3)", "title": "Independent Verification", "family": "SA"},
    "review": {"control": "SA-11(4)", "title": "Manual Code Reviews", "family": "SA"},
    "pentest": {"control": "SA-11(5)", "title": "Penetration Testing", "family": "SA"},
    "surface": {"control": "SA-11(6)", "title": "Attack Surface Reviews", "family": "SA"},
    "coverage": {"control": "SA-11(7)", "title": "Verify Scope of Testing", "family": "SA"},
    "dast": {"control": "SA-11(8)", "title": "Dynamic Code Analysis", "family": "SA"},
    "iast": {"control": "SA-11(9)", "title": "Interactive Application Security Testing", "family": "SA"},
    "process": {"control": "SA-15", "title": "Development Process, Standards, and Tools", "family": "SA"},
    "assessment": {"control": "CA-2", "title": "Control Assessments", "family": "CA"},
    "runtime": {"control": "SI-6", "title": "Security and Privacy Function Verification", "family": "SI"},
    "integrity": {"control": "SI-7", "title": "Software, Firmware, and Information Integrity", "family": "SI"},
    "config": {"control": "CM-6", "title": "Configuration Settings", "family": "CM"},
    "diagram": {"control": "PL-2", "title": "System Security and Privacy Plans — Architecture Diagrams", "family": "PL"},
}

# ---------------------------------------------------------------------------
# Gate-to-tool mapping — which ICDEV tool implements each gate
# ---------------------------------------------------------------------------
QDC_GATE_TOOLS: dict[str, str | None] = {
    "sast": "tools/security/sast_runner.py",
    "threat": "tools/security_canvas/security_engine.py",
    "independent": "tools/testing/production_audit.py",
    "review": "goals/code_review.md",
    "pentest": None,  # Manual/external
    "surface": "tools/testing/api_surface_extractor.py",
    "coverage": "tools/testing/test_orchestrator.py",
    "dast": "tools/testing/e2e_runner.py",
    "iast": None,  # Manual/external
    "process": "tools/workflow/coherence_checker.py",
    "assessment": "tools/compliance/multi_regime_assessor.py",
    "runtime": "tools/monitor/health_checker.py",
    "integrity": "tools/security/blueprint_verifier.py",
    "config": "tools/compliance/stig_checker.py",
    "diagram": "tools/compliance/diagram_validator.py",
}

# ---------------------------------------------------------------------------
# Palette objects — drag-and-drop categories for the visual canvas
# ---------------------------------------------------------------------------
QDC_OBJECTS: dict = {
    "categories": {
        "quality_gates": [
            {"id": "gate-sast", "label": "SAST Gate", "type": "gate-sast"},
            {"id": "gate-dast", "label": "DAST Gate", "type": "gate-dast"},
            {"id": "gate-sca", "label": "SCA Gate", "type": "gate-sca"},
            {"id": "gate-secret", "label": "Secret Scan Gate", "type": "gate-secret"},
            {"id": "gate-container", "label": "Container Scan Gate", "type": "gate-container"},
            {"id": "gate-unit", "label": "Unit Test Gate", "type": "gate-unit"},
            {"id": "gate-bdd", "label": "BDD Test Gate", "type": "gate-bdd"},
            {"id": "gate-e2e", "label": "E2E Test Gate", "type": "gate-e2e"},
            {"id": "gate-fuzz", "label": "Fuzz Test Gate", "type": "gate-fuzz"},
            {"id": "gate-review", "label": "Code Review Gate", "type": "gate-review"},
            {"id": "gate-pentest", "label": "Pen Test Gate", "type": "gate-pentest"},
            {"id": "gate-iast", "label": "IAST Gate", "type": "gate-iast"},
            {"id": "gate-diagram", "label": "Diagram Quality Gate", "type": "gate-diagram"},
        ],
        "quality_sources": [
            {"id": "src-repo", "label": "Code Repository", "type": "src-repo"},
            {"id": "src-pipeline", "label": "CI/CD Pipeline", "type": "src-pipeline"},
            {"id": "src-registry", "label": "Container Registry", "type": "src-registry"},
            {"id": "src-testrunner", "label": "Test Runner", "type": "src-testrunner"},
            {"id": "src-scanner", "label": "Security Scanner", "type": "src-scanner"},
            {"id": "src-compliance", "label": "Compliance Engine", "type": "src-compliance"},
            {"id": "src-observability", "label": "Observability Stack", "type": "src-observability"},
            {"id": "src-health", "label": "Health Monitor", "type": "src-health"},
        ],
        "quality_consumers": [
            {"id": "con-uqs", "label": "UQS Dashboard", "type": "con-uqs"},
            {"id": "con-compliance", "label": "Compliance Report", "type": "con-compliance"},
            {"id": "con-cato", "label": "cATO Evidence Pack", "type": "con-cato"},
            {"id": "con-oscal", "label": "OSCAL Artifact", "type": "con-oscal"},
            {"id": "con-poam", "label": "POAM Generator", "type": "con-poam"},
            {"id": "con-trend", "label": "Trend Report", "type": "con-trend"},
        ],
        "cross_canvas": [
            {"id": "xc-idc", "label": "IDC Quality Link", "type": "xc-idc"},
            {"id": "xc-ndc", "label": "NDC Quality Link", "type": "xc-ndc"},
            {"id": "xc-sdc", "label": "SDC Quality Link", "type": "xc-sdc"},
            {"id": "xc-bdc", "label": "BDC Quality Link", "type": "xc-bdc"},
            {"id": "xc-pdc", "label": "PDC Quality Link", "type": "xc-pdc"},
            {"id": "xc-odc", "label": "ODC Quality Link", "type": "xc-odc"},
            {"id": "xc-ddc", "label": "DDC Quality Link", "type": "xc-ddc"},
        ],
        "compliance_targets": [
            {"id": "tgt-fedramp-mod", "label": "FedRAMP Moderate", "type": "tgt-fedramp-mod"},
            {"id": "tgt-fedramp-high", "label": "FedRAMP High", "type": "tgt-fedramp-high"},
            {"id": "tgt-cmmc-l2", "label": "CMMC Level 2", "type": "tgt-cmmc-l2"},
            {"id": "tgt-cmmc-l3", "label": "CMMC Level 3", "type": "tgt-cmmc-l3"},
            {"id": "tgt-cato", "label": "cATO Continuous", "type": "tgt-cato"},
            {"id": "tgt-stig", "label": "STIG Compliance", "type": "tgt-stig"},
            {"id": "tgt-il45", "label": "IL4/IL5 Baseline", "type": "tgt-il45"},
        ],
    }
}

# ---------------------------------------------------------------------------
# Node tooltip descriptions — one entry per node type across all categories
# ---------------------------------------------------------------------------
QDC_NODE_DESCS: dict[str, str] = {
    # quality_gates
    "gate-sast": "Static Application Security Testing — analyzes source code for vulnerabilities without executing it. Maps to NIST SA-11(1).",  # noqa: E501
    "gate-dast": "Dynamic Application Security Testing — tests running application for vulnerabilities. Maps to NIST SA-11(8).",  # noqa: E501
    "gate-sca": "Software Composition Analysis — scans third-party dependencies for known CVEs. Maps to NIST SA-11(2).",
    "gate-secret": "Secret Detection Gate — scans code and config for leaked credentials, tokens, and API keys. Maps to NIST SA-11(1).",  # noqa: E501
    "gate-container": "Container Scan Gate — analyzes container images for OS and library vulnerabilities. Maps to NIST SI-7.",  # noqa: E501
    "gate-unit": "Unit Test Gate — verifies individual functions and modules pass automated tests. Maps to NIST SA-11(7).",  # noqa: E501
    "gate-bdd": "BDD Test Gate — validates behavior-driven scenarios against acceptance criteria. Maps to NIST SA-11(7).",  # noqa: E501
    "gate-e2e": "E2E Test Gate — runs end-to-end browser and API integration tests. Maps to NIST SA-11(8).",
    "gate-fuzz": "Fuzz Test Gate — sends randomized inputs to discover crash and edge-case bugs. Maps to NIST SA-11(5).",  # noqa: E501
    "gate-review": "Code Review Gate — enforces peer review of all code changes before merge. Maps to NIST SA-11(4).",
    "gate-pentest": "Penetration Test Gate — tracks manual or automated pen-test execution and findings. Maps to NIST SA-11(5).",  # noqa: E501
    "gate-iast": "Interactive Application Security Testing — instruments the running application to detect vulnerabilities in real time. Maps to NIST SA-11(9).",  # noqa: E501
    "gate-diagram": "Diagram Quality Gate — validates infrastructure diagrams for ATO readiness: HA, network segmentation, encryption, logging, IAM, data tier isolation. CSP-agnostic (works with AWS, Azure, GCP, OCI, IBM). Maps to NIST PL-2.",  # noqa: E501
    # quality_sources
    "src-repo": "Code Repository — source control system providing code artifacts to quality gates.",
    "src-pipeline": "CI/CD Pipeline — automated build and deployment pipeline that triggers quality gates.",
    "src-registry": "Container Registry — stores and serves container images for scanning and deployment.",
    "src-testrunner": "Test Runner — orchestrates unit, integration, and BDD test execution.",
    "src-scanner": "Security Scanner — aggregated security scanning tools (SAST, DAST, SCA).",
    "src-compliance": "Compliance Engine — evaluates artifacts against NIST, FedRAMP, and CMMC controls. Maps to NIST CA-2.",  # noqa: E501
    "src-observability": "Observability Stack — collects logs, metrics, and traces for runtime quality monitoring. Maps to NIST SI-4.",  # noqa: E501
    "src-health": "Health Monitor — continuously checks service availability and performance baselines. Maps to NIST SI-6.",  # noqa: E501
    # quality_consumers
    "con-uqs": "Unified Quality Score Dashboard — aggregates all gate results into a single quality metric.",
    "con-compliance": "Compliance Report — generates formatted compliance evidence packages for auditors.",
    "con-cato": "cATO Evidence Pack — produces continuous Authority to Operate evidence bundles. Maps to NIST CA-6.",
    "con-oscal": "OSCAL Artifact — emits machine-readable OSCAL JSON for automated compliance exchange. Maps to NIST CA-2.",  # noqa: E501
    "con-poam": "POAM Generator — creates Plan of Action and Milestones from open findings. Maps to NIST CA-5.",
    "con-trend": "Trend Report — visualizes quality score trends over time for executive reporting.",
    # cross_canvas
    "xc-idc": "Infrastructure Design Canvas quality integration — links QDC gates to IDC infrastructure nodes.",
    "xc-ndc": "Network Design Canvas quality integration — links QDC gates to NDC network topology.",
    "xc-sdc": "Security Design Canvas quality integration — links QDC gates to SDC security controls.",
    "xc-bdc": "Boundary Design Canvas quality integration — links QDC gates to BDC ATO boundary.",
    "xc-pdc": "Pipeline Design Canvas quality integration — links QDC gates to PDC CI/CD stages.",
    "xc-odc": "Observability Design Canvas quality integration — links QDC gates to ODC monitoring.",
    "xc-ddc": "Data Design Canvas quality integration — links QDC gates to DDC data flows.",
    # compliance_targets
    "tgt-fedramp-mod": "FedRAMP Moderate baseline — 325 controls, suitable for CUI/IL4 workloads. Maps to NIST CA-2.",
    "tgt-fedramp-high": "FedRAMP High baseline — 421 controls, required for high-impact federal systems. Maps to NIST CA-2.",  # noqa: E501
    "tgt-cmmc-l2": "CMMC Level 2 — advanced cybersecurity practices aligned to NIST SP 800-171.",
    "tgt-cmmc-l3": "CMMC Level 3 — expert cybersecurity practices with additional NIST SP 800-172 controls.",
    "tgt-cato": "Continuous Authority to Operate — ongoing automated compliance monitoring. Maps to NIST CA-6.",
    "tgt-stig": "STIG Compliance — Defense Information Systems Agency security technical implementation guides. Maps to NIST CM-6.",  # noqa: E501
    "tgt-il45": "Impact Level 4/5 Baseline — DoD Cloud Computing SRG requirements for CUI workloads.",
}

# ---------------------------------------------------------------------------
# Compliance rules — checked by assess_quality_design()
# ---------------------------------------------------------------------------
QDC_COMPLIANCE_RULES: list[dict[str, str]] = [
    {
        "id": "QDC-001",
        "title": "SAST Gate Required",
        "severity": "CAT1",
        "category": "security",
        "check": "has_sast_gate",
    },
    {
        "id": "QDC-002",
        "title": "Unit Test Gate Required",
        "severity": "CAT1",
        "category": "testing",
        "check": "has_unit_gate",
    },
    {
        "id": "QDC-003",
        "title": "Code Review Gate Required",
        "severity": "CAT2",
        "category": "quality",
        "check": "has_review_gate",
    },
    {
        "id": "QDC-004",
        "title": "STIG Compliance Gate Required",
        "severity": "CAT2",
        "category": "compliance",
        "check": "has_stig_gate",
    },
    {
        "id": "QDC-005",
        "title": "SBOM Generation Required",
        "severity": "CAT2",
        "category": "compliance",
        "check": "has_sbom_node",
    },
    {
        "id": "QDC-006",
        "title": "Secret Detection Gate Required",
        "severity": "CAT1",
        "category": "security",
        "check": "has_secret_gate",
    },
    {
        "id": "QDC-007",
        "title": "UQS Consumer Required",
        "severity": "CAT3",
        "category": "quality",
        "check": "has_uqs_consumer",
    },
    {
        "id": "QDC-008",
        "title": "Cross-Canvas Link Recommended",
        "severity": "CAT3",
        "category": "integration",
        "check": "has_cross_canvas_link",
    },
    {
        "id": "QDC-009",
        "title": "Compliance Target Required",
        "severity": "CAT2",
        "category": "compliance",
        "check": "has_compliance_target",
    },
    {
        "id": "QDC-010",
        "title": "E2E Test Gate Recommended",
        "severity": "CAT3",
        "category": "testing",
        "check": "has_e2e_gate",
    },
    {
        "id": "QDC-011",
        "title": "All Gates Must Have Source",
        "severity": "CAT2",
        "category": "topology",
        "check": "gates_have_sources",
    },
    {
        "id": "QDC-012",
        "title": "No Orphan Nodes",
        "severity": "CAT3",
        "category": "topology",
        "check": "no_orphan_nodes",
    },
]

# ---------------------------------------------------------------------------
# UQS default dimension weights (must sum to 1.0)
# ---------------------------------------------------------------------------
QDC_UQS_DEFAULTS: dict[str, float] = {
    "code_quality": 0.20,
    "security_posture": 0.25,
    "test_coverage": 0.20,
    "runtime_health": 0.15,
    "compliance_readiness": 0.20,
}

_Q = "https://icdev.dev/ontology/quality#"

QDC_ONTOLOGY_MAP: dict[str, str] = {
    "gate-sast":      f"{_Q}QualityGate.SAST",
    "gate-dast":      f"{_Q}QualityGate.DAST",
    "gate-sca":       f"{_Q}QualityGate.SCA",
    "gate-secret":    f"{_Q}QualityGate.SecretScan",
    "gate-container": f"{_Q}QualityGate.ContainerScan",
    "gate-unit":      f"{_Q}QualityGate.UnitTest",
    "gate-bdd":       f"{_Q}QualityGate.BDD",
    "gate-e2e":       f"{_Q}QualityGate.E2E",
    "gate-fuzz":      f"{_Q}QualityGate.Fuzz",
    "gate-review":    f"{_Q}QualityGate.CodeReview",
    "gate-pentest":   f"{_Q}QualityGate.PenTest",
    "gate-iast":      f"{_Q}QualityGate.IAST",
    "gate-diagram":   f"{_Q}QualityGate.DiagramQuality",
}
