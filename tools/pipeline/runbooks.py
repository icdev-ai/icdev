# [CUI // SP-CTI]
"""ICDEV™ Pipeline Design Canvas — Incident Response Runbooks.

Pre-built IR playbooks for pipeline-specific incident types, aligned to
NIST 800-53 SI/SA/CM/IR/SR/RA control families.  Each runbook follows the
standard five-phase IR lifecycle: Detect → Contain → Eradicate → Recover →
Lessons Learned.
"""

from __future__ import annotations

INCIDENT_RUNBOOKS: list[dict] = [
    # ── 1. Build Pipeline Failure / Compromise ───────────────────────────
    {
        "id": "IR-PDC-BUILD-001",
        "title": "Build Pipeline Failure / Compromise",
        "severity": "CAT1",
        "nist_controls": ["SI-7", "SA-11", "CM-3"],
        "description": (
            "Response playbook for compromised or failed build pipelines "
            "including unexpected artifacts, hash mismatches, build time "
            "anomalies, and unauthorized build triggers."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Identify unexpected build artifacts or output file changes",
                    "Detect hash mismatch between source and compiled output",
                    "Flag build time anomaly (significantly faster/slower than baseline)",
                    "Alert on unauthorized or unscheduled build trigger",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Halt all deployments from affected pipeline immediately",
                    "Quarantine suspect build artifacts in isolated storage",
                    "Lock CI/CD service-account credentials and tokens",
                ],
                "max_duration": "2h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Audit full build logs for injected steps or modified commands",
                    "Verify source integrity against signed commits",
                    "Rebuild from a known-clean commit on a verified runner",
                ],
                "max_duration": "8h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Re-run pipeline from verified source with enhanced logging",
                    "Deploy only validated artifacts that pass hash verification",
                    "Restore normal deployment cadence after sign-off",
                ],
                "max_duration": "24h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Add build provenance attestation (SLSA Level 3+)",
                    "Enforce hermetic builds with no network access",
                    "Update detection rules for build anomaly patterns",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "3-5 days",
        "required_roles": [
            "Incident Commander",
            "DevSecOps Engineer",
            "Build/Release Engineer",
            "ISSO",
        ],
        "escalation_threshold": (
            "Escalate to CISO if artifact tampering confirmed or if compromised builds were deployed to production"
        ),
    },
    # ── 2. Secret Leak in Pipeline ───────────────────────────────────────
    {
        "id": "IR-PDC-SECRET-001",
        "title": "Secret Leak in Pipeline",
        "severity": "CAT1",
        "nist_controls": ["IA-5", "SC-28", "IR-4"],
        "description": (
            "Response playbook for secrets exposed in CI/CD pipelines "
            "including credentials in logs, leaked env variables, and "
            "tokens committed to source control."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Secret scanner alert triggers on pipeline output or logs",
                    "Detect credential patterns in build log artifacts",
                    "Identify exposed environment variables in public outputs",
                ],
                "max_duration": "30m",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Rotate ALL exposed secrets immediately (API keys, tokens, passwords)",
                    "Revoke all associated OAuth grants and service tokens",
                    "Restrict access to affected build logs",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Remove secrets from git history (BFG Repo-Cleaner or filter-branch)",
                    "Scan all branches and tags for additional secret exposure",
                    "Audit CI/CD variable configuration for insecure storage",
                ],
                "max_duration": "4h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Deploy with fully rotated secrets and verified configuration",
                    "Verify no lingering exposure in logs, artifacts, or caches",
                    "Confirm downstream services authenticate with new credentials",
                ],
                "max_duration": "8h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Enforce pre-commit secret scanning hooks (detect-secrets, gitleaks)",
                    "Migrate from env-var injection to vault-based secret delivery",
                    "Add log redaction rules for common credential patterns",
                ],
                "max_duration": "3d",
                "automated": False,
            },
        ],
        "estimated_duration": "2-4 days",
        "required_roles": [
            "Incident Commander",
            "DevSecOps Engineer",
            "Identity & Access Engineer",
            "SOC Analyst",
        ],
        "escalation_threshold": (
            "Immediate CISO notification if production credentials exposed; "
            "escalate to legal if customer data access keys compromised"
        ),
    },
    # ── 3. Supply Chain Attack on Dependencies ───────────────────────────
    {
        "id": "IR-PDC-SUPPLY-001",
        "title": "Supply Chain Attack on Dependencies",
        "severity": "CAT1",
        "nist_controls": ["SR-2", "SR-3", "SA-12", "SI-7"],
        "description": (
            "Response playbook for supply chain attacks targeting project "
            "dependencies including dependency confusion, typosquatting, "
            "compromised packages, and SBOM drift."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Dependency confusion alert from private/public registry mismatch",
                    "Typosquatting detection on newly added packages",
                    "SBOM drift — unexpected dependency version or hash change",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Pin all dependencies to last known-good versions immediately",
                    "Block suspicious packages at registry proxy / firewall",
                    "Halt all builds consuming affected dependency tree",
                ],
                "max_duration": "2h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Audit full dependency tree (transitive included) for compromise indicators",
                    "Verify checksums of all packages against trusted source",
                    "Update lockfiles with verified, pinned versions",
                ],
                "max_duration": "12h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Rebuild all artifacts with verified dependency set",
                    "Regenerate SBOM and distribute to downstream consumers",
                    "Notify dependent teams and service owners of remediation",
                ],
                "max_duration": "24h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Implement package allowlist registry (Artifactory/Nexus)",
                    "Add SLSA verification for all third-party dependencies",
                    "Enable automated SBOM diffing on every dependency update",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "3-5 days",
        "required_roles": [
            "Incident Commander",
            "DevSecOps Engineer",
            "Software Supply Chain Analyst",
            "Build/Release Engineer",
            "ISSO",
        ],
        "escalation_threshold": (
            "Escalate to CISO if compromised dependency was deployed to "
            "production or if attack affects FedRAMP-authorized systems"
        ),
    },
    # ── 4. Failed / Rogue Deployment ─────────────────────────────────────
    {
        "id": "IR-PDC-DEPLOY-001",
        "title": "Failed / Rogue Deployment",
        "severity": "CAT1",
        "nist_controls": ["CM-3", "CM-5", "SI-7"],
        "description": (
            "Response playbook for unauthorized or failed deployments "
            "including deployments without approval, canary failures, "
            "and health check degradation post-deploy."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Alert on deployment without required approval chain",
                    "Canary failure or error-rate spike exceeding threshold",
                    "Health check degradation across service endpoints",
                ],
                "max_duration": "30m",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Trigger immediate rollback to last known-good version",
                    "Halt all pipeline activity and enable incident mode",
                    "Preserve deployment logs and artifact metadata",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Identify deployment source and verify operator identity",
                    "Verify artifact integrity (hash, signature, provenance)",
                    "Audit approval chain for missing or forged approvals",
                ],
                "max_duration": "4h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Redeploy known-good version with verified artifact",
                    "Verify health checks and SLO metrics return to baseline",
                    "Resume pipeline with enhanced deployment gates",
                ],
                "max_duration": "8h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Enforce multi-person approval for production deployments",
                    "Add post-deployment verification (smoke tests, synthetic monitors)",
                    "Automate canary analysis with rollback triggers",
                ],
                "max_duration": "3d",
                "automated": False,
            },
        ],
        "estimated_duration": "2-3 days",
        "required_roles": [
            "Incident Commander",
            "DevSecOps Engineer",
            "Site Reliability Engineer",
            "Release Manager",
        ],
        "escalation_threshold": (
            "Escalate to CISO if unauthorized deployment reached production "
            "or if rogue artifact served customer traffic"
        ),
    },
    # ── 5. Vulnerable Container in Production ────────────────────────────
    {
        "id": "IR-PDC-CONTAINER-001",
        "title": "Vulnerable Container in Production",
        "severity": "CAT2",
        "nist_controls": ["RA-5", "SI-2", "CM-6"],
        "description": (
            "Response playbook for containers with known vulnerabilities "
            "running in production, including CVE alerts, scan findings, "
            "and runtime vulnerability detection."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Container image scan finding (Trivy, Grype, or Prisma)",
                    "CVE alert matching running container image digest",
                    "Runtime vulnerability detection via eBPF/Falco agent",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Apply NetworkPolicy restrictions to affected pods",
                    "Scale down affected replicas if exploitability is critical",
                    "Enable enhanced audit logging on container runtime",
                ],
                "max_duration": "2h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Patch base image to resolve identified CVEs",
                    "Rebuild container image from patched base",
                    "Re-scan rebuilt image and verify zero critical/high findings",
                ],
                "max_duration": "8h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Deploy patched container through standard pipeline",
                    "Verify vulnerability resolved via post-deploy scan",
                    "Update SBOM with patched component versions",
                ],
                "max_duration": "4h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Automate base image updates with Renovate/Dependabot",
                    "Add admission control (Kyverno/OPA) to block vulnerable images",
                    "Define SLA for CVE patching by severity (critical < 48h)",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "2-4 days",
        "required_roles": [
            "Incident Commander",
            "DevSecOps Engineer",
            "Platform/Kubernetes Engineer",
            "SOC Analyst",
        ],
        "escalation_threshold": (
            "Escalate to CISO if critical CVE with known exploit is running "
            "in production or if container serves FedRAMP boundary traffic"
        ),
    },
    # ── 6. Container Registry Compromise ─────────────────────────────────
    {
        "id": "IR-PDC-REGISTRY-001",
        "title": "Container Registry Compromise",
        "severity": "CAT1",
        "nist_controls": ["SR-3", "SI-7", "SC-28"],
        "description": (
            "Response playbook for compromised container registries "
            "including unsigned image pushes, unexpected tag mutations, "
            "and anomalous registry access patterns."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Alert on unsigned image pushed to production repository",
                    "Detect unexpected tag mutation (mutable tag overwrite)",
                    "Identify anomalous registry access (unusual IP, time, volume)",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Set registry to read-only mode immediately",
                    "Block all image pulls of suspect tags/digests",
                    "Preserve registry audit logs for forensic analysis",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Audit all images pushed during compromise window",
                    "Verify signatures and provenance of all production images",
                    "Remove compromised or unsigned artifacts from registry",
                ],
                "max_duration": "12h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Re-push verified images with fresh signatures",
                    "Restore signing keys from secure backup (HSM/KMS)",
                    "Re-enable registry with write access after validation",
                ],
                "max_duration": "8h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Enforce image signing (Cosign/Notation) as admission requirement",
                    "Enable immutable tags on all production repositories",
                    "Add audit alerting on all push events to production repos",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "3-5 days",
        "required_roles": [
            "Incident Commander",
            "DevSecOps Engineer",
            "Platform/Registry Administrator",
            "Forensic Analyst",
            "ISSO",
        ],
        "escalation_threshold": (
            "Immediate CISO notification; escalate to law enforcement "
            "if supply chain attack confirmed affecting multiple consumers"
        ),
    },
    # ── 7. Configuration Drift in Production ─────────────────────────────
    {
        "id": "IR-PDC-DRIFT-001",
        "title": "Configuration Drift in Production",
        "severity": "CAT2",
        "nist_controls": ["CM-2", "CM-3", "CM-6"],
        "description": (
            "Response playbook for configuration drift where production "
            "state diverges from declared IaC/GitOps source of truth, "
            "including CSPM findings and manual changes."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "IaC drift scan detects delta between declared and actual state",
                    "GitOps controller reports desynchronization alert",
                    "CSPM finding identifies non-compliant configuration",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Document current actual state before any remediation",
                    "Freeze manual access to affected infrastructure",
                    "Enable enhanced change-detection monitoring",
                ],
                "max_duration": "2h",
                "automated": False,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Reconcile GitOps desired state with actual infrastructure",
                    "Apply IaC plan to converge drifted resources",
                    "Verify full convergence with post-apply drift scan",
                ],
                "max_duration": "4h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Re-baseline configuration in IaC source of truth",
                    "Verify compliance posture after reconciliation",
                    "Enable continuous drift alerting with auto-remediation",
                ],
                "max_duration": "4h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Enforce GitOps-only changes (remove manual kubectl/console access)",
                    "Add drift detection automation to CI/CD pipeline",
                    "Implement policy-as-code (OPA/Kyverno) for configuration guardrails",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "1-3 days",
        "required_roles": [
            "Incident Commander",
            "Platform/Infrastructure Engineer",
            "DevSecOps Engineer",
            "ISSO",
        ],
        "escalation_threshold": (
            "Escalate to CISO if drift introduced security misconfiguration "
            "or if affected resources are within FedRAMP/CMMC boundary"
        ),
    },
    # ── 8. CI/CD Platform Compromise ─────────────────────────────────────
    {
        "id": "IR-PDC-CICD-001",
        "title": "CI/CD Platform Compromise",
        "severity": "CAT1",
        "nist_controls": ["SI-7", "AC-2", "AU-6", "IR-4"],
        "description": (
            "Response playbook for compromise of the CI/CD platform itself "
            "including unauthorized pipeline modifications, admin access "
            "anomalies, and webhook tampering."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Alert on unauthorized pipeline definition modification",
                    "Detect admin-level access anomaly (unusual IP, time, action)",
                    "Identify webhook URL tampering or unauthorized webhook creation",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Disable all CI/CD webhooks to stop triggered executions",
                    "Lock all admin and service accounts on CI/CD platform",
                    "Preserve full audit logs and pipeline execution history",
                ],
                "max_duration": "2h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Rotate all CI/CD platform tokens, API keys, and runner tokens",
                    "Audit all pipeline definitions for unauthorized modifications",
                    "Verify all webhook URLs against authorized endpoint list",
                ],
                "max_duration": "8h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Re-enable CI/CD with hardened access controls and MFA",
                    "Enforce MFA on all CI/CD admin accounts",
                    "Deploy pipelines from verified pipeline-as-code definitions",
                ],
                "max_duration": "12h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Implement CI/CD admin MFA as mandatory policy",
                    "Require code review for all pipeline definition changes",
                    "Add webhook signature validation on all inbound triggers",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "3-5 days",
        "required_roles": [
            "Incident Commander",
            "DevSecOps Engineer",
            "CI/CD Platform Administrator",
            "Forensic Analyst",
            "CISO",
        ],
        "escalation_threshold": (
            "Immediate CISO and legal notification; escalate to law enforcement if platform-level compromise confirmed"
        ),
    },
]

