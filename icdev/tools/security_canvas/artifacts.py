# [CUI // SP-CTI]
"""ICDEV(TM) Security Design Canvas -- ATO Artifact Export.

Pure functions for generating SSP, SAR, and POA&M documents from
security design assessment data.  Markdown output, no LLM dependency.
"""

from datetime import datetime, timezone

from tools.security_canvas.security_engine import (
    run_security_assessment,
    compute_nist_coverage,
)
from tools.security_canvas.remediation import generate_remediation_plan


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── SSP Generator ─────────────────────────────────────────────────────────────


def generate_ssp_artifact(
    design_name: str, design_id: str, graph_data: dict, assessment: dict, nist_coverage: dict
) -> str:
    """Generate a System Security Plan (SSP) as Markdown.

    Args:
        design_name: Human-readable name of the design.
        design_id: UUID of the security design.
        graph_data: The design graph dict (nodes, edges, boundaries).
        assessment: Output from run_security_assessment().
        nist_coverage: Output from compute_nist_coverage().

    Returns:
        Markdown string containing the SSP document.
    """
    generated_at = _now()
    risk_score = assessment.get("risk_score", 0)
    posture_grade = assessment.get("posture_grade", "F")
    findings = assessment.get("findings", [])

    boundaries = graph_data.get("boundaries", [])
    nodes = graph_data.get("nodes", [])

    families = nist_coverage.get("families", {})
    overall_pct = nist_coverage.get("overall_coverage_pct", 0)
    covered = nist_coverage.get("covered_families", 0)
    total = nist_coverage.get("total_families", 20)

    lines = []

    # Cover page
    lines.append("# System Security Plan (SSP)")
    lines.append("")
    lines.append("**CUI // SP-CTI**")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|-------|-------|")
    lines.append(f"| System Name | {design_name} |")
    lines.append(f"| Design ID | {design_id} |")
    lines.append("| Classification | CUI // SP-CTI |")
    lines.append(f"| Generated | {generated_at} |")
    lines.append(f"| Risk Score | {risk_score}/100 |")
    lines.append(f"| Posture Grade | {posture_grade} |")
    lines.append("")

    # System Description
    lines.append("## 1. System Description")
    lines.append("")
    lines.append(
        f"This System Security Plan documents the security controls "
        f"implemented for **{design_name}**. The system comprises "
        f"{len(nodes)} components and {len(boundaries)} trust "
        f"boundaries."
    )
    lines.append("")

    # Authorization Boundary
    lines.append("## 2. Authorization Boundary")
    lines.append("")
    if boundaries:
        lines.append("| Boundary | Type |")
        lines.append("|----------|------|")
        for b in boundaries:
            lines.append(f"| {b.get('label', b.get('id', ''))} | {b.get('type', 'boundary')} |")
    else:
        lines.append("*No authorization boundaries defined. Use FedRAMP Boundary Auto-Draw to generate.*")
    lines.append("")

    # Information Types & Impact Level
    lines.append("## 3. Information Types and Impact Level")
    lines.append("")
    lines.append("| Category | Confidentiality | Integrity | Availability |")
    lines.append("|----------|-----------------|-----------|--------------|")
    lines.append("| CUI | Moderate | Moderate | Moderate |")
    lines.append("| PII | Moderate | Moderate | Low |")
    lines.append("")

    # Security Control Implementation
    lines.append("## 4. Security Control Implementation")
    lines.append("")
    lines.append(f"Overall NIST 800-53 coverage: **{overall_pct}%** ({covered}/{total} families addressed)")
    lines.append("")
    lines.append("| Family | Name | Coverage | Status |")
    lines.append("|--------|------|----------|--------|")
    for fam_code in sorted(families.keys()):
        fam = families[fam_code]
        pct = fam.get("coverage_pct", 0)
        status = fam.get("status", "none")
        status_icon = {"full": "Implemented", "partial": "Partially Implemented", "none": "Not Implemented"}.get(
            status, "Unknown"
        )
        lines.append(f"| {fam_code} | {fam.get('name', '')} | {pct}% | {status_icon} |")
    lines.append("")

    # Findings Summary
    lines.append("## 5. Security Findings Summary")
    lines.append("")
    lines.append(f"Total findings: **{len(findings)}**")
    lines.append("")
    if findings:
        by_sev = {}
        for f in findings:
            sev = f.get("severity", "CAT3")
            by_sev[sev] = by_sev.get(sev, 0) + 1
        lines.append("| Severity | Count |")
        lines.append("|----------|-------|")
        for sev in ("CAT1", "CAT2", "CAT3"):
            if sev in by_sev:
                lines.append(f"| {sev} | {by_sev[sev]} |")
    lines.append("")

    # Continuous Monitoring Strategy
    lines.append("## 6. Continuous Monitoring Strategy")
    lines.append("")
    lines.append("- Automated vulnerability scanning: Weekly")
    lines.append("- SIEM log review: Continuous with 15-minute alerting")
    lines.append("- Configuration baseline compliance: Daily")
    lines.append("- POA&M review: Monthly")
    lines.append("- Annual security assessment and penetration testing")
    lines.append("")

    # Appendices
    lines.append("## Appendices")
    lines.append("")
    lines.append("- Appendix A: Network Architecture Diagram (see Security Design Canvas)")
    lines.append("- Appendix B: NIST 800-53 Control Matrix")
    lines.append("- Appendix C: POA&M (see POA&M artifact)")
    lines.append("")
    lines.append("---")
    lines.append("*Generated by ICDEV(TM) Security Design Canvas*")

    return "\n".join(lines)


