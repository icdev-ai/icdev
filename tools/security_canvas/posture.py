# [TEMPLATE: CUI // SP-CTI]
"""ICDEV™ Security Design Canvas — posture-summary aggregation (shx-hyg-02).

Behavior-preserving extraction of the ``/security/api/posture-summary`` route
handler out of ``blueprint.py``. The Flask route is now a thin wrapper:

    with get_connection() as conn:
        return jsonify(compute_posture_summary(conn))

``compute_posture_summary(conn)`` performs the same aggregation the inline
handler did, in the same order, with the same SQL, using a single connection for
all reads (equivalent to the original per-block connections since these are all
reads). Focused internal helpers cover each aggregation block.

IMPORTANT — pinned known behavior (see tests/test_sc_posture_summary.py):
the per-design "latest assessment" query intentionally selects only
``risk_score, posture_grade, ran_at`` (NOT ``findings_json``), so the per-design
``cat1_count`` lookup raises and is swallowed, leaving every design's
``cat1_count`` at 0. This is preserved verbatim.
"""
from __future__ import annotations

import json


def _count_cat1(findings_json) -> int:
    """Count CAT1-severity findings in a findings_json blob. Never raises."""
    try:
        findings = json.loads(findings_json or "[]")
        return sum(1 for f in findings if f.get("severity") == "CAT1")
    except Exception:
        return 0


def _grade_for_score(avg_score: float) -> str:
    """Map an average risk score to a letter grade (A/B/C/D/F)."""
    if avg_score >= 90:
        return "A"
    if avg_score >= 80:
        return "B"
    if avg_score >= 70:
        return "C"
    if avg_score >= 60:
        return "D"
    return "F"


def _overall_posture(avg_score: float) -> str:
    """Bucket an average risk score into an overall posture label."""
    if avg_score >= 80:
        return "strong"
    if avg_score >= 60:
        return "moderate"
    if avg_score >= 40:
        return "weak"
    return "critical"


def _compute_design_rollup(conn):
    """Per-design latest-assessment roll-up.

    Returns ``(design_list, total_score, assessed_count, grade_dist)``.

    Note: mirrors the original exactly — the latest-assessment SELECT omits
    ``findings_json``, so the per-design ``cat1_count`` access raises and is
    swallowed, leaving every entry's ``cat1_count`` at 0.
    """
    designs = [
        dict(r)
        for r in conn.execute("SELECT id, name FROM security_designs ORDER BY name").fetchall()
    ]

    design_list = []
    total_score = 0.0
    assessed_count = 0
    grade_dist = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}

    for d in designs:
        latest = conn.execute(
            "SELECT risk_score, posture_grade, ran_at "
            "FROM sc_assessments WHERE design_id=%s "
            "ORDER BY ran_at DESC LIMIT 1",
            (d["id"],),
        ).fetchone()

        entry = {
            "id": d["id"],
            "name": d["name"] or d["id"],
            "risk_score": None,
            "grade": None,
            "last_assessed": None,
            "cat1_count": 0,
        }

        if latest:
            entry["risk_score"] = latest["risk_score"]
            entry["grade"] = latest["posture_grade"]
            entry["last_assessed"] = latest["ran_at"]
            total_score += latest["risk_score"] or 0.0
            assessed_count += 1
            g = latest["posture_grade"] or "F"
            if g in grade_dist:
                grade_dist[g] += 1
            # Count CAT1 findings from the latest assessment
            try:
                findings = json.loads(latest["findings_json"] or "[]")
                entry["cat1_count"] = sum(1 for f in findings if f.get("severity") == "CAT1")
            except Exception:
                pass

        design_list.append(entry)

    return design_list, total_score, assessed_count, grade_dist


def _compute_pipeline_assessments(conn):
    """Pipeline-level assessments (design_id IS NULL, trigger_source='pdc_save').

    Deduplicated by pipeline id, newest first. Returns
    ``(pipeline_assessments, pipeline_cat1_total)``.
    """
    pipeline_rows = conn.execute(
        "SELECT source_entity_id, findings_json, ran_at "
        "FROM sc_assessments WHERE design_id IS NULL "
        "AND trigger_source='pdc_save' "
        "ORDER BY ran_at DESC"
    ).fetchall()

    seen_pipelines: set = set()
    pipeline_assessments = []
    pipeline_cat1_total = 0
    for pr in pipeline_rows:
        pid = pr["source_entity_id"] or pr[0]
        if pid in seen_pipelines:
            continue
        seen_pipelines.add(pid)
        cat1 = _count_cat1(pr["findings_json"])
        pipeline_cat1_total += cat1
        pipeline_assessments.append(
            {
                "pipeline_id": pid,
                "cat1_count": cat1,
                "last_assessed": pr["ran_at"],
            }
        )

    return pipeline_assessments, pipeline_cat1_total


def _compute_ndc_assessments(conn):
    """NDC-triggered design assessments (trigger_source='ndc_save').

    Deduplicated by design_id, newest first.
    """
    ndc_rows = conn.execute(
        "SELECT design_id, source_entity_id, risk_score, posture_grade, "
        "findings_json, ran_at "
        "FROM sc_assessments WHERE trigger_source='ndc_save' "
        "ORDER BY ran_at DESC"
    ).fetchall()

    seen_ndc: set = set()
    ndc_assessments = []
    for nr in ndc_rows:
        did = nr["design_id"] or nr[0]
        if did in seen_ndc:
            continue
        seen_ndc.add(did)
        cat1 = _count_cat1(nr["findings_json"])
        ndc_assessments.append(
            {
                "topology_id": nr["source_entity_id"],
                "design_id": did,
                "risk_score": nr["risk_score"],
                "posture_grade": nr["posture_grade"],
                "cat1_count": cat1,
                "last_assessed": nr["ran_at"],
            }
        )

    return ndc_assessments


def compute_posture_summary(conn) -> dict:
    """Aggregate security posture across all designs.

    Pure aggregation over ``conn`` — performs no writes. Returns the JSON-ready
    dict the ``/security/api/posture-summary`` route serializes verbatim.
    """
    design_list, total_score, assessed_count, grade_dist = _compute_design_rollup(conn)

    total_designs = len(design_list)
    unassessed = total_designs - assessed_count
    avg_score = round(total_score / assessed_count, 1) if assessed_count else 0.0

    avg_grade = _grade_for_score(avg_score)
    overall_posture = _overall_posture(avg_score)

    # total_cat1 starts from the per-design roll-up (always 0 today; see note),
    # then accrues pipeline CAT1 findings — matching the original ordering.
    total_cat1 = sum(e["cat1_count"] for e in design_list)

    pipeline_assessments, pipeline_cat1_total = _compute_pipeline_assessments(conn)
    total_cat1 += pipeline_cat1_total

    ndc_assessments = _compute_ndc_assessments(conn)

    return {
        "total_designs": total_designs,
        "assessed_designs": assessed_count,
        "unassessed_designs": unassessed,
        "average_risk_score": avg_score,
        "average_grade": avg_grade,
        "grade_distribution": grade_dist,
        "designs": design_list,
        "overall_posture": overall_posture,
        "total_cat1_findings": total_cat1,
        "pipeline_assessments": pipeline_assessments,
        "ndc_assessments": ndc_assessments,
    }
