# CUI // SP-CTI
"""Agentic AI Design Canvas — Executive Summary Report Generator (Phase 6).

Produces a structured C-suite/CISO brief combining assessment scores,
ATO readiness, regulatory compliance, top risks and threats.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone


def generate_exec_summary(
    design: dict,
    assessment: dict | None,
    risks: list[dict],
    threat_model: dict | None,
    ato_result: dict | None,
    reg_result: dict | None,
) -> dict:
    """
    Generate executive summary data for CISO/C-suite brief.

    Returns dict with posture_rating, scores, top_risks, top_threats,
    key_strengths, critical_gaps, recommended_actions.
    """
    now = datetime.now(timezone.utc).isoformat()
    overall = assessment.get("score", 0) if assessment else 0
    nist = assessment.get("nist_rmf_score", 0) if assessment else 0
    owasp = assessment.get("owasp_score", 0) if assessment else 0
    autonomy = assessment.get("autonomy_max", 0) if assessment else 0
    ato_score = ato_result["summary"]["score_pct"] if ato_result else 0
    reg_score = reg_result["summary"]["score_pct"] if reg_result else 0

    combined = (overall * 0.5 + ato_score * 0.3 + reg_score * 0.2) if assessment else 0

    if combined >= 85:
        posture_rating, posture_color = "EXCELLENT", "#4ade80"
    elif combined >= 70:
        posture_rating, posture_color = "GOOD", "#86efac"
    elif combined >= 50:
        posture_rating, posture_color = "FAIR", "#fbbf24"
    elif combined > 0:
        posture_rating, posture_color = "POOR", "#f87171"
    else:
        posture_rating, posture_color = "UNRATED", "#94a3b8"

    sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

    open_risks = sorted(
        [r for r in risks if r.get("status") == "open"],
        key=lambda r: sev_order.get(r.get("severity", "LOW"), 3),
    )
    top_risks = [
        {"title": r.get("title", ""), "severity": r.get("severity", ""), "owner": r.get("owner") or "—"}
        for r in open_risks[:3]
    ]

    top_threats = []
    if threat_model:
        stride = threat_model.get("stride") or []
        atlas = threat_model.get("atlas_threats") or []
        if isinstance(stride, str):
            stride = json.loads(stride)
        if isinstance(atlas, str):
            atlas = json.loads(atlas)
        all_threats = stride + atlas
        sev_sorted = sorted(all_threats, key=lambda t: sev_order.get(t.get("severity", "LOW"), 3))
        top_threats = [
            {
                "title": t.get("title", ""),
                "severity": t.get("severity", ""),
                "category": t.get("category") or t.get("tactic", ""),
            }
            for t in sev_sorted[:3]
        ]

    strengths: list[str] = []
    if assessment:
        findings = assessment.get("findings", [])
        if isinstance(findings, str):
            findings = json.loads(findings)
        for f in findings:
            if f.get("status") == "pass":
                label = f.get("label") or f.get("check", "")
                if label:
                    strengths.append(label)
                if len(strengths) >= 4:
                    break

    critical_gaps: list[str] = []
    if ato_result:
        for item in ato_result.get("items", []):
            if item["status"] == "fail" and item["severity"] in ("CRITICAL", "HIGH"):
                critical_gaps.append(f"[{item['framework']}] {item['title']}")
    if reg_result:
        for gap in reg_result.get("gaps", []):
            if gap["status"] == "gap" and gap["severity"] == "CRITICAL":
                entry = f"[{gap['framework']}] {gap['title']}"
                if entry not in critical_gaps:
                    critical_gaps.append(entry)

    actions: list[str] = []
    if ato_result:
        for item in ato_result.get("items", []):
            if item["status"] == "fail" and item["severity"] == "CRITICAL" and item.get("remediation"):
                if item["remediation"] not in actions:
                    actions.append(item["remediation"])
    if reg_result:
        for gap in reg_result.get("gaps", []):
            if gap["status"] == "gap" and gap["severity"] == "CRITICAL" and gap.get("gap_advice"):
                if gap["gap_advice"] not in actions:
                    actions.append(gap["gap_advice"])
    if len(actions) < 3 and assessment:
        findings = assessment.get("findings", [])
        if isinstance(findings, str):
            findings = json.loads(findings)
        for f in findings:
            if f.get("status") == "fail" and f.get("severity") in ("CRITICAL", "HIGH"):
                hint = f.get("remediation") or f.get("detail") or ""
                if hint and hint not in actions:
                    actions.append(hint)
                    if len(actions) >= 5:
                        break

    return {
        "posture_rating": posture_rating,
        "posture_color": posture_color,
        "combined_score": round(combined, 1),
        "overall_score": round(overall, 1),
        "nist_score": round(nist, 1),
        "owasp_score": round(owasp, 1),
        "ato_score": round(ato_score, 1),
        "reg_score": round(reg_score, 1),
        "top_risks": top_risks,
        "top_threats": top_threats,
        "key_strengths": strengths[:4],
        "critical_gaps": critical_gaps[:6],
        "recommended_actions": actions[:5],
        "autonomy_level": autonomy,
        "classification": design.get("classification", "CUI"),
        "generated_at": now,
        "design_name": design.get("name", ""),
        "design_domain": design.get("domain", ""),
    }
