# [CUI // SP-CTI]
"""ICDEV™ Security Design Canvas — Remediation Planning Engine.

Pure functions for generating remediation plans, POA&M entries, and effort
estimates from security assessment results.

No Flask dependency — takes assessment data and returns remediation artifacts.
No LLM dependency — all logic is deterministic.
"""

import uuid
from datetime import datetime, timezone, timedelta


# ── Remediation Plan Generation ──────────────────────────────────────────────

# Severity → phase mapping
_SEVERITY_PHASES = {
    "CAT1": {"phase": 1, "label": "Critical (0–48h)", "max_hours": 48},
    "CAT2": {"phase": 2, "label": "High (1–2 weeks)", "max_hours": 336},
    "CAT3": {"phase": 3, "label": "Moderate (30 days)", "max_hours": 720},
}

# Check → remediation step mapping (human-readable instructions)
_REMEDIATION_STEPS = {
    # Authentication
    "all_flows_authenticated": {
        "step": "Enable authentication (mTLS, OAuth2, or API key) on all data flows between services.",
        "effort_hours": 8,
        "auto_fixable": False,
    },
    "idp_for_user_assets": {
        "step": "Deploy an Identity Provider (IdP) with MFA for all user-facing applications. Configure SAML/OIDC federation.",
        "effort_hours": 16,
        "auto_fixable": False,
    },
    "pam_for_privileged": {
        "step": "Implement Privileged Access Management (PAM) solution for all administrative access. Enforce session recording and just-in-time access.",
        "effort_hours": 24,
        "auto_fixable": False,
    },
    # Encryption
    "boundary_flows_encrypted": {
        "step": "Enable TLS 1.2+ encryption on all data flows crossing trust boundaries. Use mTLS for service-to-service communication.",
        "effort_hours": 4,
        "auto_fixable": True,
    },
    "kms_present": {
        "step": "Deploy KMS/HSM for centralized key management. Rotate keys on schedule per NIST SC-12.",
        "effort_hours": 16,
        "auto_fixable": False,
    },
    "db_encryption_at_rest": {
        "step": "Enable encryption at rest for all databases using KMS-managed keys. Verify with FIPS 140-2/3 validated modules.",
        "effort_hours": 8,
        "auto_fixable": True,
    },
    # Segmentation
    "boundaries_defined": {
        "step": "Define trust boundaries between network zones. Create at least DMZ, application, and data tiers.",
        "effort_hours": 4,
        "auto_fixable": True,
    },
    "firewall_at_boundary": {
        "step": "Deploy firewall/WAF between internet-facing boundary and internal assets. Configure deny-by-default rules.",
        "effort_hours": 8,
        "auto_fixable": False,
    },
    "no_direct_inet_db": {
        "step": "Remove direct internet-to-database connections. Route through application tier with WAF and authentication.",
        "effort_hours": 4,
        "auto_fixable": True,
    },
    # Logging & Monitoring
    "siem_present": {
        "step": "Deploy SIEM solution (Splunk, Elastic, or equivalent). Configure log ingestion from all assets per NIST AU-2.",
        "effort_hours": 24,
        "auto_fixable": False,
    },
    "all_assets_logged": {
        "step": "Configure all assets to forward logs to SIEM. Ensure audit events per NIST AU-2 baseline are captured.",
        "effort_hours": 4,
        "auto_fixable": True,
    },
    "db_audit_logging": {
        "step": "Enable database audit logging and forward to SIEM. Capture all DDL, DML on sensitive tables, and authentication events.",
        "effort_hours": 4,
        "auto_fixable": True,
    },
    # Monitoring
    "ids_present": {
        "step": "Deploy IDS/IPS for network traffic inspection. Place at trust boundary crossings and east-west segments.",
        "effort_hours": 16,
        "auto_fixable": False,
    },
    "scanner_present": {
        "step": "Deploy vulnerability scanner (Nessus, Qualys, or equivalent). Schedule weekly scans with automated reporting.",
        "effort_hours": 8,
        "auto_fixable": False,
    },
    # Access Control
    "s2s_auth": {
        "step": "Implement service-to-service authentication using mTLS certificates or short-lived tokens (SPIFFE/SPIRE).",
        "effort_hours": 12,
        "auto_fixable": False,
    },
    # Data Protection
    "data_classification": {
        "step": "Apply data classification labels (CUI, PII, PHI) to all storage assets. Update asset configuration metadata.",
        "effort_hours": 2,
        "auto_fixable": True,
    },
    "dlp_present": {
        "step": "Deploy DLP solution for data egress monitoring. Configure policies for CUI and PII pattern detection.",
        "effort_hours": 16,
        "auto_fixable": False,
    },
    # Incident Response
    "siem_alerting": {
        "step": "Configure SIEM alerting rules and integrate with incident response ticketing system.",
        "effort_hours": 8,
        "auto_fixable": False,
    },
    # Supply Chain
    "registry_admission": {
        "step": "Deploy admission controller (Kyverno, OPA Gatekeeper) for container registry. Block unsigned or non-scanned images.",
        "effort_hours": 8,
        "auto_fixable": False,
    },
    # Documentation
    "assets_labeled": {
        "step": "Update all asset nodes with descriptive labels matching operational naming conventions.",
        "effort_hours": 1,
        "auto_fixable": True,
    },
    "has_boundaries": {
        "step": "Define trust boundaries in the design to document network segmentation.",
        "effort_hours": 2,
        "auto_fixable": True,
    },
    "threats_documented": {
        "step": "Run STRIDE analysis and document identified threats with risk ratings.",
        "effort_hours": 4,
        "auto_fixable": True,
    },
    # Endpoint Detection & Response
    "edr_present": {
        "step": "Deploy EDR/XDR solution (CrowdStrike, SentinelOne, Microsoft Defender for Endpoint, or equivalent) on all servers and endpoints. Enable real-time threat detection and automated response.",
        "effort_hours": 20,
        "auto_fixable": False,
    },
    # Cloud Security Posture Management
    "cspm_present": {
        "step": "Deploy CSPM solution (Prisma Cloud, Wiz, AWS Security Hub, Azure Defender, or equivalent). Enable continuous compliance scanning and misconfiguration detection.",
        "effort_hours": 16,
        "auto_fixable": False,
    },
    # Backup & DR
    "backup_present": {
        "step": "Implement backup and disaster recovery strategy. Configure automated backups with RPO ≤ 24h and RTO ≤ 4h. Test restore procedures quarterly. Document in contingency plan (NIST CP-9/CP-10).",
        "effort_hours": 16,
        "auto_fixable": False,
    },
    # Secret Management
    "secret_mgmt_present": {
        "step": "Deploy centralized secrets manager (HashiCorp Vault, AWS Secrets Manager, Azure Key Vault). Implement automated rotation with ≤ 90 day cycle. Eliminate hardcoded credentials.",
        "effort_hours": 16,
        "auto_fixable": False,
    },
    # mTLS
    "mtls_s2s": {
        "step": "Enforce mutual TLS (mTLS) on all service-to-service communication. Deploy service mesh (Istio, Linkerd) or certificate-based auth (SPIFFE/SPIRE). Automate certificate rotation.",
        "effort_hours": 24,
        "auto_fixable": False,
    },
    # API Gateway
    "api_gateway_protected": {
        "step": "Deploy API gateway (Kong, AWS API Gateway, Azure APIM) with WAF, rate limiting, OAuth2/OIDC authentication, and request schema validation for all external-facing APIs.",
        "effort_hours": 16,
        "auto_fixable": False,
    },
    # Zero Trust
    "zero_trust_posture": {
        "step": "Enforce Zero Trust model: every data flow must have BOTH encryption (TLS 1.2+) AND authentication (mTLS, OAuth2, API key). Implement microsegmentation with identity-based access control per NIST 800-207.",
        "effort_hours": 40,
        "auto_fixable": False,
    },
    # Admission Control
    "admission_control_present": {
        "step": "Deploy Kubernetes admission controller (Kyverno, OPA Gatekeeper, or Kubewarden). Enforce policies: no privileged containers, image signature verification, resource limits, network policies required.",
        "effort_hours": 12,
        "auto_fixable": False,
    },
    # FIPS Crypto
    "fips_crypto_validated": {
        "step": "Ensure all cryptographic modules are FIPS 140-2/3 validated. Verify CMVP certificate numbers. Replace non-validated modules with certified alternatives (NIST SC-13).",
        "effort_hours": 16,
        "auto_fixable": False,
    },
    # Hardening Baseline
    "hardening_baseline": {
        "step": "Apply CIS Benchmark or DISA STIG hardening baseline to all servers. Use configuration management (Ansible, Chef, Puppet) to enforce and scan for drift. Document baseline in CM-2.",
        "effort_hours": 12,
        "auto_fixable": False,
    },
    # Shared Admin Accounts (existing rule, missing remediation)
    "no_shared_admin": {
        "step": "Audit and eliminate shared administrative accounts. Implement individual named accounts with PAM session recording and just-in-time access elevation.",
        "effort_hours": 8,
        "auto_fixable": False,
    },
    # Incident Response Runbook (existing rule, missing remediation)
    "ir_runbook": {
        "step": "Document incident response runbooks with escalation procedures, contact lists, recovery objectives, and communication plans. Link to SIEM alerting rules for auto-trigger.",
        "effort_hours": 8,
        "auto_fixable": False,
    },
}

