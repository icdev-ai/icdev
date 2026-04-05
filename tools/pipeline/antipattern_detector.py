#!/usr/bin/env python3
# CUI // SP-CTI — Pipeline Design Anti-Pattern Detector
"""Detects architectural anti-patterns in DevSecOps pipeline designs.

Mirrors tools/network/cloud_architecture.py anti-pattern detection
but for CI/CD pipeline topology.

Each anti-pattern has: id, title, severity, description, affected nodes,
recommendation, and NIST/framework reference.

Usage:
    from tools.pipeline.antipattern_detector import detect_antipatterns
    findings = detect_antipatterns(nodes, edges)
"""


# ── Anti-Pattern Definitions ─────────────────────────────────────────────────
# Severity: critical (blocks deploy), high (strong warning), medium (advisory)

ANTIPATTERNS = {
    "AP-PDC-001": {
        "title": "No security scanning in pipeline",
        "severity": "critical",
        "description": "Pipeline has CI/CD engine and deploy target but zero security scanners (SAST, SCA, DAST, container scan, secret detection). This violates all DevSecOps frameworks.",
        "recommendation": "Add at minimum: SAST (Semgrep/SonarQube), SCA (Trivy/Grype), and secret detection (Gitleaks).",
        "frameworks": ["nist_ssdf", "dod_devsecops", "fedramp", "cmmc"],
    },
    "AP-PDC-002": {
        "title": "Direct deploy to production without approval gate",
        "severity": "critical",
        "description": "Pipeline deploys directly to production K8s/VM without a manual or automated approval gate. This bypasses change control.",
        "recommendation": "Add a manual approval gate or automated condition check (vulnerability threshold, SLO health) before production deployment.",
        "frameworks": ["dod_devsecops", "fedramp"],
    },
    "AP-PDC-003": {
        "title": "No container image signing",
        "severity": "high",
        "description": "Pipeline pushes container images to registry without signing. Unsigned images can be tampered with between build and deploy.",
        "recommendation": "Add Cosign or Notation signing after container build, verify signatures at deploy time with Kyverno/Gatekeeper.",
        "frameworks": ["slsa", "dod_devsecops"],
    },
    "AP-PDC-004": {
        "title": "Secret management missing",
        "severity": "high",
        "description": "Pipeline references secrets (API keys, credentials) but has no secret management tool (Vault, KMS, Sealed Secrets). Secrets may be hardcoded or in env vars.",
        "recommendation": "Add HashiCorp Vault, AWS Secrets Manager, or Azure Key Vault. Use External Secrets Operator for K8s.",
        "frameworks": ["nist_ssdf", "fedramp"],
    },
    "AP-PDC-005": {
        "title": "No SBOM generation",
        "severity": "high",
        "description": "Pipeline builds and deploys software without generating a Software Bill of Materials. SBOM is required by EO 14028 for federal software.",
        "recommendation": "Add Syft or CycloneDX SBOM generation in the build stage.",
        "frameworks": ["nist_ssdf", "dod_devsecops", "fedramp"],
    },
    "AP-PDC-006": {
        "title": "Single registry without replication",
        "severity": "medium",
        "description": "Pipeline uses a single container registry. If the registry goes down, no images can be deployed or rolled back.",
        "recommendation": "Configure Harbor replication to a secondary registry, or use a managed registry with built-in geo-replication.",
        "frameworks": ["dod_devsecops"],
    },
    "AP-PDC-007": {
        "title": "No runtime security monitoring",
        "severity": "high",
        "description": "Pipeline deploys to production but has no runtime security monitoring (Falco, NeuVector, Wazuh). Post-deploy threats are invisible.",
        "recommendation": "Add Falco for K8s runtime threat detection or NeuVector for container security.",
        "frameworks": ["nist_ssdf", "fedramp"],
    },
    "AP-PDC-008": {
        "title": "Air-gapped pipeline connected to cloud services",
        "severity": "critical",
        "description": "Pipeline is marked as air-gapped (SIPR/JWICS/IL6) but includes cloud-managed services (ECR, CodeBuild, etc.) that require internet connectivity.",
        "recommendation": "Replace cloud-managed services with self-hosted alternatives (Harbor instead of ECR, GitLab CI instead of CodePipeline).",
        "frameworks": ["dod_devsecops"],
    },
    "AP-PDC-009": {
        "title": "No SLO defined for production services",
        "severity": "medium",
        "description": "Pipeline deploys to production but has no SLO tracking. Without SLOs, there's no objective measure of service health or error budget.",
        "recommendation": "Add SLO Manager with availability and latency targets. Use burn-rate alerting.",
        "frameworks": ["dod_devsecops"],
    },
    "AP-PDC-010": {
        "title": "No incident management connected",
        "severity": "medium",
        "description": "Pipeline has monitoring but no incident management. Alerts fire but nobody is paged and no incident lifecycle is tracked.",
        "recommendation": "Add PagerDuty, Grafana OnCall, or ICDEV Incident Commander. Wire alerts to incident creation.",
        "frameworks": ["nist_ssdf", "fedramp"],
    },
    "AP-PDC-011": {
        "title": "CI/CD engine without source control",
        "severity": "high",
        "description": "Pipeline has a CI/CD engine but no source control management. Builds may be triggered from unversioned code.",
        "recommendation": "Add GitLab, Gitea, or a cloud SCM. Connect SCM → CI engine with push/MR triggers.",
        "frameworks": ["nist_ssdf", "slsa"],
    },
    "AP-PDC-012": {
        "title": "No IaC scanning for infrastructure code",
        "severity": "medium",
        "description": "Pipeline includes Terraform/Ansible/K8s deployment but no IaC scanner (Checkov, tfsec, KICS). Infrastructure misconfigurations ship to production.",
        "recommendation": "Add Checkov or tfsec in the test stage to scan Terraform/CloudFormation/K8s manifests.",
        "frameworks": ["dod_devsecops", "fedramp"],
    },
    "AP-PDC-013": {
        "title": "Cross-domain without CDS guard",
        "severity": "critical",
        "description": "Pipeline spans classification boundaries (commercial → GovCloud, NIPR → SIPR) without a Cross-Domain Solution guard or data diode.",
        "recommendation": "Add a CDS Guard or data diode between classification boundaries. Required by CNSS Policy 15.",
        "frameworks": ["dod_devsecops"],
    },
    "AP-PDC-014": {
        "title": "No policy admission controller on K8s",
        "severity": "medium",
        "description": "Pipeline deploys to Kubernetes without Kyverno, OPA/Gatekeeper, or Binary Authorization. Unsigned/non-compliant images can be deployed.",
        "recommendation": "Add Kyverno or OPA Gatekeeper to enforce image signing, source verification, and pod security standards.",
        "frameworks": ["dod_devsecops", "slsa"],
    },
    "AP-PDC-015": {
        "title": "Single CI/CD engine (no pipeline resilience)",
        "severity": "medium",
        "description": "Entire pipeline depends on a single CI/CD engine. If GitLab/Jenkins/Tekton goes down, no builds or deploys can run.",
        "recommendation": "For production-critical pipelines, consider a secondary CI engine or ensure the primary has HA (GitLab HA, Jenkins HA).",
        "frameworks": [],
    },
}


