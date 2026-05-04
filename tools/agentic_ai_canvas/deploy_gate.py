# CUI // SP-CTI
"""Agentic AI Design Canvas — Deployment Gate (Phase 9).

Generates a deployment readiness verdict for CI/CD pipeline integration:
  APPROVED  — all checks pass
  CONDITIONAL — soft warnings (no hard blockers)
  BLOCKED  — one or more hard blockers

Hard blockers:
  - Any CRITICAL unmitigated red team scenario
  - ATO readiness score < 40
  - Lint score < 40
  - Structural resilience CRITICAL
  - Assessment score < 40

Soft warnings:
  - Any HIGH unmitigated red team scenario
  - ATO score < 70
  - Regulatory compliance rate < 60
  - Lint score < 70
  - Assessment score < 70
  - Any open CRITICAL risk item
"""

from __future__ import annotations

import yaml


_GATE_CHECKS: list[dict] = [
    # id, label, blocker_threshold, warn_threshold, source_key, higher_is_better
    {"id": "assessment",    "label": "NIST AI RMF / OWASP Assessment Score",  "block": 40, "warn": 70, "higher": True},
    {"id": "ato",           "label": "ATO Readiness Score",                    "block": 40, "warn": 70, "higher": True},
    {"id": "regulatory",    "label": "Regulatory Compliance Rate",             "block": 0,  "warn": 60, "higher": True},
    {"id": "lint",          "label": "Design Lint Score",                      "block": 40, "warn": 70, "higher": True},
    {"id": "resilience",    "label": "Structural Resilience Score",            "block": 0,  "warn": 60, "higher": True},
]


def run_deploy_gate(
    design: dict,
    assessment: dict | None,
    ato_data: dict,
    reg_data: dict,
    red_team_data: dict,
    lint_data: dict,
    impact_data: dict,
    risk_items: list[dict],
) -> dict:
    """
    Evaluate deployment readiness across all gate checks.

    Returns:
        {
          verdict: str,          # APPROVED / CONDITIONAL / BLOCKED
          blockers: list[dict],
          warnings: list[dict],
          gate_checks: list[dict],
          gate_yaml: str,        # downloadable YAML string
          design_id: str,
        }
    """
    scores = _extract_scores(assessment, ato_data, reg_data, red_team_data,
                              lint_data, impact_data, risk_items)
    blockers: list[dict] = []
    warnings: list[dict] = []
    gate_checks: list[dict] = []

    for check in _GATE_CHECKS:
        cid = check["id"]
        val = scores.get(cid, 100.0)
        if val <= check["block"] and check["block"] > 0:
            status = "blocked"
            blockers.append({"check": check["label"], "value": val, "threshold": check["block"]})
        elif val < check["warn"] and check["warn"] > 0:
            status = "warning"
            warnings.append({"check": check["label"], "value": val, "threshold": check["warn"]})
        else:
            status = "pass"
        gate_checks.append({"id": cid, "label": check["label"], "score": val, "status": status})

    # Special red team checks
    rt_sum = red_team_data.get("summary", {})
    crit_unmitigated = rt_sum.get("critical_unmitigated", 0)
    high_unmitigated = sum(
        1 for s in red_team_data.get("scenarios", [])
        if not s.get("mitigated") and s.get("exploitability", 0) >= 7
    )
    if crit_unmitigated > 0:
        blockers.append({"check": f"Red Team: {crit_unmitigated} CRITICAL unmitigated scenario(s)",
                         "value": 0, "threshold": 0})
        gate_checks.append({"id": "rt_critical", "label": "Red Team CRITICAL scenarios",
                             "score": 0, "status": "blocked"})
    elif high_unmitigated > 0:
        warnings.append({"check": f"Red Team: {high_unmitigated} HIGH unmitigated scenario(s)",
                         "value": 0, "threshold": 0})
        gate_checks.append({"id": "rt_high", "label": "Red Team HIGH scenarios",
                             "score": 0, "status": "warning"})
    else:
        gate_checks.append({"id": "rt_critical", "label": "Red Team CRITICAL/HIGH scenarios",
                             "score": 100, "status": "pass"})

    # Open critical risk items
    open_crit = sum(1 for r in risk_items
                    if r.get("severity") == "CRITICAL" and r.get("status") == "open")
    if open_crit > 0:
        warnings.append({"check": f"Risk Register: {open_crit} open CRITICAL risk(s)",
                         "value": open_crit, "threshold": 0})

    if blockers:
        verdict = "BLOCKED"
    elif warnings:
        verdict = "CONDITIONAL"
    else:
        verdict = "APPROVED"

    design_id = design.get("id", "unknown")
    gate_yaml = _build_gate_yaml(design_id, design.get("name", ""), verdict,
                                 blockers, warnings, gate_checks)

    return {
        "verdict": verdict,
        "blockers": blockers,
        "warnings": warnings,
        "gate_checks": gate_checks,
        "gate_yaml": gate_yaml,
        "design_id": design_id,
        "design_name": design.get("name", "Unnamed"),
    }


def _extract_scores(
    assessment: dict | None,
    ato_data: dict,
    reg_data: dict,
    red_team_data: dict,
    lint_data: dict,
    impact_data: dict,
    risk_items: list[dict],
) -> dict:
    return {
        "assessment": float(assessment.get("score", 0)) if assessment else 0.0,
        "ato": float(ato_data.get("summary", {}).get("score", 0)),
        "regulatory": float(reg_data.get("summary", {}).get("compliance_rate", 0)),
        "lint": float(
            lint_data.get("summary", {}).get("lint_score",
            lint_data.get("lint_score", 100))
        ),
        "resilience": float(impact_data.get("summary", {}).get("resilience_score", 100)),
    }


def _build_gate_yaml(
    design_id: str,
    design_name: str,
    verdict: str,
    blockers: list[dict],
    warnings: list[dict],
    gate_checks: list[dict],
) -> str:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    data = {
        "aadc_deploy_gate": {
            "design_id": design_id,
            "design_name": design_name,
            "verdict": verdict,
            "timestamp": now,
            "blockers": blockers,
            "warnings": warnings,
            "gate_checks": [
                {"id": c["id"], "label": c["label"],
                 "score": c.get("score"), "status": c["status"]}
                for c in gate_checks
            ],
        }
    }
    return yaml.dump(data, default_flow_style=False, allow_unicode=True)