# Default step for unknown checks
_DEFAULT_STEP = {
    "step": "Review finding and implement appropriate remediation per NIST 800-53 guidance.",
    "effort_hours": 8,
    "auto_fixable": False,
}


def generate_remediation_plan(assessment: dict, design_data: dict) -> dict:
    """Generate a phased remediation plan from assessment findings.

    Phase 1: CAT1 critical findings — 0–48 hours
    Phase 2: CAT2 high findings — 1–2 weeks
    Phase 3: CAT3 moderate findings — 30 days

    Args:
        assessment: Output from run_security_assessment().
        design_data: The design graph_data dict (for context).

    Returns:
        Dict with phases, actions, summary, and timestamps.
    """
    findings = assessment.get("findings", [])
    now = datetime.now(timezone.utc)

    phases = {
        1: {"label": "Critical (0–48h)", "deadline": (now + timedelta(hours=48)).isoformat(), "actions": []},
        2: {"label": "High (1–2 weeks)", "deadline": (now + timedelta(weeks=2)).isoformat(), "actions": []},
        3: {"label": "Moderate (30 days)", "deadline": (now + timedelta(days=30)).isoformat(), "actions": []},
    }

    for finding in findings:
        severity = finding.get("severity", "CAT3")
        phase_info = _SEVERITY_PHASES.get(severity, _SEVERITY_PHASES["CAT3"])
        phase_num = phase_info["phase"]

        # Try to find remediation step by the check function name
        # We need to look up the rule to get the check name
        step_info = _DEFAULT_STEP
        for rule in assessment.get("_rules", []):
            if rule.get("id") == finding.get("rule_id"):
                step_info = _REMEDIATION_STEPS.get(rule.get("check", ""), _DEFAULT_STEP)
                break
        else:
            # Fallback: try matching by category + rule_id pattern
            for check_key, check_step in _REMEDIATION_STEPS.items():
                # Match on rule_id suffix pattern
                if check_key in finding.get("rule_id", "").lower():
                    step_info = check_step
                    break

        action = {
            "id": str(uuid.uuid4())[:8],
            "finding_id": finding.get("rule_id", ""),
            "title": finding.get("title", ""),
            "severity": severity,
            "remediation_step": step_info["step"],
            "effort_hours": step_info["effort_hours"],
            "auto_fixable": step_info["auto_fixable"],
            "affected_entity": finding.get("affected_entity", ""),
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

    # Risk level
    cat1_count = sum(1 for p in phase_list for a in p["actions"] if a["severity"] == "CAT1")
    risk_score = assessment.get("risk_score", 0)
    if cat1_count >= 3 or risk_score < 30:
        risk = "CRITICAL"
    elif cat1_count >= 1 or risk_score < 50:
        risk = "HIGH"
    elif risk_score < 70:
        risk = "MODERATE"
    else:
        risk = "LOW"

    summary_parts = []
    for p in phase_list:
        summary_parts.append(
            f"{len(p['actions'])} {p['priority'].lower()} action{'s' if len(p['actions']) != 1 else ''}"
        )
    summary_text = ". ".join(summary_parts) + "." if summary_parts else "No actions needed."
    if auto_fixable:
        summary_text += f" {auto_fixable} of {total_actions} can be auto-fixed."

    return {
        "design_id": assessment.get("design_id", ""),
        "plan_id": str(uuid.uuid4()),
        "phases": phase_list,
        "summary": summary_text,
        "total_actions": total_actions,
        "auto_fixable": auto_fixable,
        "estimated_effort": estimate_effort([a for p in phase_list for a in p["actions"]]),
        "overall_risk": risk,
        "posture_grade": assessment.get("posture_grade", "F"),
        "risk_score": assessment.get("risk_score", 0),
        "created_at": now.isoformat(),
    }


# ── POA&M Entry Generation ──────────────────────────────────────────────────


def generate_poam_entries(remediation_plan: dict) -> list:
    """Convert remediation plan actions to POA&M (Plan of Action & Milestones) entries.

    Each entry follows the OMB POA&M format required for FedRAMP/ATO packages.

    Args:
        remediation_plan: Output from generate_remediation_plan().

    Returns:
        List of POA&M entry dicts.
    """
    entries = []
    now = datetime.now(timezone.utc)
    seq = 1

    # ``generate_remediation_plan`` emits ``phases`` as a LIST of phase dicts
    # (each carrying its own ``phase`` number), which is the shape every other
    # consumer — ``generate_poam_artifact``, ``generate_sar_artifact``, and the
    # blueprint export routes — reads. Iterate that list directly.
    for phase in remediation_plan.get("phases", []):
        phase_num = phase.get("phase", "")
        deadline = phase.get("deadline", now.isoformat())
        for action in phase.get("actions", []):
            entry = {
                "poam_id": f"POAM-{seq:04d}",
                "weakness_id": action.get("finding_id", ""),
                "weakness_name": action.get("title", ""),
                "weakness_description": action.get("remediation_step", ""),
                "point_of_contact": action.get("assigned_to", "Security Team"),
                "resources_required": f"{action.get('effort_hours', 8)} hours — "
                f"{'auto-fixable' if action.get('auto_fixable') else 'manual remediation'}",
                "severity": action.get("severity", "CAT3"),
                "milestone": f"Phase {phase_num}: {action.get('title', 'Remediate finding')}",
                "milestone_changes": "",
                "scheduled_completion": deadline,
                "status": "Open",
                "comments": f"Affected: {action.get('affected_entity', 'design')}",
                "risk_level": "High"
                if action.get("severity") == "CAT1"
                else "Medium"
                if action.get("severity") == "CAT2"
                else "Low",
                "created_at": now.isoformat(),
            }
            entries.append(entry)
            seq += 1

    return entries


# ── Effort Estimation ────────────────────────────────────────────────────────


def estimate_effort(steps: list) -> str:
    """Sum effort hours from remediation steps and format as human-readable string.

    Args:
        steps: List of action dicts with effort_hours field.

    Returns:
        Formatted string like "40 hours" or "5 days".
    """
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