# ── Lookup index (built once at import time) ──────────────────────────────────
_RUNBOOK_INDEX: dict[str, dict] = {rb["id"]: rb for rb in INCIDENT_RUNBOOKS}


def get_all_runbooks() -> list[dict]:
    """Return every pipeline incident-response runbook."""
    return INCIDENT_RUNBOOKS


def get_runbook_by_id(runbook_id: str) -> dict | None:
    """Return a single runbook by its ID, or None if not found."""
    return _RUNBOOK_INDEX.get(runbook_id)


def get_applicable_runbooks(findings: list[dict]) -> list[dict]:
    """Return runbooks relevant to a set of pipeline assessment findings.

    Matching rules:
    - build/compile findings         → Build Pipeline Failure
    - secret/credential findings     → Secret Leak in Pipeline
    - dependency/supply chain        → Supply Chain Attack
    - deployment findings            → Failed / Rogue Deployment
    - container/image findings       → Vulnerable Container
    - registry findings              → Container Registry Compromise
    - configuration/drift findings   → Configuration Drift
    - Any CAT1 finding               → also include CI/CD Platform Compromise
    """
    matched_ids: set[str] = set()

    _build_keywords = {
        "build",
        "compile",
        "artifact",
        "hash",
        "provenance",
        "hermetic",
        "runner",
        "build-time",
    }
    _secret_keywords = {
        "secret",
        "credential",
        "password",
        "token",
        "api-key",
        "apikey",
        "leak",
        "exposed",
    }
    _supply_keywords = {
        "dependency",
        "supply-chain",
        "supplychain",
        "sbom",
        "typosquat",
        "package",
        "npm",
        "pypi",
        "maven",
        "crate",
    }
    _deploy_keywords = {
        "deploy",
        "deployment",
        "rollback",
        "canary",
        "release",
        "approval",
        "promotion",
    }
    _container_keywords = {
        "container",
        "image",
        "cve",
        "vulnerability",
        "trivy",
        "grype",
        "scan",
        "base-image",
    }
    _registry_keywords = {
        "registry",
        "cosign",
        "signature",
        "unsigned",
        "tag",
        "digest",
        "push",
        "pull",
    }
    _drift_keywords = {
        "drift",
        "configuration",
        "gitops",
        "iac",
        "terraform",
        "reconcile",
        "cspm",
        "compliance",
    }

    for finding in findings:
        text = " ".join(
            [
                str(finding.get("category", "")),
                str(finding.get("title", "")),
                str(finding.get("description", "")),
            ]
        ).lower()

        if any(kw in text for kw in _build_keywords):
            matched_ids.add("IR-PDC-BUILD-001")

        if any(kw in text for kw in _secret_keywords):
            matched_ids.add("IR-PDC-SECRET-001")

        if any(kw in text for kw in _supply_keywords):
            matched_ids.add("IR-PDC-SUPPLY-001")

        if any(kw in text for kw in _deploy_keywords):
            matched_ids.add("IR-PDC-DEPLOY-001")

        if any(kw in text for kw in _container_keywords):
            matched_ids.add("IR-PDC-CONTAINER-001")

        if any(kw in text for kw in _registry_keywords):
            matched_ids.add("IR-PDC-REGISTRY-001")

        if any(kw in text for kw in _drift_keywords):
            matched_ids.add("IR-PDC-DRIFT-001")

        severity = str(finding.get("severity", "")).upper()
        if severity == "CAT1":
            matched_ids.add("IR-PDC-CICD-001")

    return [rb for rb in INCIDENT_RUNBOOKS if rb["id"] in matched_ids]
