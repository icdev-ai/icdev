# CUI // SP-CTI
"""Agentic AI Design Canvas — AI Risk Register (Phase 5).

Manages risk items linked to designs and assessment findings.
Risk items track: category, severity, likelihood, impact, status, mitigation.
"""

from __future__ import annotations

RISK_CATEGORIES = {
    "operational": "Operational Risk",
    "compliance": "Compliance Risk",
    "security": "Security Risk",
    "safety": "Safety Risk",
    "governance": "Governance Risk",
    "model": "Model Risk",
    "data": "Data Risk",
    "supply-chain": "Supply-Chain Risk",
}

SEVERITY_LEVELS = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
RISK_STATUSES = ("open", "mitigated", "accepted", "transferred", "closed")

# Autoconvert assessment findings to risk items
_FINDING_TO_CATEGORY = {
    "NIST": "compliance",
    "OWASP": "security",
    "ATLAS": "security",
    "SAFETY": "safety",
    "GOVERN": "governance",
    "Phase4": "operational",
}


def finding_to_risk(finding: dict) -> dict:
    """Convert an assessment finding dict to a draft risk item dict."""
    sev = finding.get("severity", "MEDIUM").upper()
    if sev not in SEVERITY_LEVELS:
        sev = "MEDIUM"

    framework = finding.get("framework", "")
    category = "operational"
    for key, cat in _FINDING_TO_CATEGORY.items():
        if key.lower() in framework.lower():
            category = cat
            break

    return {
        "title": finding.get("title", "Untitled Risk"),
        "description": finding.get("description", ""),
        "risk_category": category,
        "severity": sev,
        "likelihood": "MEDIUM",
        "impact": sev,
        "status": "open",
        "owner": "",
        "mitigation": finding.get("recommendation", ""),
        "finding_id": finding.get("id", ""),
        "node_id": finding.get("node_id", ""),
    }


def risk_score(severity: str, likelihood: str) -> int:
    """Return numeric risk score 1–25 from severity × likelihood matrix."""
    weights = {"CRITICAL": 5, "HIGH": 4, "MEDIUM": 3, "LOW": 2, "INFO": 1}
    s = weights.get(severity.upper(), 3)
    l = weights.get(likelihood.upper(), 3)
    return s * l


def summarize_register(risk_items: list[dict]) -> dict:
    """Return aggregate metrics for a list of risk items."""
    total = len(risk_items)
    by_severity: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for r in risk_items:
        sev = r.get("severity", "MEDIUM")
        st = r.get("status", "open")
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_status[st] = by_status.get(st, 0) + 1

    open_count = by_status.get("open", 0)
    critical_open = sum(
        1 for r in risk_items
        if r.get("severity") == "CRITICAL" and r.get("status") == "open"
    )
    return {
        "total": total,
        "open": open_count,
        "critical_open": critical_open,
        "by_severity": by_severity,
        "by_status": by_status,
        "residual_risk": "HIGH" if critical_open > 0 else ("MEDIUM" if open_count > 3 else "LOW"),
    }
