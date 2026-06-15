# [CUI // SP-CTI]
"""ZIG Assessor — main entry point for ZIG gap assessment.

run_zig_assessment() scores all 7 pillars, identifies gaps, and persists
results to zig_maturity_scores. Bridges to existing zta_maturity_scorer.py
when available.
"""

from datetime import datetime, timezone

from tools.security_canvas.db.init_db import get_connection
from tools.security_canvas.zig_pillar_scorer import score_all_pillars, aggregate_zig_score
from tools.security_canvas.zig_phase_tracker import get_all_phases_status, compute_fy2027_readiness


def _try_zta_bridge(pillar_slug: str) -> float:
    """Try to pull existing ZTA maturity score for the pillar as a signal."""
    zta_map = {
        "user": "user_identity",
        "device": "device",
        "network": "network",
        "application": "application_workload",
        "data": "data",
        "visibility": "visibility_analytics",
        "automation": "automation_orchestration",
    }
    try:
        from tools.devsecops.zta_maturity_scorer import get_latest_score
        zta_key = zta_map.get(pillar_slug)
        if zta_key:
            score_data = get_latest_score()
            if score_data and zta_key in score_data.get("pillar_scores", {}):
                return float(score_data["pillar_scores"][zta_key])
    except Exception:
        pass
    return None


def _identify_gaps(pillar_scores: list) -> list:
    """Identify capability gaps (not_started or planned) ordered by priority."""
    conn = get_connection()
    try:
        gaps = []
        for ps in pillar_scores:
            caps = conn.execute(
                "SELECT id, title, phase, maturity_level, implementation_status, description "
                "FROM zig_capabilities WHERE pillar_slug=? AND implementation_status IN ('not_started','planned')"
                "ORDER BY CASE phase WHEN 'discovery' THEN 1 WHEN 'phase1' THEN 2 ELSE 3 END",
                (ps["slug"],),
            ).fetchall()
            for c in caps:
                gaps.append({
                    "capability_id": c["id"],
                    "pillar_slug": ps["slug"],
                    "pillar_name": ps["name"],
                    "title": c["title"],
                    "phase": c["phase"],
                    "maturity_level": c["maturity_level"],
                    "status": c["implementation_status"],
                    "description": c["description"],
                    "priority": 1 if c["phase"] == "discovery" else (2 if c["phase"] == "phase1" else 3),
                })
        gaps.sort(key=lambda g: (g["priority"], g["pillar_slug"]))
        return gaps
    finally:
        conn.close()


def _persist_scores(pillar_scores: list, target_id: str = "icdev-self"):
    """Persist pillar scores to zig_maturity_scores (one row per pillar per run)."""
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    try:
        for ps in pillar_scores:
            conn.execute(
                "INSERT INTO zig_maturity_scores "
                "(pillar_slug, target_id, score, maturity_level, capability_count, activity_count, "
                "complete_activities, assessment_run_at, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (ps["slug"], target_id, ps["score"], ps["maturity_level"],
                 ps.get("capability_count", 0), ps.get("activity_count", 0),
                 ps.get("complete_activities", 0), now, now),
            )
        conn.commit()
    finally:
        conn.close()


def run_zig_assessment(target_id: str = "icdev-self") -> dict:
    """Run full ZIG assessment across all 7 pillars for a target.

    Args:
        target_id: Assessment target (default: 'icdev-self' for the platform itself).

    Returns:
        {
            pillar_scores: [...],
            aggregate: {score, maturity_level, fy2027_readiness_pct},
            gaps: [...],
            phases: [...],
            fy2027: {...},
            assessed_at: ISO timestamp,
            target_id: str,
        }
    """
    pillar_scores = score_all_pillars(target_id=target_id)

    # Attempt ZTA bridge enrichment only for icdev-self (has legacy ZTA data)
    if target_id == "icdev-self":
        for ps in pillar_scores:
            if ps["score"] == 0.0:
                zta_score = _try_zta_bridge(ps["slug"])
                if zta_score is not None:
                    ps["score"] = round(zta_score * 0.5, 4)  # ZTA score as 50% signal

    aggregate = aggregate_zig_score(pillar_scores)
    gaps = _identify_gaps(pillar_scores)
    phases = get_all_phases_status(target_id=target_id)
    fy2027 = compute_fy2027_readiness(phases)

    _persist_scores(pillar_scores, target_id=target_id)

    return {
        "pillar_scores": pillar_scores,
        "aggregate": aggregate,
        "gaps": gaps,
        "phases": phases,
        "fy2027": fy2027,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "top_gaps": gaps[:10],
        "target_id": target_id,
    }


def get_latest_zig_maturity() -> dict:
    """Get most recent maturity scores without running a full assessment."""
    from tools.security_canvas.constants import ZIG_PILLARS
    _weight_map = {p["slug"]: p.get("pillar_weight", 0.143) for p in ZIG_PILLARS}
    _name_map = {p["slug"]: p["name"] for p in ZIG_PILLARS}

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT pillar_slug, score, maturity_level, assessment_run_at "
            "FROM zig_maturity_scores "
            "ORDER BY created_at DESC LIMIT 7"
        ).fetchall()
        if not rows:
            return None
        pillar_scores = []
        for r in rows:
            d = dict(r)
            slug = d.get("pillar_slug", "")
            d["pillar_weight"] = _weight_map.get(slug, 0.143)
            d["name"] = _name_map.get(slug, slug)
            d["slug"] = slug
            pillar_scores.append(d)
        aggregate = aggregate_zig_score(pillar_scores)
        return {"pillar_scores": pillar_scores, "aggregate": aggregate}
    finally:
        conn.close()