# ── Type Sets ────────────────────────────────────────────────────────────────

_CI_ENGINES = {
    "cicd-gitlab",
    "cicd-jenkins",
    "cicd-tekton",
    "cicd-github-actions",
    "cicd-argo-workflows",
    "cicd-drone",
    "aws-codepipeline",
    "aws-codebuild",
    "az-pipelines",
    "gcp-cloudbuild",
    "oci-devops",
    "ibm-cd",
}
_SCM = {
    "scm-gitlab",
    "scm-gitea",
    "scm-forgejo",
    "scm-bitbucket",
    "aws-codecommit",
    "az-repos",
    "gcp-source",
    "oci-code-repos",
}
_SCANNERS = {
    "scan-sast",
    "scan-sonarqube",
    "scan-semgrep",
    "scan-codeql",
    "scan-bandit",
    "scan-spotbugs",
    "scan-dast",
    "scan-zap",
    "scan-nuclei",
    "scan-burp",
    "scan-sca",
    "scan-trivy",
    "scan-grype",
    "scan-snyk",
    "scan-dep-check",
    "scan-container",
    "scan-anchore",
    "scan-neuvector",
    "scan-iac",
    "scan-checkov",
    "scan-tfsec",
    "scan-kics",
    "scan-secret",
    "scan-gitleaks",
    "scan-trufflehog",
    "scan-detect-secrets",
    "aws-codeguru",
    "aws-inspector",
    "az-defender",
    "gcp-artifact-analysis",
}
_IAC_SCANNERS = {"scan-iac", "scan-checkov", "scan-tfsec", "scan-kics"}
_REGISTRIES = {
    "registry-generic",
    "registry-harbor",
    "registry-nexus",
    "registry-jfrog",
    "registry-zot",
    "aws-ecr",
    "az-acr",
    "gcp-gar",
    "oci-cr",
    "ibm-cr",
}
_SIGNERS = {"sign-cosign", "sign-notation", "sign-dct"}
_K8S = {
    "k8s-cluster",
    "aws-eks",
    "az-aks",
    "gcp-gke",
    "oci-oke",
    "ibm-iks",
    "openshift",
    "rke2",
    "k3s",
    "deploy-bigbang",
}
_DEPLOY = _K8S | {"deploy-serverless", "deploy-vm", "deploy-edge"}
_GATES = {"gate-manual", "gate-automated", "gate-vuln-threshold", "gate-deploy-window"}
_SECRETS = {
    "vault-hashicorp",
    "vault-openbao",
    "aws-secrets",
    "aws-kms",
    "az-keyvault",
    "gcp-secret",
    "gcp-kms",
    "oci-vault",
    "ibm-secrets",
    "ibm-hpcs",
    "kms-generic",
    "hsm-generic",
    "sealed-secrets",
    "sops",
    "external-secrets",
}
_SBOM = {"sbom-syft", "sbom-cyclonedx", "sbom-spdx"}
_MONITOR = {
    "mon-prometheus",
    "mon-grafana",
    "mon-loki",
    "mon-tempo",
    "mon-otel",
    "mon-elk",
    "mon-fluentbit",
    "aws-cloudwatch",
    "aws-guardduty",
    "az-monitor",
    "az-sentinel",
    "gcp-monitoring",
    "gcp-scc",
}
_RUNTIME_SEC = {"mon-falco", "mon-wazuh", "scan-neuvector"}
_SLO = {"sre-slo", "sre-sli", "sre-openslo", "sre-sloth", "sre-pyrra", "aws-cw-slo", "gcp-service-mon"}
_INCIDENT = {"sre-incident", "sre-pagerduty", "sre-grafana-oncall", "sre-opsgenie", "aws-incident-mgr"}
_POLICY = {
    "policy-opa",
    "policy-kyverno",
    "policy-gatekeeper",
    "policy-kubewarden",
    "gcp-binary-auth",
    "ibm-portieris",
    "verify-slsa",
}
_CLOUD_MANAGED = {
    "aws-codepipeline",
    "aws-codebuild",
    "aws-ecr",
    "aws-eks",
    "aws-inspector",
    "az-pipelines",
    "az-acr",
    "az-aks",
    "az-defender",
    "gcp-cloudbuild",
    "gcp-gke",
    "gcp-gar",
    "gcp-artifact-analysis",
}
_AIRGAP_MARKERS = {"pipeline-sipr", "pipeline-jwics", "boundary-secret", "boundary-topsecret"}
_CDS = {"cds-guard", "cds-data-diode", "cds-transfer"}
_BOUNDARIES = {"boundary-commercial", "boundary-govcloud", "boundary-secret", "boundary-topsecret"}
_IaC_DEPLOY = {"ndc-topology", "ndc-vpc", "hybrid-directconnect", "hybrid-vpn", "hybrid-transit"}


