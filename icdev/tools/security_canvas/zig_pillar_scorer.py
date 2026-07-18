# [CUI // SP-CTI]
"""ZIG Pillar Scorer — per-pillar maturity score (0.0–1.0).

Bridges existing zta_maturity_scorer.py output to ZIG capability/activity completion.
Score = weighted average of:
  - Activity completion rate (60% weight)
  - Capability implementation rate (40% weight)
"""

from tools.security_canvas.db.init_db import get_connection
from tools.security_canvas.constants import ZIG_PILLARS, ZIG_MATURITY_LEVELS


def _maturity_level_for_score(score: float) -> str:
    # Pick the highest band whose floor the score clears. Using only score_min
    # (sorted descending) avoids gaps between adjacent bands' max/min boundaries
    # (e.g. intermediate max 0.74 vs advanced min 0.75 would otherwise drop a
    # score of 0.745 through to the default).
    for level in sorted(ZIG_MATURITY_LEVELS, key=lambda lvl: lvl["score_min"], reverse=True):
        if score >= level["score_min"]:
            return level["slug"]
    return "preparation"


def score_pillar(pillar_slug: str, target_id: str = "icdev-self") -> dict:
    """Compute maturity score for a single ZIG pillar for one assessment target.

    Activity completions are per-target (zig_activity_completions.target_id), so
    the 60% activity-completion weight is scoped to ``target_id``. Capability
    implementation status is a platform-wide concept (zig_capabilities has no
    target dimension), so the 40% capability weight is shared across targets.
    Two targets with different completion progress therefore produce independent
    scores.

    Returns:
        {slug, score, maturity_level, capability_count, implemented_capabilities,
         activity_count, complete_activities, in_progress_activities}
    """
    conn = get_connection()
    try:
        # Capabilities for this pillar
        caps = conn.execute(
            "SELECT id, implementation_status FROM zig_capabilities WHERE pillar_slug=?",
            (pillar_slug,),
        ).fetchall()

        cap_count = len(caps)
        implemented_caps = sum(1 for c in caps if c["implementation_status"] == "implemented")
        in_progress_caps = sum(1 for c in caps if c["implementation_status"] == "in_progress")

        # Activities for this pillar's capabilities
        cap_ids = [c["id"] for c in caps]
        if not cap_ids:
            return {
                "slug": pillar_slug, "score": 0.0, "maturity_level": "preparation",
                "capability_count": 0, "implemented_capabilities": 0,
                "activity_count": 0, "complete_activities": 0, "in_progress_activities": 0,
            }

        placeholders = ",".join("?" * len(cap_ids))
        acts = conn.execute(
            f"SELECT a.id FROM zig_activities a WHERE a.capability_id IN ({placeholders})",
            cap_ids,
        ).fetchall()
        act_ids = [a["id"] for a in acts]
        act_count = len(act_ids)

        complete_acts = 0
        in_progress_acts = 0
        if act_ids:
            comp_placeholders = ",".join("?" * len(act_ids))
            completions = conn.execute(
                f"SELECT status FROM zig_activity_completions "
                f"WHERE activity_id IN ({comp_placeholders}) AND target_id=?",
                act_ids + [target_id],
            ).fetchall()
            complete_acts = sum(1 for c in completions if c["status"] == "complete")
            in_progress_acts = sum(1 for c in completions if c["status"] == "in_progress")

        # Score calculation
        # Partial credit for in_progress
        activity_rate_partial = (complete_acts + 0.5 * in_progress_acts) / act_count if act_count > 0 else 0.0
        cap_rate = (implemented_caps + 0.5 * in_progress_caps) / cap_count if cap_count > 0 else 0.0

        score = round(0.6 * activity_rate_partial + 0.4 * cap_rate, 4)
        maturity_level = _maturity_level_for_score(score)

        return {
            "slug": pillar_slug,
            "score": score,
            "maturity_level": maturity_level,
            "capability_count": cap_count,
            "implemented_capabilities": implemented_caps,
            "activity_count": act_count,
            "complete_activities": complete_acts,
            "in_progress_activities": in_progress_acts,
        }
    finally:
        conn.close()


def score_all_pillars(target_id: str = "icdev-self") -> list:
    """Score all 7 ZIG pillars for one target. Returns list of pillar score dicts."""
    results = []
    for p in ZIG_PILLARS:
        score_data = score_pillar(p["slug"], target_id=target_id)
        score_data["name"] = p["name"]
        score_data["full_name"] = p.get("full_name", p["name"])
        score_data["color"] = p.get("color", "#6366f1")
        score_data["icon"] = p.get("icon", "shield")
        score_data["pillar_weight"] = p.get("pillar_weight", 0.143)
        results.append(score_data)
    return results


def aggregate_zig_score(pillar_scores: list) -> dict:
    """Compute weighted aggregate ZIG score from pillar scores."""
    total_weight = sum(p["pillar_weight"] for p in pillar_scores)
    if total_weight == 0:
        return {"score": 0.0, "maturity_level": "preparation"}

    weighted_score = sum(p["score"] * p["pillar_weight"] for p in pillar_scores) / total_weight
    weighted_score = round(weighted_score, 4)
    maturity_level = _maturity_level_for_score(weighted_score)

    return {
        "score": weighted_score,
        "maturity_level": maturity_level,
        "fy2027_readiness_pct": min(100, round(weighted_score / 0.50 * 100)),
    }
