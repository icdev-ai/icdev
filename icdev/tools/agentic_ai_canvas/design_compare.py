# CUI // SP-CTI
"""Agentic AI Design Canvas — Design Comparison Engine (Phase 6).

Compares two AADC designs: node delta, score comparison,
assessment finding comparison, risk register comparison.
"""

from __future__ import annotations

import json


def compare_designs(
    design_a: dict,
    design_b: dict,
    assessment_a: dict | None,
    assessment_b: dict | None,
    risks_a: list[dict],
    risks_b: list[dict],
) -> dict:
    """
    Compare two AADC designs.

    Returns dict with keys: node_delta, score_delta, risk_delta,
    autonomy_delta, type_counts, verdict.
    """
    graph_a = design_a.get("graph_json", {})
    graph_b = design_b.get("graph_json", {})
    if isinstance(graph_a, str):
        graph_a = json.loads(graph_a)
    if isinstance(graph_b, str):
        graph_b = json.loads(graph_b)

    nodes_a = {n["id"]: n for n in graph_a.get("nodes", [])}
    nodes_b = {n["id"]: n for n in graph_b.get("nodes", [])}

    types_a: dict[str, int] = {}
    types_b: dict[str, int] = {}
    for n in nodes_a.values():
        t = n.get("type", "unknown")
        types_a[t] = types_a.get(t, 0) + 1
    for n in nodes_b.values():
        t = n.get("type", "unknown")
        types_b[t] = types_b.get(t, 0) + 1

    ids_a = set(nodes_a)
    ids_b = set(nodes_b)
    shared = ids_a & ids_b
    added_to_b = ids_b - ids_a
    removed_from_b = ids_a - ids_b
    changed_labels = [
        nid for nid in shared
        if nodes_a[nid].get("label") != nodes_b[nid].get("label")
    ]

    score_a = assessment_a.get("score", 0) if assessment_a else 0
    score_b = assessment_b.get("score", 0) if assessment_b else 0
    nist_a = assessment_a.get("nist_rmf_score", 0) if assessment_a else 0
    nist_b = assessment_b.get("nist_rmf_score", 0) if assessment_b else 0
    owasp_a = assessment_a.get("owasp_score", 0) if assessment_a else 0
    owasp_b = assessment_b.get("owasp_score", 0) if assessment_b else 0

    winner = (
        design_a["name"] if score_a > score_b
        else design_b["name"] if score_b > score_a
        else "Tie"
    )

    open_a = sum(1 for r in risks_a if r.get("status") == "open")
    open_b = sum(1 for r in risks_b if r.get("status") == "open")
    crit_a = sum(1 for r in risks_a if r.get("status") == "open" and r.get("severity") == "CRITICAL")
    crit_b = sum(1 for r in risks_b if r.get("status") == "open" and r.get("severity") == "CRITICAL")

    auto_a = assessment_a.get("autonomy_max", 0) if assessment_a else 0
    auto_b = assessment_b.get("autonomy_max", 0) if assessment_b else 0

    if score_a == score_b == 0:
        verdict = "Neither design has been assessed yet."
    elif score_a > score_b:
        verdict = f"{design_a['name']} scores higher ({score_a:.0f}% vs {score_b:.0f}%)."
    elif score_b > score_a:
        verdict = f"{design_b['name']} scores higher ({score_b:.0f}% vs {score_a:.0f}%)."
    else:
        verdict = f"Both designs score equally at {score_a:.0f}%."

    return {
        "node_delta": {
            "added_to_b": [nodes_b[i].get("label", i) for i in sorted(added_to_b)],
            "removed_from_b": [nodes_a[i].get("label", i) for i in sorted(removed_from_b)],
            "shared_count": len(shared),
            "changed_labels_count": len(changed_labels),
            "count_a": len(ids_a),
            "count_b": len(ids_b),
        },
        "score_delta": {
            "overall_a": round(score_a, 1),
            "overall_b": round(score_b, 1),
            "overall_diff": round(score_b - score_a, 1),
            "nist_a": round(nist_a, 1),
            "nist_b": round(nist_b, 1),
            "nist_diff": round(nist_b - nist_a, 1),
            "owasp_a": round(owasp_a, 1),
            "owasp_b": round(owasp_b, 1),
            "owasp_diff": round(owasp_b - owasp_a, 1),
            "winner": winner,
        },
        "risk_delta": {
            "open_a": open_a, "open_b": open_b,
            "critical_a": crit_a, "critical_b": crit_b,
            "total_a": len(risks_a), "total_b": len(risks_b),
        },
        "autonomy_delta": {
            "a_max": auto_a, "b_max": auto_b,
            "diff": auto_b - auto_a,
        },
        "type_counts": {"a": types_a, "b": types_b},
        "verdict": verdict,
    }