def detect_antipatterns(nodes, edges):
    """Detect architectural anti-patterns in a pipeline design.

    Args:
        nodes: list of node dicts from graph_json
        edges: list of edge dicts from graph_json

    Returns:
        list of finding dicts with id, title, severity, description,
        recommendation, affected_nodes, frameworks
    """
    node_types = set(n.get("type", "") for n in nodes)
    findings = []

    has_ci = bool(node_types & _CI_ENGINES)
    has_scm = bool(node_types & _SCM)
    has_scanners = bool(node_types & _SCANNERS)
    has_registry = bool(node_types & _REGISTRIES)
    has_signing = bool(node_types & _SIGNERS)
    has_deploy = bool(node_types & _DEPLOY)
    has_k8s = bool(node_types & _K8S)
    has_gates = bool(node_types & _GATES)
    has_secrets = bool(node_types & _SECRETS)
    has_sbom = bool(node_types & _SBOM)
    has_monitor = bool(node_types & _MONITOR)
    has_runtime_sec = bool(node_types & _RUNTIME_SEC)
    has_slo = bool(node_types & _SLO)
    has_incident = bool(node_types & _INCIDENT)
    has_policy = bool(node_types & _POLICY)
    has_iac_scan = bool(node_types & _IAC_SCANNERS)
    has_airgap = bool(node_types & _AIRGAP_MARKERS)
    has_cloud = bool(node_types & _CLOUD_MANAGED)
    has_cds = bool(node_types & _CDS)
    has_iac_deploy = bool(node_types & _IaC_DEPLOY)

    # AP-PDC-001: No security scanning
    if has_ci and has_deploy and not has_scanners:
        findings.append(_finding("AP-PDC-001", [n for n in nodes if n.get("type") in _CI_ENGINES | _DEPLOY]))

    # AP-PDC-002: No approval gate before prod
    if has_deploy and not has_gates:
        findings.append(_finding("AP-PDC-002", [n for n in nodes if n.get("type") in _DEPLOY]))

    # AP-PDC-003: No image signing
    if has_registry and not has_signing:
        findings.append(_finding("AP-PDC-003", [n for n in nodes if n.get("type") in _REGISTRIES]))

    # AP-PDC-004: No secret management
    if has_ci and has_deploy and not has_secrets:
        findings.append(_finding("AP-PDC-004", [n for n in nodes if n.get("type") in _CI_ENGINES]))

    # AP-PDC-005: No SBOM
    if has_ci and has_deploy and not has_sbom:
        findings.append(_finding("AP-PDC-005", [n for n in nodes if n.get("type") in _CI_ENGINES]))

    # AP-PDC-006: Single registry
    registry_count = len(node_types & _REGISTRIES)
    if registry_count == 1 and has_deploy:
        findings.append(_finding("AP-PDC-006", [n for n in nodes if n.get("type") in _REGISTRIES]))

    # AP-PDC-007: No runtime security
    if has_deploy and not has_runtime_sec:
        findings.append(_finding("AP-PDC-007", [n for n in nodes if n.get("type") in _DEPLOY]))

    # AP-PDC-008: Air-gapped with cloud services
    if has_airgap and has_cloud:
        findings.append(_finding("AP-PDC-008", [n for n in nodes if n.get("type") in _AIRGAP_MARKERS | _CLOUD_MANAGED]))

    # AP-PDC-009: No SLO
    if has_deploy and not has_slo:
        findings.append(_finding("AP-PDC-009", [n for n in nodes if n.get("type") in _DEPLOY]))

    # AP-PDC-010: No incident management
    if has_monitor and not has_incident:
        findings.append(_finding("AP-PDC-010", [n for n in nodes if n.get("type") in _MONITOR]))

    # AP-PDC-011: CI without SCM
    if has_ci and not has_scm:
        findings.append(_finding("AP-PDC-011", [n for n in nodes if n.get("type") in _CI_ENGINES]))

    # AP-PDC-012: No IaC scanning
    if has_iac_deploy and not has_iac_scan:
        findings.append(_finding("AP-PDC-012", [n for n in nodes if n.get("type") in _IaC_DEPLOY]))

    # AP-PDC-013: Cross-domain without CDS
    boundary_count = len(node_types & _BOUNDARIES)
    if boundary_count >= 2 and not has_cds:
        findings.append(_finding("AP-PDC-013", [n for n in nodes if n.get("type") in _BOUNDARIES]))

    # AP-PDC-014: K8s without admission controller
    if has_k8s and not has_policy:
        findings.append(_finding("AP-PDC-014", [n for n in nodes if n.get("type") in _K8S]))

    # AP-PDC-015: Single CI engine
    ci_count = len(node_types & _CI_ENGINES)
    if ci_count == 1 and has_deploy:
        findings.append(_finding("AP-PDC-015", [n for n in nodes if n.get("type") in _CI_ENGINES]))

    return findings


def _finding(ap_id, affected_nodes):
    """Create a finding dict from an anti-pattern ID and affected nodes."""
    ap = ANTIPATTERNS[ap_id]
    return {
        "id": ap_id,
        "title": ap["title"],
        "severity": ap["severity"],
        "description": ap["description"],
        "recommendation": ap["recommendation"],
        "frameworks": ap["frameworks"],
        "affected_nodes": [
            {"id": n.get("id", ""), "label": n.get("label", ""), "type": n.get("type", "")} for n in affected_nodes[:5]
        ],
    }
