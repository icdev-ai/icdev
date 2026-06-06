# CUI // SP-CTI
"""Agentic AI Design Canvas — ATO Readiness Checker (Phase 6).

Maps AADC design graph → FedRAMP High / CMMC L2 / OMB M-25-21 / DoD AI Ethics
checklist items. Produces an interactive readiness report for ATO packages.
"""

from __future__ import annotations

from tools.agentic_ai_canvas.constants import GOVERNANCE_NODES

_HITL_TYPES = {"hitl-gate", "approval-workflow", "caio-override"}
_AUDIT_TYPES = {"audit-logger", "compliance-reporter"}
_PII_TYPES = {"pii-detector", "pii-field-detector", "redaction-engine"}
_GUARD_TYPES = {"guardrail", "input-sanitizer", "toxicity-filter"}
_CB_TYPES = {"circuit-breaker", "confidence-threshold"}
_MONITOR_TYPES = {"trusted-monitor", "drift-detector", "baseline-snapshot"}
_RATE_TYPES = {"rate-limiter"}
_PROMPT_TYPES = {"prompt-registry"}
_GOV_TYPES = GOVERNANCE_NODES

CHECKLIST_ITEMS: list[dict] = [
    # ── FedRAMP / NIST 800-53 Rev 5 ───────────────────────────────────────
    {
        "id": "FED-01", "framework": "FedRAMP", "control": "AU-2",
        "title": "Audit Logging Configured",
        "description": "Design includes an audit-logger or compliance-reporter node.",
        "required_types": _AUDIT_TYPES,
        "severity": "HIGH",
        "remediation": "Add an Audit Logger node to your design.",
    },
    {
        "id": "FED-02", "framework": "FedRAMP", "control": "SI-3",
        "title": "Input Validation / Guardrail Present",
        "description": "Design includes input validation or a guardrail node.",
        "required_types": _GUARD_TYPES,
        "severity": "HIGH",
        "remediation": "Add an Input Sanitizer or Guardrail node.",
    },
    {
        "id": "FED-03", "framework": "FedRAMP", "control": "AC-3",
        "title": "Human-in-the-Loop Gate Present",
        "description": "Design includes a HITL gate or approval workflow.",
        "required_types": _HITL_TYPES,
        "severity": "HIGH",
        "remediation": "Add a HITL Gate or Approval Workflow node.",
    },
    {
        "id": "FED-04", "framework": "FedRAMP", "control": "SC-28",
        "title": "PII Detection Configured",
        "description": "Design includes PII detection for personal information.",
        "required_types": _PII_TYPES,
        "severity": "MEDIUM",
        "remediation": "Add a PII Detector or Redaction Engine node.",
    },
    {
        "id": "FED-05", "framework": "FedRAMP", "control": "SI-17",
        "title": "Circuit Breaker / Failure Mode Configured",
        "description": "Design includes circuit breaker or confidence threshold.",
        "required_types": _CB_TYPES,
        "severity": "MEDIUM",
        "remediation": "Add a Circuit Breaker node.",
    },
    {
        "id": "FED-06", "framework": "FedRAMP", "control": "SC-5",
        "title": "Rate Limiting Implemented",
        "description": "Design includes rate limiter to prevent resource exhaustion.",
        "required_types": _RATE_TYPES,
        "severity": "MEDIUM",
        "remediation": "Add a Rate Limiter node.",
    },
    {
        "id": "FED-07", "framework": "FedRAMP", "control": "SI-4",
        "title": "Monitoring / Drift Detection Active",
        "description": "Design includes trusted monitor or drift detector.",
        "required_types": _MONITOR_TYPES,
        "severity": "MEDIUM",
        "remediation": "Add a Trusted Monitor or Drift Detector node.",
    },
    {
        "id": "FED-08", "framework": "FedRAMP", "control": "CM-3",
        "title": "Prompt Registry / Configuration Management",
        "description": "System prompts are version-controlled via a prompt registry.",
        "required_types": _PROMPT_TYPES,
        "severity": "LOW",
        "remediation": "Add a Prompt Registry node.",
    },
    # ── OMB M-25-21 ────────────────────────────────────────────────────────
    {
        "id": "OMB-01", "framework": "OMB M-25-21", "control": "§4(a)",
        "title": "Safety-Impacting AI: HITL Required",
        "description": "Safety-impacting domains require at least one HITL gate.",
        "required_types": _HITL_TYPES,
        "severity": "CRITICAL",
        "domain_filter": "safety_impacting",
        "remediation": "Add a HITL Gate for safety-impacting decisions.",
    },
    {
        "id": "OMB-02", "framework": "OMB M-25-21", "control": "§4(b)",
        "title": "Rights-Impacting AI: HITL Required",
        "description": "Rights-impacting domains require at least one HITL gate.",
        "required_types": _HITL_TYPES,
        "severity": "CRITICAL",
        "domain_filter": "rights_impacting",
        "remediation": "Add a HITL Gate for rights-impacting decisions.",
    },
    {
        "id": "OMB-03", "framework": "OMB M-25-21", "control": "§5",
        "title": "AI Audit Trail Maintained",
        "description": "AI decisions are logged to an immutable audit trail.",
        "required_types": _AUDIT_TYPES,
        "severity": "HIGH",
        "remediation": "Add an Audit Logger node.",
    },
    # ── DoD AI Ethics Principles ───────────────────────────────────────────
    {
        "id": "DOD-01", "framework": "DoD AI Ethics", "control": "Governable",
        "title": "Governance Oversight Node Present",
        "description": "Design includes a governance/oversight node.",
        "required_types": _GOV_TYPES,
        "severity": "HIGH",
        "remediation": "Add a CAIO Override or Compliance Reporter node.",
    },
    {
        "id": "DOD-02", "framework": "DoD AI Ethics", "control": "Traceable",
        "title": "Explainability / Traceability Configured",
        "description": "Design includes observability or explainability module.",
        "required_types": _MONITOR_TYPES | _AUDIT_TYPES,
        "severity": "HIGH",
        "remediation": "Add an Audit Logger or Trusted Monitor for traceability.",
    },
    {
        "id": "DOD-03", "framework": "DoD AI Ethics", "control": "Reliable",
        "title": "Reliability Guards Present",
        "description": "Design includes circuit breaker, confidence threshold, or rate limiter.",
        "required_types": _CB_TYPES | _RATE_TYPES,
        "severity": "MEDIUM",
        "remediation": "Add a Circuit Breaker or Confidence Threshold node.",
    },
    # ── CMMC Level 2 ──────────────────────────────────────────────────────
    {
        "id": "CMMC-01", "framework": "CMMC L2", "control": "SI.2.214",
        "title": "Malicious Code Protection",
        "description": "Output filtering prevents injection of malicious content.",
        "required_types": _GUARD_TYPES,
        "severity": "HIGH",
        "remediation": "Add a Toxicity Filter or Input Sanitizer node.",
    },
    {
        "id": "CMMC-02", "framework": "CMMC L2", "control": "AU.2.041",
        "title": "Audit Log Review Mechanism",
        "description": "Audit logs include automated review/alerting capability.",
        "required_types": {"compliance-reporter", "drift-detector"},
        "severity": "MEDIUM",
        "remediation": "Add a Compliance Reporter for automated log review.",
    },
]


