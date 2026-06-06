# [CUI // SP-CTI]
"""ICDEV™ Pipeline Design Canvas — Remediation Engine.

Maps compliance rule check names to human-readable remediation steps,
effort estimates, and auto-fixability flags.  Generates phased remediation
plans from compliance findings.

No Flask dependency — takes findings list and returns remediation artifacts.
No LLM dependency — all logic is deterministic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta


# ── Severity → phase mapping ────────────────────────────────────────────────

_SEVERITY_PHASES = {
    "CAT1": {"phase": 1, "label": "Critical (0-48h)", "max_hours": 48},
    "CAT2": {"phase": 2, "label": "High (1-2 weeks)", "max_hours": 336},
    "CAT3": {"phase": 3, "label": "Moderate (30 days)", "max_hours": 720},
}

# ── Check → remediation step mapping ────────────────────────────────────────
# Keys MUST match the "check" field of each rule in PIPELINE_COMPLIANCE_RULES
# (tools/pipeline/constants.py).  All 27 rules are covered.

_REMEDIATION_STEPS = {
    # Source Integrity
    "branch_protection": {
        "step": "Enable branch protection on main/release branches: require pull request reviews (>=1 reviewer), status checks passing, no force pushes. Configure in GitLab (Protected Branches) or GitHub (Branch Protection Rules).",
        "effort_hours": 2,
        "auto_fixable": True,
    },
    "code_review_required": {
        "step": "Enforce mandatory code review before merge. Configure merge request approval rules requiring at least 1 reviewer from CODEOWNERS. Enable reviewer assignment automation.",
        "effort_hours": 2,
        "auto_fixable": True,
    },
    # Build Integrity
    "hermetic_build": {
        "step": "Configure hermetic (network-isolated) builds using Tekton or Bazel sandboxed execution. Block internet access during build phase. Pre-cache dependencies in local registry.",
        "effort_hours": 16,
        "auto_fixable": False,
    },
    # Supply Chain
    "sbom_generated": {
        "step": "Add SBOM generation stage using Syft or CycloneDX CLI after build. Output CycloneDX JSON format. Store alongside container image in OCI registry. Validate with sbom-scorecard.",
        "effort_hours": 4,
        "auto_fixable": True,
    },
    "provenance_attestation": {
        "step": "Generate SLSA provenance attestation using slsa-github-generator or Tekton Chains. Sign with Cosign keyless (Fulcio/Rekor) or KMS-backed key. Verify with slsa-verifier before deploy.",
        "effort_hours": 8,
        "auto_fixable": False,
    },
    "image_signing": {
        "step": "Sign container images with Cosign (keyless via Fulcio/Rekor, or KMS-backed). Add signing step after container build and scan. Verify signatures at deploy time with Kyverno ClusterPolicy or Gatekeeper.",
        "effort_hours": 8,
        "auto_fixable": False,
    },
    # Security Testing
    "sast_present": {
        "step": "Add SAST scanning stage: Semgrep (multi-language, free), SonarQube (enterprise), or CodeQL (GitHub). Run on every commit/MR. Block merge on high/critical findings. Configure language-specific rulesets.",
        "effort_hours": 4,
        "auto_fixable": True,
    },
    "sca_present": {
        "step": "Add SCA/dependency scanning: Trivy (free, multi-ecosystem), Grype, or Snyk. Scan lock files and container images. Set threshold: 0 critical, 0 high with known exploits. Generate CycloneDX VEX for exceptions.",
        "effort_hours": 4,
        "auto_fixable": True,
    },
    "container_scan_before_push": {
        "step": "Add container image scanning before registry push: Trivy, Anchore, or Prisma Cloud. Scan for OS and language-level CVEs. Block images with critical/high vulnerabilities. Re-scan on base image updates.",
        "effort_hours": 4,
        "auto_fixable": True,
    },
    "secret_detection_present": {
        "step": "Add secret detection in pre-commit (Gitleaks, detect-secrets) AND CI pipeline (TruffleHog). Scan entire git history on first run. Block commits with detected secrets. Configure allowlist for false positives.",
        "effort_hours": 2,
        "auto_fixable": True,
    },
    "iac_scan_present": {
        "step": "Add IaC scanning: Checkov (Terraform/CloudFormation/K8s), tfsec, or KICS. Scan all .tf, .yaml, .json infrastructure files. Block on CAT1 findings (public access, encryption disabled). Output SARIF for IDE integration.",
        "effort_hours": 4,
        "auto_fixable": True,
    },
    "dast_present": {
        "step": "Add DAST scanning for web applications: OWASP ZAP (free) or Nuclei. Run against staging environment after deployment. Scan for OWASP Top 10. Generate findings report with remediation guidance.",
        "effort_hours": 8,
        "auto_fixable": False,
    },
    # Policy & Governance
    "vuln_threshold_gate": {
        "step": "Add vulnerability threshold gate before deployment: 0 critical, 0 high with known exploits, <=5 medium. Use Trivy exit-code or policy engine. Integrate with merge request approval flow.",
        "effort_hours": 4,
        "auto_fixable": True,
    },
    "admission_controller": {
        "step": "Deploy Kubernetes admission controller: Kyverno (recommended, no Rego) or OPA Gatekeeper. Enforce policies: signed images only, no privileged containers, resource limits required, no latest tag.",
        "effort_hours": 12,
        "auto_fixable": False,
    },
    # Deployment Controls
    "prod_approval_gate": {
        "step": "Add manual approval gate before production deployment. Require at least 2 approvers (developer + lead/ISSO). Configure timeout (24h max). Enable audit logging of approval decisions.",
        "effort_hours": 4,
        "auto_fixable": True,
    },
    "progressive_delivery": {
        "step": "Implement progressive delivery: canary deployment (5%->25%->100%) with automated rollback on error rate > 1% or latency P99 > threshold. Use Argo Rollouts, Flagger, or Istio traffic splitting.",
        "effort_hours": 16,
        "auto_fixable": False,
    },
    "cds_for_cross_domain": {
        "step": "Deploy Cross-Domain Solution (CDS) or data guard for cross-classification pipelines. Configure content inspection, format validation, and audit logging. Ensure NIAP-evaluated CDS for IL5/IL6.",
        "effort_hours": 40,
        "auto_fixable": False,
    },
    # Monitoring & Compliance
    "runtime_monitoring": {
        "step": "Deploy runtime security monitoring: Falco (free, eBPF-based) or NeuVector. Detect anomalous system calls, file access, network connections. Alert to SIEM/PagerDuty. Enable response playbooks.",
        "effort_hours": 12,
        "auto_fixable": False,
    },
    "evidence_collection": {
        "step": "Configure continuous compliance evidence collection: OSCAL Assessment Results (AR) format, SBOM per build, scan results (SARIF), approval records. Archive in compliance evidence locker with retention >=3 years.",
        "effort_hours": 16,
        "auto_fixable": False,
    },
    "audit_logging": {
        "step": "Enable pipeline audit logging: capture all CI/CD events (trigger, build, test, deploy, approval) with user identity, timestamp, and outcome. Forward to SIEM. Retain for >=1 year per NIST AU-11.",
        "effort_hours": 4,
        "auto_fixable": True,
    },
    # Air-Gapped / Cross-Domain
    "airgap_vuln_mirror": {
        "step": "Configure offline vulnerability database mirror for air-gapped environments. Use Trivy --offline-scan with periodically updated DB (USB transfer or data diode). Schedule weekly DB refresh.",
        "effort_hours": 8,
        "auto_fixable": False,
    },
    "airgap_package_mirror": {
        "step": "Deploy internal package mirror (Nexus, Artifactory, Verdaccio) for air-gapped pipelines. Mirror all approved packages from upstream registries. Implement allowlist for approved packages only.",
        "effort_hours": 16,
        "auto_fixable": False,
    },
    # SRE
    "slo_defined": {
        "step": "Define Service Level Objectives (SLOs) for all production services: availability (e.g., 99.9%), latency P99 (e.g., < 200ms), error rate (e.g., < 0.1%). Implement burn-rate alerting with Sloth or OpenSLO.",
        "effort_hours": 8,
        "auto_fixable": False,
    },
    "incident_mgmt_present": {
        "step": "Integrate incident management: PagerDuty, Opsgenie, or Grafana OnCall. Define escalation policies, on-call schedules, and communication channels. Link CI/CD failures to incident creation.",
        "effort_hours": 12,
        "auto_fixable": False,
    },
    "runbooks_present": {
        "step": "Create automated runbooks for common pipeline failures: build timeout, dependency conflict, deploy rollback, secret rotation, registry unavailable. Link to alerting rules for auto-trigger.",
        "effort_hours": 16,
        "auto_fixable": False,
    },
    "chaos_present": {
        "step": "Implement chaos engineering: LitmusChaos (K8s-native) or AWS FIS. Start with pod-kill and network-delay experiments. Define blast radius limits. Run weekly in staging, monthly in production.",
        "effort_hours": 24,
        "auto_fixable": False,
    },
    "dora_tracked": {
        "step": "Track DORA metrics: Deployment Frequency, Lead Time for Changes, Change Failure Rate, Mean Time to Recovery. Use Backstage DORA plugin, Sleuth, or custom Prometheus metrics. Set targets per team maturity.",
        "effort_hours": 12,
        "auto_fixable": False,
    },
}

# Default step for checks not yet mapped
_DEFAULT_STEP = {
    "step": "Review finding and implement appropriate remediation per compliance framework guidance.",
    "effort_hours": 8,
    "auto_fixable": False,
}


# ── Plan Generation ─────────────────────────────────────────────────────────


def generate_remediation_plan(findings: list, rules: list | None = None) -> dict:
    """Generate a phased remediation plan from compliance findings.

    Phase 1: CAT1 critical findings — 0-48 hours
    Phase 2: CAT2 high findings — 1-2 weeks
    Phase 3: CAT3 moderate findings — 30 days

    Args:
        findings: List of finding dicts from ``_run_compliance_check`` output.
                  Each must have ``rule_id``, ``title``, ``severity``, and
                  ``category``.  Optionally ``check`` (the check key name).
        rules:    Optional full rule list (PIPELINE_COMPLIANCE_RULES) used to
                  resolve check names from rule_id when ``check`` is absent.

    Returns:
        Dict with phases, actions, summary, and timestamps.
    """
    rules = rules or []
    now = datetime.now(timezone.utc)

    # Build rule_id -> check lookup from the full rule list
    _rule_id_to_check: dict[str, str] = {}
    for r in rules:
        _rule_id_to_check[r["id"]] = r["check"]

    phases: dict[int, dict] = {
        1: {"label": "Critical (0-48h)", "deadline": (now + timedelta(hours=48)).isoformat(), "actions": []},
        2: {"label": "High (1-2 weeks)", "deadline": (now + timedelta(weeks=2)).isoformat(), "actions": []},
        3: {"label": "Moderate (30 days)", "deadline": (now + timedelta(days=30)).isoformat(), "actions": []},
    }

    for finding in findings:
        severity = finding.get("severity", "CAT3")
        phase_info = _SEVERITY_PHASES.get(severity, _SEVERITY_PHASES["CAT3"])
        phase_num = phase_info["phase"]

        # Resolve check key — prefer explicit, fall back to rule lookup
        check_name = finding.get("check", "")
        if not check_name:
            check_name = _rule_id_to_check.get(finding.get("rule_id", ""), "")

        step_info = _REMEDIATION_STEPS.get(check_name, _DEFAULT_STEP)

        action = {
            "id": str(uuid.uuid4())[:8],
            "finding_id": finding.get("rule_id", finding.get("id", "")),
            "title": finding.get("title", ""),
            "severity": severity,
            "category": finding.get("category", ""),
            "frameworks": finding.get("frameworks", []),
            "remediation_step": step_info["step"],
            "effort_hours": step_info["effort_hours"],
            "auto_fixable": step_info["auto_fixable"],
            "status": "open",
        }
        phases[phase_num]["actions"].append(action)

    # Build phase list (only include non-empty phases)
    phase_list = []
    for phase_num in (1, 2, 3):
        p = phases[phase_num]
        if p["actions"]:
            priority = {1: "CRITICAL", 2: "HIGH", 3: "MEDIUM"}[phase_num]
            phase_list.append(
                {
                    "phase": phase_num,
                    "name": f"Phase {phase_num}: {p['label']}",
                    "priority": priority,
                    "description": {
                        1: "CAT1 findings that deny or disrupt capability. Must resolve before ATO.",
                        2: "CAT2 findings that degrade capability. Resolve for full compliance.",
                        3: "CAT3 findings — minor. Track in POA&M for next review cycle.",
                    }[phase_num],
                    "deadline": p["deadline"],
                    "actions": p["actions"],
                    "total_effort_hours": sum(a["effort_hours"] for a in p["actions"]),
                    "auto_fixable_count": sum(1 for a in p["actions"] if a["auto_fixable"]),
                }
            )

    total_actions = sum(len(p["actions"]) for p in phase_list)
    auto_fixable = sum(p["auto_fixable_count"] for p in phase_list)

    summary_parts = []
    for p in phase_list:
        n = len(p["actions"])
        summary_parts.append(f"{n} {p['priority'].lower()} action{'s' if n != 1 else ''}")
    summary_text = ". ".join(summary_parts) + "." if summary_parts else "No actions needed."
    if auto_fixable:
        summary_text += f" {auto_fixable} of {total_actions} can be auto-fixed."

    return {
        "phases": phase_list,
        "total_actions": total_actions,
        "auto_fixable": auto_fixable,
        "estimated_effort": _estimate_effort([a for p in phase_list for a in p["actions"]]),
        "summary": summary_text,
        "created_at": now.isoformat(),
    }


# ── Effort Estimation ───────────────────────────────────────────────────────


def _estimate_effort(steps: list) -> str:
    """Sum effort hours from remediation steps and format as human-readable string."""
    total_hours = sum(s.get("effort_hours", 0) for s in steps)

    if total_hours == 0:
        return "0 hours"
    elif total_hours < 8:
        return f"{total_hours} hours"
    elif total_hours < 80:
        days = total_hours / 8
        if days == int(days):
            return f"{int(days)} days"
        return f"{days:.1f} days"
    else:
        weeks = total_hours / 40
        if weeks == int(weeks):
            return f"{int(weeks)} weeks"
        return f"{weeks:.1f} weeks"