# ── SAR Generator ─────────────────────────────────────────────────────────────


def generate_sar_artifact(design_name: str, assessment: dict, remediation_plan: dict) -> str:
    """Generate a Security Assessment Report (SAR) as Markdown.

    Args:
        design_name: Human-readable name of the design.
        assessment: Output from run_security_assessment().
        remediation_plan: Output from generate_remediation_plan().

    Returns:
        Markdown string containing the SAR document.
    """
    generated_at = _now()
    risk_score = assessment.get("risk_score", 0)
    posture_grade = assessment.get("posture_grade", "F")
    findings = assessment.get("findings", [])

    by_sev = {}
    for f in findings:
        sev = f.get("severity", "CAT3")
        by_sev[sev] = by_sev.get(sev, 0) + 1

    lines = []

    # Header
    lines.append("# Security Assessment Report (SAR)")
    lines.append("")
    lines.append("**CUI // SP-CTI**")
    lines.append("")

    # Executive Summary
    lines.append("## 1. Executive Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| System | {design_name} |")
    lines.append(f"| Assessment Date | {generated_at} |")
    lines.append(f"| Posture Grade | {posture_grade} |")
    lines.append(f"| Risk Score | {risk_score}/100 |")
    lines.append(f"| Total Findings | {len(findings)} |")
    for sev in ("CAT1", "CAT2", "CAT3"):
        if sev in by_sev:
            lines.append(f"| {sev} Findings | {by_sev[sev]} |")
    lines.append(f"| Overall Risk | {remediation_plan.get('overall_risk', 'N/A')} |")
    lines.append("")

    # Assessment Methodology
    lines.append("## 2. Assessment Methodology")
    lines.append("")
    lines.append(
        "Deterministic rule-based assessment using 25 NIST 800-53 "
        "controls mapped across 7 security domains: Authentication, "
        "Encryption, Segmentation, Logging, Monitoring, Access "
        "Control, and Data Protection."
    )
    lines.append("")
    lines.append(
        "Assessment checks are applied against the system's security "
        "design graph, which models components, data flows, trust "
        "boundaries, and security controls."
    )
    lines.append("")

    # Findings Detail
    lines.append("## 3. Findings Detail")
    lines.append("")
    if findings:
        lines.append("| # | Rule ID | Title | Severity | Category | Affected Entity |")
        lines.append("|---|---------|-------|----------|----------|----------------|")
        for i, f in enumerate(findings, 1):
            lines.append(
                f"| {i} | {f.get('rule_id', '')} | {f.get('title', '')} "
                f"| {f.get('severity', '')} | {f.get('category', '')} "
                f"| {f.get('affected_entity', '')} |"
            )
    else:
        lines.append("*No findings identified. System meets all assessed controls.*")
    lines.append("")

    # Risk Summary
    lines.append("## 4. Risk Summary")
    lines.append("")
    lines.append(f"The system achieved a risk score of **{risk_score}/100** (grade: **{posture_grade}**). ")
    if by_sev.get("CAT1", 0) > 0:
        lines.append(
            f"**{by_sev['CAT1']} CAT1 (critical) findings** must be "
            f"resolved before Authorization to Operate (ATO) can be "
            f"granted."
        )
    elif by_sev.get("CAT2", 0) > 0:
        lines.append(f"{by_sev['CAT2']} CAT2 (high) findings should be resolved or documented in the POA&M.")
    else:
        lines.append("No critical or high findings. System is a strong candidate for ATO.")
    lines.append("")

    # Recommendations
    lines.append("## 5. Recommendations")
    lines.append("")
    phases = remediation_plan.get("phases", [])
    if phases:
        for phase in phases:
            lines.append(f"### {phase.get('name', '')}")
            lines.append(f"*Priority: {phase.get('priority', '')} | Deadline: {phase.get('deadline', '')}*")
            lines.append("")
            for action in phase.get("actions", []):
                lines.append(f"- {action.get('remediation_step', '')}")
            lines.append("")
    else:
        lines.append("*No remediation actions required.*")
    lines.append("")

    lines.append("---")
    lines.append("*Generated by ICDEV(TM) Security Design Canvas*")

    return "\n".join(lines)


