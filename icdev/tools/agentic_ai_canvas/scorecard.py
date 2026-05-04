# CUI // SP-CTI
"""Agentic AI Design Canvas — Unified Design Scorecard (Phase 9).

Aggregates 8 analysis dimensions into a single design health score:
  1. Assessment (NIST AI RMF + OWASP LLM) — 25%
  2. ATO Readiness — 20%
  3. Regulatory Compliance — 15%
  4. Red Team Resilience (inverse of unmitigated fraction) — 15%
  5. Lint Score (design quality) — 10%
  6. Structural Resilience (impact analyzer) — 10%
  7. Risk Posture (penalized by open critical risks) — 5%

Overall health: HEALTHY (≥80) / AT_RISK (≥60) / DEGRADED (≥40) / CRITICAL (<40)
"""

from __future__ import annotations

_WEIGHTS = {
    "assessment": 0.25,
    "ato": 0.20,
    "regulatory": 0.15,
    "red_team": 0.15,
    "lint": 0.10,
    "resilience": 0.10,
    "risk_posture": 0.05,
}

_DIMENSION_LABELS = {
    "assessment": "NIST AI RMF / OWASP Assessment",
    "ato": "ATO Readiness",
    "regulatory": "Regulatory Compliance",
    "red_team": "Adversarial Resilience",
    "lint": "Design Lint Quality",
    "resilience": "Structural Resilience",
    "risk_posture": "Risk Register Posture",
}


def build_scorecard(
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
    Build unified scorecard across all 8 AADC analysis dimensions.

    Returns:
        {
          dimensions: [{id, label, score, weight, status, details}],
          overall_score: float,
          health: str,
          design_id: str,
          design_name: str,
          safety_impacting: bool,
          rights_impacting: bool,
        }
    """
    dims = _compute_dimensions(assessment, ato_data, reg_data, red_team_data,
                               lint_data, impact_data, risk_items)

    weighted_sum = sum(
        d["score"] * _WEIGHTS.get(d["id"], 0) for d in dims
    )
    total_weight = sum(_WEIGHTS.values())
    overall = round(weighted_sum / total_weight, 1)
    health = _health_label(overall)

    return {
        "dimensions": dims,
        "overall_score": overall,
        "health": health,
        "design_id": design.get("id", ""),
        "design_name": design.get("name", "Unnamed"),
        "safety_impacting": bool(design.get("safety_impacting")),
        "rights_impacting": bool(design.get("rights_impacting")),
        "domain": design.get("domain", "unspecified"),
        "classification": design.get("classification", "CUI"),
    }


def _compute_dimensions(
    assessment: dict | None,
    ato_data: dict,
    reg_data: dict,
    red_team_data: dict,
    lint_data: dict,
    impact_data: dict,
    risk_items: list[dict],
) -> list[dict]:
    dims = []

    # 1. Assessment
    if assessment:
        score = float(assessment.get("score", 0))
        dims.append(_dim("assessment", score, f"Score {score}%"))
    else:
        dims.append(_dim("assessment", 0, "No assessment run yet", "missing"))

    # 2. ATO Readiness
    s = ato_data.get("summary", {})
    ato_score = float(s.get("score", 0))
    ato_ready = s.get("ato_ready", False)
    dims.append(_dim("ato", ato_score,
                     f"{'ATO ready' if ato_ready else 'Not ATO ready'} — {s.get('passed',0)}/{s.get('total',0)} items passed"))

    # 3. Regulatory Compliance
    reg_sum = reg_data.get("summary", {})
    reg_score = float(reg_sum.get("compliance_rate", 0))
    gaps = reg_sum.get("total_gaps", 0)
    dims.append(_dim("regulatory", reg_score, f"{gaps} gap(s) identified"))

    # 4. Red Team Resilience
    rt_sum = red_team_data.get("summary", {})
    applicable = rt_sum.get("applicable", 0)
    unmitigated = rt_sum.get("unmitigated", 0)
    if applicable > 0:
        mitigated_frac = (applicable - unmitigated) / applicable
        rt_score = round(mitigated_frac * 100, 1)
    else:
        rt_score = 100.0
    dims.append(_dim("red_team", rt_score,
                     f"{unmitigated} unmitigated / {applicable} applicable scenarios · risk: {rt_sum.get('overall_risk','UNKNOWN')}"))

    # 5. Lint Score
    lint_score = float(lint_data.get("summary", {}).get("lint_score",
                       lint_data.get("lint_score", 100)))
    total_issues = lint_data.get("summary", {}).get("total", lint_data.get("total_issues", 0))
    dims.append(_dim("lint", lint_score, f"{total_issues} issue(s)"))

    # 6. Structural Resilience
    impact_sum = impact_data.get("summary", {})
    res_score = float(impact_sum.get("resilience_score", 100))
    spofs = len(impact_sum.get("spofs", []))
    dims.append(_dim("resilience", res_score,
                     f"{spofs} SPOF(s) · risk: {impact_sum.get('overall_risk_level','LOW')}"))

    # 7. Risk Posture
    open_critical = sum(1 for r in risk_items
                        if r.get("severity") == "CRITICAL" and r.get("status") == "open")
    open_high = sum(1 for r in risk_items
                    if r.get("severity") == "HIGH" and r.get("status") == "open")
    risk_penalty = min(100, open_critical * 15 + open_high * 5)
    risk_score = max(0, 100 - risk_penalty)
    dims.append(_dim("risk_posture", risk_score,
                     f"{open_critical} critical open · {open_high} high open"))

    return dims


def _dim(dim_id: str, score: float, detail: str, status: str = "") -> dict:
    if not status:
        if score >= 80:
            status = "green"
        elif score >= 60:
            status = "amber"
        elif score > 0:
            status = "red"
        else:
            status = "missing"
    return {
        "id": dim_id,
        "label": _DIMENSION_LABELS.get(dim_id, dim_id),
        "score": round(score, 1),
        "weight_pct": round(_WEIGHTS.get(dim_id, 0) * 100),
        "status": status,
        "detail": detail,
    }


def _health_label(score: float) -> str:
    if score >= 80:
        return "HEALTHY"
    if score >= 60:
        return "AT_RISK"
    if score >= 40:
        return "DEGRADED"
    return "CRITICAL"
