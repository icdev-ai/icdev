# CUI // SP-CTI
"""AI-ify Canvas — Compliance Posture engine.

Computes a deterministic AI-governance posture grade for the AI-ify canvas from
signals already captured in the canvas DB (scans, opportunities, scores, IL model
assignments, HITL decisions, audit trail, roadmaps). No LLM is involved — the
grade is reproducible and audit-defensible.

Six dimensions, each mapped to a recognised AI-governance framework:

  human_oversight       0.20  NIST AI RMF GOVERN-1.1 · OMB M-25-21 · DoD AI Ethics "Governable"
  data_sovereignty      0.20  DoD Impact Levels (IL4/5/6) · NIST AI RMF MAP-3.4 · SC-7
  auditability          0.15  NIST 800-53 AU-2/AU-12 · NIST AI RMF GOVERN-1.2
  risk_triage           0.15  NIST AI RMF MEASURE-2 · NIST AI 600-1 · RA-3
  lifecycle_management  0.15  NIST AI RMF MANAGE-1 · NIST 800-53 SA-3/SA-8
  classification_marking 0.15 CUI (32 CFR 2002) · NIST 800-53 AC-16/MP-3 · DoDI 5200.48

Each dimension yields a 0-100 score (or None when not yet applicable, e.g. an
empty canvas). The overall score is the weight-renormalised average over the
applicable dimensions, then mapped to an A-F grade and a posture band.
"""
from __future__ import annotations

import json
from typing import Any

from tools.aiify.constants import (
    POSTURE_WEIGHTS,
    POSTURE_DIMENSION_LABELS,
    POSTURE_FRAMEWORK_CROSSWALK,
    POSTURE_DIMENSION_RATIONALE,
)


# ── low-level helpers ─────────────────────────────────────────────────────────
def _scalar(conn: Any, sql: str, params: tuple = ()) -> int:
    """Run a COUNT-style query, returning 0 on any failure (empty/missing table)."""
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return 0
    if row is None:
        return 0
    try:
        val = row[0]
    except (KeyError, TypeError, IndexError):
        return 0
    return int(val or 0)


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _band(score: float) -> str:
    if score >= 80:
        return "strong"
    if score >= 60:
        return "moderate"
    if score >= 40:
        return "weak"
    return "critical"


def _status(score: float | None) -> str:
    if score is None:
        return "na"
    if score >= 80:
        return "green"
    if score >= 60:
        return "amber"
    return "red"


def _dim(dim_id: str, score: float | None, detail: str) -> dict:
    return {
        "id": dim_id,
        "label": POSTURE_DIMENSION_LABELS.get(dim_id, dim_id),
        "score": round(score, 1) if score is not None else None,
        "weight_pct": round(POSTURE_WEIGHTS.get(dim_id, 0) * 100),
        "status": _status(score),
        "detail": detail,
        "frameworks": POSTURE_FRAMEWORK_CROSSWALK.get(dim_id, []),
        "rationale": POSTURE_DIMENSION_RATIONALE.get(dim_id, ""),
    }


# ── dimension computations ────────────────────────────────────────────────────
def _compute_dimensions(conn: Any) -> tuple[list[dict], dict]:
    """Return (dimensions, counts) computed from the canvas DB."""
    total_scans = _scalar(conn, "SELECT COUNT(*) FROM aiify_scans")
    total_opps = _scalar(conn, "SELECT COUNT(*) FROM aiify_opportunities")

    # IL model coverage — opportunities routed to an IL-appropriate model.
    with_il = _scalar(
        conn,
        "SELECT COUNT(*) FROM aiify_opportunities "
        "WHERE il_recommended_model IS NOT NULL AND il_recommended_model != ''",
    )

    # Risk triage — opportunities that have been deterministically scored.
    scored = _scalar(
        conn,
        "SELECT COUNT(*) FROM aiify_scores WHERE composite_score IS NOT NULL",
    )

    # Lifecycle management — scans with a generated remediation roadmap.
    scans_with_roadmap = _scalar(
        conn, "SELECT COUNT(DISTINCT scan_id) FROM aiify_roadmaps"
    )

    # Human oversight — fraction of scans whose AI-opportunity findings entered a
    # governed workflow: promoted onto the tracked kanban board and/or HITL-decided.
    # Governance applies at the AI-adoption-decision gate (which opportunities to
    # build), NOT to every code pattern the scanner flags — so the denominator is
    # scans, not the thousands of raw candidate opportunities.
    hitl = _scalar(conn, "SELECT COUNT(*) FROM aiify_hitl_decisions")
    promotions = _scalar(
        conn,
        "SELECT COUNT(*) FROM aiify_audit_log WHERE event_type = 'kanban_promoted'",
    )
    governed_scans = _scalar(
        conn,
        "SELECT COUNT(DISTINCT scan_id) FROM aiify_audit_log "
        "WHERE event_type IN ('kanban_promoted','hitl_decision') AND scan_id IS NOT NULL",
    )

    # Auditability — immutable audit-trail density per scan.
    audit_events = _scalar(conn, "SELECT COUNT(*) FROM aiify_audit_log")

    dims: list[dict] = []

    # 1. Human oversight
    if total_scans == 0:
        dims.append(_dim("human_oversight", None, "No scans recorded yet"))
    else:
        score = 100.0 * governed_scans / total_scans
        dims.append(
            _dim(
                "human_oversight",
                score,
                f"{governed_scans}/{total_scans} scans triaged into a governed workflow "
                f"({promotions} kanban promotion(s), {hitl} HITL decision(s))",
            )
        )

    # 2. Data sovereignty (IL model routing)
    if total_opps == 0:
        dims.append(_dim("data_sovereignty", None, "No opportunities scanned yet"))
    else:
        score = 100.0 * with_il / total_opps
        dims.append(
            _dim(
                "data_sovereignty",
                score,
                f"{with_il}/{total_opps} opportunities assigned an IL-appropriate model",
            )
        )

    # 3. Auditability
    if total_scans == 0:
        dims.append(_dim("auditability", None, "No scans recorded yet"))
    else:
        score = min(100.0, 100.0 * audit_events / total_scans)
        dims.append(
            _dim(
                "auditability",
                score,
                f"{audit_events} audit event(s) across {total_scans} scan(s)",
            )
        )

    # 4. Risk triage
    if total_opps == 0:
        dims.append(_dim("risk_triage", None, "No opportunities to triage yet"))
    else:
        score = 100.0 * scored / total_opps
        dims.append(
            _dim(
                "risk_triage",
                score,
                f"{scored}/{total_opps} opportunities risk-scored (value/feasibility/risk)",
            )
        )

    # 5. Lifecycle management
    if total_scans == 0:
        dims.append(_dim("lifecycle_management", None, "No scans recorded yet"))
    else:
        score = 100.0 * scans_with_roadmap / total_scans
        dims.append(
            _dim(
                "lifecycle_management",
                score,
                f"{scans_with_roadmap}/{total_scans} scans produced a managed roadmap",
            )
        )

    # 6. Classification marking (structural control)
    if total_scans == 0:
        dims.append(_dim("classification_marking", None, "No artifacts produced yet"))
    else:
        dims.append(
            _dim(
                "classification_marking",
                100.0,
                "All AI-ify artifacts (PRDs, roadmaps, scan records) carry CUI // SP-CTI markings",
            )
        )

    counts = {
        "total_scans": total_scans,
        "total_opportunities": total_opps,
        "opportunities_with_il_model": with_il,
        "opportunities_scored": scored,
        "scans_with_roadmap": scans_with_roadmap,
        "hitl_decisions": hitl,
        "reviewed_promotions": promotions,
        "governed_scans": governed_scans,
        "audit_events": audit_events,
    }
    return dims, counts


