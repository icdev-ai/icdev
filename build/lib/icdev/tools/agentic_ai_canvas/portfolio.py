# CUI // SP-CTI
"""Agentic AI Design Canvas — Portfolio Dashboard aggregator (Phase 5).

Aggregates compliance metrics, risk summaries, and assessment scores across
all designs in the AADC canvas for portfolio-level reporting.
"""

from __future__ import annotations


def aggregate_portfolio(designs: list[dict], assessments: list[dict], risk_items: list[dict]) -> dict:
    """
    Compute portfolio-level metrics from all designs + their latest assessments.

    Args:
        designs: list of design dicts (from aadc_designs)
        assessments: list of latest assessment dicts per design (from aadc_assessments)
        risk_items: all risk items across designs

    Returns:
        dict with portfolio metrics for dashboard rendering
    """
    assess_by_design = {a["design_id"]: a for a in assessments}

    total_designs = len(designs)
    scored_designs = [d for d in designs if d["id"] in assess_by_design]

    scores = [assess_by_design[d["id"]]["score"] for d in scored_designs]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    top_score = max(scores, default=0.0)
    low_score = min(scores, default=0.0)

    # Compliance band distribution
    bands = {"green": 0, "amber": 0, "red": 0, "unscored": 0}
    for d in designs:
        a = assess_by_design.get(d["id"])
        if not a:
            bands["unscored"] += 1
        elif a["score"] >= 70:
            bands["green"] += 1
        elif a["score"] >= 50:
            bands["amber"] += 1
        else:
            bands["red"] += 1

    # Risk register summary across all designs
    open_risks = [r for r in risk_items if r.get("status") == "open"]
    critical_open = [r for r in open_risks if r.get("severity") == "CRITICAL"]
    high_open = [r for r in open_risks if r.get("severity") == "HIGH"]

    # Autonomy level distribution
    autonomy_dist: dict[int, int] = {}
    for d in scored_designs:
        level = assess_by_design[d["id"]].get("autonomy_max", 0)
        autonomy_dist[level] = autonomy_dist.get(level, 0) + 1

    # Safety / rights impacting counts
    safety_count = sum(1 for d in scored_designs if assess_by_design[d["id"]].get("safety_impacting"))
    rights_count = sum(1 for d in scored_designs if assess_by_design[d["id"]].get("rights_impacting"))

    # Top 5 designs by score (for heatmap)
    design_scores = sorted(
        [{"id": d["id"], "name": d["name"], "score": assess_by_design[d["id"]]["score"],
          "domain": d.get("domain", ""), "classification": d.get("classification", "CUI")}
         for d in scored_designs],
        key=lambda x: x["score"], reverse=True,
    )

    return {
        "total_designs": total_designs,
        "scored_designs": len(scored_designs),
        "avg_score": avg_score,
        "top_score": top_score,
        "low_score": low_score,
        "compliance_bands": bands,
        "open_risks": len(open_risks),
        "critical_open_risks": len(critical_open),
        "high_open_risks": len(high_open),
        "autonomy_distribution": autonomy_dist,
        "safety_impacting_count": safety_count,
        "rights_impacting_count": rights_count,
        "design_scores": design_scores,
        "portfolio_health": _portfolio_health(avg_score, len(critical_open), bands),
    }


def _portfolio_health(avg_score: float, critical_risks: int, bands: dict) -> str:
    if critical_risks > 0 or bands.get("red", 0) > 0:
        return "AT_RISK"
    if avg_score >= 70 and bands.get("amber", 0) == 0:
        return "COMPLIANT"
    if avg_score >= 50:
        return "IMPROVING"
    return "NON_COMPLIANT"