# ── POA&M Generator ──────────────────────────────────────────────────────────


def generate_poam_artifact(design_name: str, remediation_plan: dict) -> str:
    """Generate a Plan of Action & Milestones (POA&M) as Markdown.

    Args:
        design_name: Human-readable name of the design.
        remediation_plan: Output from generate_remediation_plan().

    Returns:
        Markdown string containing the POA&M table.
    """
    generated_at = _now()
    phases = remediation_plan.get("phases", [])

    lines = []

    lines.append("# Plan of Action & Milestones (POA&M)")
    lines.append("")
    lines.append("**CUI // SP-CTI**")
    lines.append("")
    lines.append(f"**System:** {design_name}  ")
    lines.append(f"**Generated:** {generated_at}  ")
    lines.append(f"**Overall Risk:** {remediation_plan.get('overall_risk', 'N/A')}  ")
    lines.append(f"**Total Actions:** {remediation_plan.get('total_actions', 0)}")
    lines.append("")

    lines.append("| POAM-ID | Weakness | Severity | Milestone | Scheduled Completion | Status | Resources Required |")
    lines.append("|---------|----------|----------|-----------|---------------------|--------|-------------------|")

    poam_num = 1
    for phase in phases:
        deadline = phase.get("deadline", "TBD")
        milestone = phase.get("name", "")
        for action in phase.get("actions", []):
            poam_id = f"POAM-{poam_num:04d}"
            weakness = action.get("title", "")
            severity = action.get("severity", "CAT3")
            status = action.get("status", "open").capitalize()
            effort = action.get("effort_hours", 8)
            resources = f"{effort}h engineering"
            if action.get("auto_fixable"):
                resources += " (auto-fixable)"
            lines.append(f"| {poam_id} | {weakness} | {severity} | {milestone} | {deadline} | {status} | {resources} |")
            poam_num += 1

    if poam_num == 1:
        lines.append("| -- | No actions required | -- | -- | -- | -- | -- |")

    lines.append("")
    lines.append("---")
    lines.append("*Generated by ICDEV(TM) Security Design Canvas*")

    return "\n".join(lines)


# ── Bundle Orchestrator ───────────────────────────────────────────────────────


def generate_artifact_bundle(design_id: str, design_name: str, graph_data: dict) -> dict:
    """Orchestrate generation of SSP, SAR, and POA&M artifacts.

    Loads assessment data, computes NIST coverage, generates a remediation
    plan, then calls all three artifact generators.

    Args:
        design_id: UUID of the security design.
        design_name: Human-readable name.
        graph_data: The design graph dict.

    Returns:
        Dict with ssp, sar, poam strings and metadata.
    """
    assessment = run_security_assessment(design_id, graph_data)
    nist_coverage = compute_nist_coverage(graph_data)
    remediation_plan = generate_remediation_plan(assessment, graph_data)

    ssp = generate_ssp_artifact(
        design_name,
        design_id,
        graph_data,
        assessment,
        nist_coverage,
    )
    sar = generate_sar_artifact(design_name, assessment, remediation_plan)
    poam = generate_poam_artifact(design_name, remediation_plan)

    return {
        "ssp": ssp,
        "sar": sar,
        "poam": poam,
        "metadata": {
            "design_name": design_name,
            "generated_at": _now(),
            "risk_score": assessment.get("risk_score", 0),
            "posture_grade": assessment.get("posture_grade", "F"),
        },
    }
