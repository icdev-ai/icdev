# [CUI // SP-CTI]
"""ZIG Assessor — main entry point for ZIG gap assessment.

run_zig_assessment() scores all 7 pillars, identifies gaps, and persists
results to zig_maturity_scores. Bridges to existing zta_maturity_scorer.py
when available.
"""

from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger
from tools.security_canvas.db.init_db import get_connection
from tools.security_canvas.zig_pillar_scorer import score_all_pillars, aggregate_zig_score
from tools.security_canvas.zig_phase_tracker import get_all_phases_status, compute_fy2027_readiness

logger = get_logger("icdev.security_canvas.zig_assessor")


def _try_zta_bridge(pillar_slug: str) -> float:
    """Try to pull existing ZTA maturity score for the pillar as a signal.

    Degrades to None (pure ZIG score) when the ZTA scorer is unavailable or
    errors — the bridge is an enrichment signal and must never break a ZIG
    assessment. Two failure modes are distinguished: an ImportError means the
    accessor symbol is missing (dead-bridge regression — logged at debug), while
    any error from the accessor itself is logged as a warning so real breakage
    surfaces instead of being silently swallowed.
    """
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
    except ImportError:
        logger.debug(
            "ZTA bridge unavailable: tools.devsecops.zta_maturity_scorer."
            "get_latest_score not importable; using pure ZIG score for pillar '%s'",
            pillar_slug,
        )
        return None

    zta_key = zta_map.get(pillar_slug)
    if not zta_key:
        return None
    try:
        score_data = get_latest_score()
    except Exception:
        logger.warning(
            "ZTA bridge accessor get_latest_score() failed for pillar '%s'; "
            "degrading to pure ZIG score",
            pillar_slug,
            exc_info=True,
        )
        return None
    if score_data and zta_key in score_data.get("pillar_scores", {}):
        return float(score_data["pillar_scores"][zta_key])
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
    """Persist pillar scores to zig_maturity_scores (one row per pillar per run).

    Rows are tagged with ``target_id`` so per-target assessment history stays
    separable — zig_portfolio._latest_scores_for_target reads back by target.
    """
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    try:
        for ps in pillar_scores:
            conn.execute(
                "INSERT INTO zig_maturity_scores "
                "(pillar_slug, target_id, score, maturity_level, capability_count, "
                "activity_count, complete_activities, assessment_run_at, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (ps["slug"], target_id, ps["score"], ps["maturity_level"],
                 ps.get("capability_count", 0), ps.get("activity_count", 0),
                 ps.get("complete_activities", 0), now, now),
            )
        conn.commit()
    finally:
        conn.close()


def reconcile_capability_status() -> int:
    """Promote/demote capability implementation_status from activity completion.

    A capability whose activities are mostly complete IS implemented; one with
    partial progress is in_progress. This keeps the 40% capability weight in the
    pillar score consistent with the 60% activity weight instead of letting them
    drift. Returns the number of capabilities whose status changed.

    Thresholds (effective completion = complete + 0.5*in_progress, per activity):
        >= 0.70  -> implemented
        >= 0.30  -> in_progress
        else     -> unchanged (preserves manual not_started/planned)
    """
    conn = get_connection()
    changed = 0
    try:
        caps = conn.execute(
            "SELECT id, implementation_status FROM zig_capabilities"
        ).fetchall()
        for cap in caps:
            acts = conn.execute(
                "SELECT id FROM zig_activities WHERE capability_id=?", (cap["id"],)
            ).fetchall()
            if not acts:
                continue
            act_ids = [a["id"] for a in acts]
            placeholders = ",".join("?" * len(act_ids))
            comps = conn.execute(
                f"SELECT status FROM zig_activity_completions WHERE activity_id IN ({placeholders})",
                act_ids,
            ).fetchall()
            complete = sum(1 for c in comps if c["status"] == "complete")
            in_prog = sum(1 for c in comps if c["status"] == "in_progress")
            rate = (complete + 0.5 * in_prog) / len(act_ids)
            new_status = cap["implementation_status"]
            if rate >= 0.70:
                new_status = "implemented"
            elif rate >= 0.30:
                new_status = "in_progress"
            if new_status != cap["implementation_status"]:
                conn.execute(
                    "UPDATE zig_capabilities SET implementation_status=? WHERE id=?",
                    (new_status, cap["id"]),
                )
                changed += 1
        conn.commit()
    finally:
        conn.close()
    return changed


def run_zig_assessment(target_id: str = "icdev-self") -> dict:
    """Run full ZIG assessment across all 7 pillars for one target.

    ``target_id`` scopes the activity-completion signal and the persisted
    zig_maturity_scores rows; capability reconciliation stays platform-wide.
    Defaults to 'icdev-self' so the global /api/zig/assess route and the seven
    pillar orchestrators keep their existing no-arg call semantics.

    Returns:
        {
            pillar_scores: [...],
            aggregate: {score, maturity_level, fy2027_readiness_pct},
            gaps: [...],
            phases: [...],
            fy2027: {...},
            assessed_at: ISO timestamp,
        }
    """
    # Keep capability status in sync with activity completion before scoring
    reconcile_capability_status()

    pillar_scores = score_all_pillars(target_id=target_id)

    # Attempt ZTA bridge enrichment for pillars with no activity data yet
    for ps in pillar_scores:
        if ps["score"] == 0.0:
            zta_score = _try_zta_bridge(ps["slug"])
            if zta_score is not None:
                ps["score"] = round(zta_score * 0.5, 4)  # ZTA score as 50% signal

    aggregate = aggregate_zig_score(pillar_scores)
    gaps = _identify_gaps(pillar_scores)
    phases = get_all_phases_status()
    fy2027 = compute_fy2027_readiness(phases)

    _persist_scores(pillar_scores, target_id=target_id)

    return {
        "pillar_scores": pillar_scores,
        "aggregate": aggregate,
        "gaps": gaps,
        "phases": phases,
        "fy2027": fy2027,
        "target_id": target_id,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "top_gaps": gaps[:10],
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
