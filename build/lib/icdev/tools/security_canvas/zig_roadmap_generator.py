# [CUI // SP-CTI]
"""ZIG Roadmap Generator — FY2027/FY2032 milestone timeline JSON.

Generates a structured roadmap showing which activities target which phase,
estimated completion dates, and progress toward FY2027 (Target) and
FY2032 (Advanced) compliance deadlines.
"""

from tools.security_canvas.constants import ZIG_PILLARS, ZIG_PHASES, ZIG_FY2027_DEADLINE, ZIG_FY2032_DEADLINE
from tools.security_canvas.db.init_db import get_connection
from tools.security_canvas.zig_phase_tracker import get_all_phases_status


def generate_roadmap() -> dict:
    """Generate ZIG compliance roadmap JSON.

    Returns milestone structure suitable for Chart.js timeline rendering.
    """
    conn = get_connection()
    try:
        phases_status = get_all_phases_status()

        # Build per-pillar summary
        pillar_summaries = []
        for p in ZIG_PILLARS:
            caps = conn.execute(
                "SELECT id, title, phase, maturity_level, implementation_status "
                "FROM zig_capabilities WHERE pillar_slug=? ORDER BY phase",
                (p["slug"],),
            ).fetchall()
            by_phase = {"discovery": [], "phase1": [], "phase2": []}
            for c in caps:
                by_phase[c["phase"]].append({
                    "id": c["id"],
                    "title": c["title"],
                    "status": c["implementation_status"],
                    "maturity_level": c["maturity_level"],
                })
            pillar_summaries.append({
                "slug": p["slug"],
                "name": p["name"],
                "color": p.get("color", "#6366f1"),
                "capabilities_by_phase": by_phase,
                "total_caps": len(caps),
                "implemented": sum(1 for c in caps if c["implementation_status"] == "implemented"),
            })

        # Timeline milestones
        milestones = [
            {
                "id": "m-discovery",
                "label": "Discovery Complete",
                "phase": "discovery",
                "target_date": "2025-03-31",
                "description": "All 14 discovery activities complete; DAAS inventory established.",
                "activities": phases_status[0]["total"] if phases_status else 14,
                "complete": phases_status[0]["complete"] if phases_status else 0,
            },
            {
                "id": "m-phase1",
                "label": "Phase 1 Complete",
                "phase": "phase1",
                "target_date": "2026-03-31",
                "description": "36 Phase 1 activities complete; foundational ZT controls deployed.",
                "activities": phases_status[1]["total"] if len(phases_status) > 1 else 36,
                "complete": phases_status[1]["complete"] if len(phases_status) > 1 else 0,
            },
            {
                "id": "m-phase2",
                "label": "Phase 2 Complete (FY2027 Target)",
                "phase": "phase2",
                "target_date": ZIG_FY2027_DEADLINE,
                "description": "41 Phase 2 activities complete; FY2027 Target-level maturity achieved.",
                "activities": phases_status[2]["total"] if len(phases_status) > 2 else 41,
                "complete": phases_status[2]["complete"] if len(phases_status) > 2 else 0,
            },
            {
                "id": "m-advanced",
                "label": "Advanced Maturity (FY2032)",
                "phase": "advanced",
                "target_date": ZIG_FY2032_DEADLINE,
                "description": "Phase 3 & 4 complete; AI/ML-driven ZT; 45 total capabilities.",
                "activities": 61,
                "complete": 0,
                "note": "Phase 3 & 4 not yet published by NSA.",
            },
        ]

        # Overall progress
        total_target_activities = sum(ps["total"] for ps in phases_status)
        total_complete = sum(ps["complete"] for ps in phases_status)
        overall_pct = round(total_complete / total_target_activities * 100) if total_target_activities > 0 else 0

        return {
            "milestones": milestones,
            "pillar_summaries": pillar_summaries,
            "fy2027_deadline": ZIG_FY2027_DEADLINE,
            "fy2032_deadline": ZIG_FY2032_DEADLINE,
            "overall_pct_complete": overall_pct,
            "total_target_activities": total_target_activities,
            "total_complete_activities": total_complete,
        }
    finally:
        conn.close()