def run_ato_checklist(nodes: list[dict], design_meta: dict) -> dict:
    """
    Run ATO readiness checklist against a design graph.

    Returns dict with keys: items, summary, by_framework.
    """
    node_types = {n.get("type", "") for n in nodes}
    safety_impacting = bool(design_meta.get("safety_impacting"))
    rights_impacting = bool(design_meta.get("rights_impacting"))

    results = []
    for item in CHECKLIST_ITEMS:
        df = item.get("domain_filter")
        if df == "safety_impacting" and not safety_impacting:
            continue
        if df == "rights_impacting" and not rights_impacting:
            continue

        passed = bool(node_types & item["required_types"])
        results.append({
            "id": item["id"],
            "framework": item["framework"],
            "control": item["control"],
            "title": item["title"],
            "description": item["description"],
            "severity": item["severity"],
            "status": "pass" if passed else "fail",
            "remediation": item["remediation"] if not passed else "",
        })

    total = len(results)
    passed_count = sum(1 for r in results if r["status"] == "pass")
    critical_failed = sum(1 for r in results if r["status"] == "fail" and r["severity"] == "CRITICAL")
    score_pct = round(passed_count / total * 100, 1) if total else 0.0
    ato_ready = critical_failed == 0 and score_pct >= 70.0

    by_framework: dict[str, dict] = {}
    for r in results:
        fw = r["framework"]
        if fw not in by_framework:
            by_framework[fw] = {"passed": 0, "total": 0}
        by_framework[fw]["total"] += 1
        if r["status"] == "pass":
            by_framework[fw]["passed"] += 1

    return {
        "items": results,
        "summary": {
            "total": total,
            "passed": passed_count,
            "failed": total - passed_count,
            "critical_failed": critical_failed,
            "score_pct": score_pct,
            "ato_ready": ato_ready,
        },
        "by_framework": by_framework,
    }
