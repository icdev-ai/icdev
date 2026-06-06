# [CUI // SP-CTI]
"""ZIG Phase Tracker — phase completion metrics and FY2027 readiness scoring."""

from tools.security_canvas.db.init_db import get_connection
from tools.security_canvas.zig_activity_tracker import get_phase_completions
from tools.security_canvas.constants import ZIG_PHASES


def get_all_phases_status() -> list:
    """Return completion metrics for all 3 ZIG phases."""
    results = []
    for phase_def in ZIG_PHASES:
        slug = phase_def["slug"]
        completion = get_phase_completions(slug)
        results.append({
            **phase_def,
            **completion,
            "pct_complete": completion["pct_complete"],
        })
    return results


def compute_fy2027_readiness(phase_statuses: list = None) -> dict:
    """Estimate FY2027 Target-level readiness (0–100%).

    FY2027 requires completing Discovery + Phase 1 + Phase 2 (91 total activities).
    Readiness = (complete activities in all phases) / 91 * 100
    """
    if phase_statuses is None:
        phase_statuses = get_all_phases_status()

    total_target = sum(p["total"] for p in phase_statuses)
    total_complete = sum(p["complete"] for p in phase_statuses)
    total_in_progress = sum(p["in_progress"] for p in phase_statuses)

    pct = round(total_complete / total_target * 100) if total_target > 0 else 0
    pct_partial = round((total_complete + 0.5 * total_in_progress) / total_target * 100) if total_target > 0 else 0

    return {
        "fy2027_target_activities": total_target,
        "complete_activities": total_complete,
        "in_progress_activities": total_in_progress,
        "pct_complete": pct,
        "pct_partial": pct_partial,
        "on_track": pct >= 25,
    }


def get_capability_status_by_pillar(pillar_slug: str) -> list:
    """Return all capabilities for a pillar with their implementation status."""
    conn = get_connection()
    try:
        caps = conn.execute(
            "SELECT id, title, phase, maturity_level, implementation_status, description, nist_controls "
            "FROM zig_capabilities WHERE pillar_slug=? ORDER BY phase, maturity_level",
            (pillar_slug,),
        ).fetchall()
        result = []
        for c in caps:
            cap = dict(c)
            # Count activities
            acts = conn.execute(
                "SELECT a.id, ac.status FROM zig_activities a "
                "LEFT JOIN zig_activity_completions ac ON a.id=ac.activity_id "
                "WHERE a.capability_id=?",
                (c["id"],),
            ).fetchall()
            cap["activity_count"] = len(acts)
            cap["complete_activities"] = sum(1 for a in acts if a["status"] == "complete")
            cap["in_progress_activities"] = sum(1 for a in acts if a["status"] == "in_progress")
            result.append(cap)
        return result
    finally:
        conn.close()
