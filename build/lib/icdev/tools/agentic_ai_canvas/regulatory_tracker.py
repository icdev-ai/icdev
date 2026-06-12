# CUI // SP-CTI
"""Agentic AI Design Canvas — Regulatory Requirements Tracker (Phase 6).

Gap analysis against EU AI Act, DoD AI Ethics, OMB M-25-21, OMB M-26-04,
and NIST AI RMF governance requirements.
"""

from __future__ import annotations

from tools.agentic_ai_canvas.constants import GOVERNANCE_NODES, MODEL_NODES

_HITL_TYPES = {"hitl-gate", "approval-workflow", "caio-override"}
_AUDIT_TYPES = {"audit-logger", "compliance-reporter"}
_SAFETY_TYPES = {"guardrail", "input-sanitizer", "toxicity-filter", "circuit-breaker",
                 "confidence-threshold", "rate-limiter", "pii-detector", "redaction-engine"}
_MONITOR_TYPES = {"trusted-monitor", "drift-detector", "baseline-snapshot"}
_CB_TYPES = {"circuit-breaker", "confidence-threshold"}
_GOV_TYPES = GOVERNANCE_NODES
_DATA_TYPES = {"vector-db", "knowledge-graph"}

_REGS: list[dict] = [
    # ── EU AI Act ──────────────────────────────────────────────────────────
    {
        "id": "EU-01", "framework": "EU AI Act", "article": "Art. 9",
        "title": "Risk Management System",
        "requirement": "High-risk AI must have a documented risk management lifecycle.",
        "check": "risk_register",
        "severity": "HIGH",
        "gap_advice": "Create risk items in the Risk Register for this design.",
    },
    {
        "id": "EU-02", "framework": "EU AI Act", "article": "Art. 10",
        "title": "Data Governance",
        "requirement": "Training/validation data must be governed and documented.",
        "check": "has_data_nodes",
        "severity": "HIGH",
        "gap_advice": "Add a Vector Store or Knowledge Graph node and document data governance.",
    },
    {
        "id": "EU-03", "framework": "EU AI Act", "article": "Art. 13",
        "title": "Transparency & Logging",
        "requirement": "High-risk AI must provide transparency and maintain logs.",
        "check": "has_audit_logger",
        "severity": "HIGH",
        "gap_advice": "Add an Audit Logger node.",
    },
    {
        "id": "EU-04", "framework": "EU AI Act", "article": "Art. 14",
        "title": "Human Oversight",
        "requirement": "High-risk AI must enable human oversight and intervention.",
        "check": "has_hitl",
        "severity": "CRITICAL",
        "gap_advice": "Add a HITL Gate or Approval Workflow node.",
    },
    {
        "id": "EU-05", "framework": "EU AI Act", "article": "Art. 15",
        "title": "Accuracy, Robustness & Cybersecurity",
        "requirement": "Appropriate levels of accuracy, robustness, and cybersecurity.",
        "check": "has_safety_node",
        "severity": "HIGH",
        "gap_advice": "Add Guardrail, Circuit Breaker, or Rate Limiter nodes.",
    },
    # ── DoD AI Ethics ──────────────────────────────────────────────────────
    {
        "id": "DOD-E01", "framework": "DoD AI Ethics", "article": "Responsible",
        "title": "Responsible AI Use",
        "requirement": "AI outcomes consistent with law and ethical principles.",
        "check": "has_governance",
        "severity": "HIGH",
        "gap_advice": "Add a Compliance Reporter or CAIO Override node.",
    },
    {
        "id": "DOD-E02", "framework": "DoD AI Ethics", "article": "Equitable",
        "title": "Equitable Outcomes",
        "requirement": "AI must avoid unlawful bias — monitor for drift.",
        "check": "has_monitor",
        "severity": "MEDIUM",
        "gap_advice": "Add a Drift Detector or Trusted Monitor node.",
    },
    {
        "id": "DOD-E03", "framework": "DoD AI Ethics", "article": "Traceable",
        "title": "Traceable Decisions",
        "requirement": "AI is sufficiently transparent for human review.",
        "check": "has_audit_logger",
        "severity": "HIGH",
        "gap_advice": "Add an Audit Logger for decision traceability.",
    },
    {
        "id": "DOD-E04", "framework": "DoD AI Ethics", "article": "Reliable",
        "title": "Reliable & Resilient",
        "requirement": "AI performs reliably across expected conditions.",
        "check": "has_circuit_breaker",
        "severity": "MEDIUM",
        "gap_advice": "Add a Circuit Breaker or Confidence Threshold node.",
    },
    {
        "id": "DOD-E05", "framework": "DoD AI Ethics", "article": "Governable",
        "title": "Governable by Humans",
        "requirement": "Humans can disengage, adjust, or correct AI behavior.",
        "check": "has_hitl",
        "severity": "CRITICAL",
        "gap_advice": "Add a HITL Gate so humans can intervene in AI decisions.",
    },
    # ── OMB M-25-21 ────────────────────────────────────────────────────────
    {
        "id": "OMB25-01", "framework": "OMB M-25-21", "article": "§4(a)",
        "title": "Safety-Impacting: HITL Mandatory",
        "requirement": "Federal AI with safety impact requires human review.",
        "check": "has_hitl_if_safety",
        "severity": "CRITICAL",
        "gap_advice": "Add a HITL Gate; safety-impacting designs require it.",
    },
    {
        "id": "OMB25-02", "framework": "OMB M-25-21", "article": "§5",
        "title": "AI Inventory & Documentation",
        "requirement": "All federal AI must be inventoried and documented.",
        "check": "has_name_desc",
        "severity": "MEDIUM",
        "gap_advice": "Ensure design has a name and description for AI inventory.",
    },
    # ── OMB M-26-04 ────────────────────────────────────────────────────────
    {
        "id": "OMB26-01", "framework": "OMB M-26-04", "article": "§3",
        "title": "AI Security Baseline Controls",
        "requirement": "Federal AI must implement NIST AI RMF security baseline.",
        "check": "has_safety_node",
        "severity": "HIGH",
        "gap_advice": "Add Guardrail, Input Sanitizer, or Toxicity Filter nodes.",
    },
    {
        "id": "OMB26-02", "framework": "OMB M-26-04", "article": "§4",
        "title": "Supply Chain Verification",
        "requirement": "AI model sources and supply chain must be documented.",
        "check": "has_provenance",
        "severity": "MEDIUM",
        "gap_advice": "Document model_source and training_data in model node properties.",
    },
]