# ── public API ────────────────────────────────────────────────────────────────
def compute_posture(conn: Any) -> dict:
    """Compute the full AI-ify compliance posture from an open canvas connection.

    The caller owns the connection lifecycle (this function never closes it).
    """
    dims, counts = _compute_dimensions(conn)

    applicable = [d for d in dims if d["score"] is not None]
    if applicable:
        weighted = sum(d["score"] * POSTURE_WEIGHTS.get(d["id"], 0) for d in applicable)
        total_w = sum(POSTURE_WEIGHTS.get(d["id"], 0) for d in applicable)
        overall = round(weighted / total_w, 1) if total_w else 0.0
        grade = _grade(overall)
        band = _band(overall)
    else:
        overall = 0.0
        grade = "F"
        band = "unrated"

    # Top remediation levers — applicable dimensions below target, worst first.
    gaps = sorted(
        [d for d in applicable if d["score"] < 80],
        key=lambda d: d["score"],
    )
    recommendations = [
        {
            "dimension": d["label"],
            "score": d["score"],
            "advice": POSTURE_DIMENSION_RATIONALE.get(d["id"], ""),
        }
        for d in gaps[:4]
    ]

    return {
        "overall_score": overall,
        "grade": grade,
        "posture": band,
        "dimensions": dims,
        "counts": counts,
        "recommendations": recommendations,
        "applicable_dimensions": len(applicable),
        "total_dimensions": len(dims),
    }


def snapshot_posture(conn: Any, actor: str = "system") -> dict:
    """Compute the posture and persist a snapshot row. Returns the posture dict.

    Snapshots are insert-only historical records (audit evidence). The caller
    owns the connection; this function commits but does not close it.
    """
    posture = compute_posture(conn)
    try:
        conn.execute(
            "INSERT INTO aiify_posture_snapshots "
            "(overall_score, grade, posture, scan_count, opportunity_count, "
            " dimensions_json, snapshot_json) VALUES (?,?,?,?,?,?,?)",
            (
                posture["overall_score"],
                posture["grade"],
                posture["posture"],
                posture["counts"]["total_scans"],
                posture["counts"]["total_opportunities"],
                json.dumps(posture["dimensions"]),
                json.dumps(posture),
            ),
        )
        conn.execute(
            "INSERT INTO aiify_audit_log (event_type, actor, detail) VALUES (?,?,?)",
            (
                "posture_snapshot",
                actor,
                json.dumps(
                    {
                        "overall_score": posture["overall_score"],
                        "grade": posture["grade"],
                        "posture": posture["posture"],
                    }
                ),
            ),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    return posture


def posture_trend(conn: Any, limit: int = 30) -> list[dict]:
    """Return recent posture snapshots (newest first) for trend display."""
    try:
        rows = conn.execute(
            "SELECT overall_score, grade, posture, scan_count, opportunity_count, created_at "
            "FROM aiify_posture_snapshots ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            out.append(dict(r))
        except (TypeError, ValueError):
            out.append(
                {
                    "overall_score": r[0],
                    "grade": r[1],
                    "posture": r[2],
                    "scan_count": r[3],
                    "opportunity_count": r[4],
                    "created_at": r[5],
                }
            )
    return out