def run_regulatory_analysis(
    nodes: list[dict],
    design_meta: dict,
    risk_items: list[dict],
) -> dict:
    """
    Run regulatory gap analysis.

    Returns dict with keys: gaps, summary, by_framework.
    """
    types = {n.get("type", "") for n in nodes}
    safety_impacting = bool(design_meta.get("safety_impacting"))
    has_risks = bool(risk_items)
    has_provenance = any(
        (n.get("metadata") or {}).get("model_source") or n.get("model_source")
        for n in nodes
        if n.get("type") in MODEL_NODES
    )
    has_name = bool(design_meta.get("name", "").strip())
    has_desc = bool(design_meta.get("description", "").strip())

    def _check(key: str) -> bool:
        if key == "risk_register":
            return has_risks
        if key == "has_data_nodes":
            return bool(types & _DATA_TYPES)
        if key == "has_audit_logger":
            return bool(types & _AUDIT_TYPES)
        if key == "has_hitl":
            return bool(types & _HITL_TYPES)
        if key == "has_hitl_if_safety":
            return (not safety_impacting) or bool(types & _HITL_TYPES)
        if key == "has_safety_node":
            return bool(types & _SAFETY_TYPES)
        if key == "has_governance":
            return bool(types & _GOV_TYPES)
        if key == "has_monitor":
            return bool(types & _MONITOR_TYPES)
        if key == "has_circuit_breaker":
            return bool(types & _CB_TYPES)
        if key == "has_provenance":
            return has_provenance
        if key == "has_name_desc":
            return has_name and has_desc
        return False

    gaps = []
    frameworks: list[str] = []
    by_framework: dict[str, dict] = {}
    for reg in _REGS:
        compliant = _check(reg["check"])
        gaps.append({
            "id": reg["id"],
            "framework": reg["framework"],
            "article": reg["article"],
            "title": reg["title"],
            "requirement": reg["requirement"],
            "severity": reg["severity"],
            "status": "compliant" if compliant else "gap",
            "gap_advice": reg["gap_advice"] if not compliant else "",
        })
        fw = reg["framework"]
        if fw not in by_framework:
            by_framework[fw] = {"compliant": 0, "total": 0}
            frameworks.append(fw)
        by_framework[fw]["total"] += 1
        if compliant:
            by_framework[fw]["compliant"] += 1

    total = len(gaps)
    compliant_count = sum(1 for g in gaps if g["status"] == "compliant")
    critical_gaps = sum(1 for g in gaps if g["status"] == "gap" and g["severity"] == "CRITICAL")

    return {
        "gaps": gaps,
        "summary": {
            "total": total,
            "compliant": compliant_count,
            "gaps": total - compliant_count,
            "critical_gaps": critical_gaps,
            "score_pct": round(compliant_count / total * 100, 1) if total else 0.0,
            "frameworks": frameworks,
        },
        "by_framework": by_framework,
    }
