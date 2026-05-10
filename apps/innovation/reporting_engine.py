# CUI // SP-CTI
"""FORGE IGNITE reporting engine — aggregations for the executive dashboard."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from tools.db.storage import get_connection
from .db import pipeline_stats, list_ideas

logger = logging.getLogger("icdev.innovation.reporting")

_COMP_CFG_PATH = Path(__file__).resolve().parent.parent.parent / "args" / "academy_competencies.yaml"


def _load_competency_cfg() -> dict:
    try:
        with open(_COMP_CFG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Org AI Readiness Score (used by both innovation dashboard and academy page)
# ---------------------------------------------------------------------------

def compute_org_readiness() -> dict:
    """Compute the composite Org AI Readiness Score (0-100).

    Returns:
        {
          "score": float,
          "tier": str,         # "red" | "yellow" | "green"
          "tier_color": str,
          "guidance": str,
          "phase_guidance": str,
          "components": {dim: {"value": float, "weight": float, "label": str}},
          "cohort": {"total_users": int, "l1": int, "l2": int, "l3": int, "l4": int},
          "skill_gaps": [{"skill": str, "unlock_pct": float}],
        }
    """
    cfg = _load_competency_cfg()
    comp_weights = cfg.get("org_readiness", {}).get("components", {})
    thresholds = cfg.get("org_readiness", {}).get("thresholds", {"red": 40, "yellow": 65, "green": 80})

    components = {}
    cohort = {"total_users": 0, "l1": 0, "l2": 0, "l3": 0, "l4": 0, "l5": 0}
    skill_gaps = []

    try:
        with get_connection() as conn:
            # Cohort stats
            total = conn.execute("SELECT COUNT(*) FROM fa_users").fetchone()[0]
            cohort["total_users"] = total

            if total > 0:
                for level, xp_min, xp_max in [
                    ("l1", 0, 499), ("l2", 500, 1999),
                    ("l3", 2000, 4999), ("l4", 5000, 9999), ("l5", 10000, 999999),
                ]:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM fa_users WHERE xp>=? AND xp<=?",
                        (xp_min, xp_max),
                    ).fetchone()
                    cohort[level] = row[0] if row else 0

            # Component 1: Tier 2 completion %
            t2_pct = 0.0
            if total > 0:
                try:
                    row = conn.execute(
                        """SELECT COUNT(DISTINCT mp.user_id) FROM fa_mission_progress mp
                           JOIN fa_missions m ON m.id=mp.mission_id
                           WHERE m.tier=2 AND mp.status='complete'"""
                    ).fetchone()
                    t2_users = row[0] if row else 0
                    t2_pct = (t2_users / total) * 100
                except Exception:
                    pass
            components["tier2_completion_pct"] = {
                "value": round(t2_pct, 1),
                "weight": comp_weights.get("tier2_completion_pct", {}).get("weight", 0.35),
                "label": "Tier 2 Mission Completion",
            }

            # Component 2: AADC readiness score average
            aadc_avg = 0.0
            try:
                row = conn.execute(
                    "SELECT AVG(current_score) FROM aadc_design_scores"
                ).fetchone()
                aadc_avg = float(row[0] or 0)
            except Exception:
                pass
            components["aadc_readiness_score"] = {
                "value": round(aadc_avg, 1),
                "weight": comp_weights.get("aadc_readiness_score", {}).get("weight", 0.25),
                "label": "AADC Design Readiness",
            }

            # Component 3: Production AI ops coverage (SRE-AI missions)
            prod_ops_pct = 0.0
            if total > 0:
                try:
                    row = conn.execute(
                        """SELECT COUNT(DISTINCT mp.user_id) FROM fa_mission_progress mp
                           JOIN fa_missions m ON m.id=mp.mission_id
                           WHERE m.topic='sre_ai' AND mp.status='complete'"""
                    ).fetchone()
                    sre_users = row[0] if row else 0
                    prod_ops_pct = (sre_users / total) * 100
                except Exception:
                    pass
            components["production_ai_ops_coverage"] = {
                "value": round(prod_ops_pct, 1),
                "weight": comp_weights.get("production_ai_ops_coverage", {}).get("weight", 0.20),
                "label": "Production AI Ops Coverage",
            }

            # Component 4: Leadership track completion
            leadership_pct = 0.0
            try:
                total_leaders = conn.execute(
                    "SELECT COUNT(*) FROM fa_users WHERE role='leadership'"
                ).fetchone()[0]
                if total_leaders > 0:
                    row = conn.execute(
                        """SELECT COUNT(DISTINCT mp.user_id) FROM fa_mission_progress mp
                           JOIN fa_missions m ON m.id=mp.mission_id
                           JOIN fa_users u ON u.id=mp.user_id
                           WHERE u.role='leadership' AND m.topic='leadership' AND mp.status='complete'"""
                    ).fetchone()
                    leadership_pct = (row[0] / total_leaders) * 100
            except Exception:
                pass
            components["leadership_track_completion"] = {
                "value": round(leadership_pct, 1),
                "weight": comp_weights.get("leadership_track_completion", {}).get("weight", 0.20),
                "label": "Leadership Track Completion",
            }

            # Skill gaps: skills with <30% unlock rate across cohort
            try:
                rows = conn.execute(
                    """SELECT sn.slug, sn.title,
                              COUNT(us.user_id) * 100.0 / NULLIF(?,0) as unlock_pct
                       FROM fa_skill_nodes sn
                       LEFT JOIN fa_user_skills us ON us.skill_id=sn.id
                       GROUP BY sn.id
                       HAVING unlock_pct < 30 OR unlock_pct IS NULL
                       ORDER BY unlock_pct ASC LIMIT 8""",
                    (total,),
                ).fetchall()
                skill_gaps = [{"skill": r[1], "slug": r[0], "unlock_pct": round(r[2] or 0, 1)}
                              for r in rows]
            except Exception:
                skill_gaps = []

    except Exception as e:
        logger.debug("Org readiness query failed: %s", e)

    # Composite score
    score = sum(
        comp["value"] * comp["weight"]
        for comp in components.values()
    )
    score = round(min(100.0, score), 1)

    # Tier
    if score >= thresholds.get("green", 80):
        tier, tier_color = "green", "#00FF88"
    elif score >= thresholds.get("yellow", 65):
        tier, tier_color = "yellow", "#FFB800"
    else:
        tier, tier_color = "red", "#FF4444"

    # Guidance
    phase_guidance_map = cfg.get("org_readiness", {}).get("modernization_phase_guidance", {})
    if score < 40:
        guidance = "Phase 0 work required — build AI foundations before pilots"
        phase_key = "below_40"
    elif score < 65:
        guidance = "Ready for Phase 1 quick-win AI projects"
        phase_key = "40_to_65"
    elif score < 80:
        guidance = "Ready for Phase 2 AI augmentation initiatives"
        phase_key = "65_to_80"
    else:
        guidance = "Ready for Phase 3+ AI-first transformation"
        phase_key = "above_80"

    phase_guidance = phase_guidance_map.get(phase_key, guidance)

    return {
        "score": score,
        "tier": tier,
        "tier_color": tier_color,
        "guidance": guidance,
        "phase_guidance": phase_guidance,
        "components": components,
        "cohort": cohort,
        "skill_gaps": skill_gaps,
    }


def innovation_dashboard_data() -> dict:
    """Aggregate all data for the executive innovation dashboard."""
    stats = pipeline_stats()
    readiness = compute_org_readiness()
    recent = list_ideas(limit=20)

    # Department activity breakdown
    dept_counts = {}
    for idea in list_ideas(limit=500):
        dept = idea.get("department") or "Unknown"
        dept_counts[dept] = dept_counts.get(dept, 0) + 1
    top_depts = sorted(dept_counts.items(), key=lambda x: x[1], reverse=True)[:6]

    # Phase distribution for pie chart
    phase_data = [
        {"label": "Phase 1 Quick Win", "value": stats["phase_counts"].get("phase1", 0), "color": "#00FF88"},
        {"label": "Phase 2 Augmentation", "value": stats["phase_counts"].get("phase2", 0), "color": "#FFB800"},
        {"label": "Phase 3 Transformation", "value": stats["phase_counts"].get("phase3", 0), "color": "#FF4444"},
        {"label": "Deferred", "value": stats["phase_counts"].get("deferred", 0), "color": "#555555"},
    ]

    # Pipeline funnel for bar chart
    funnel_data = [
        {"stage": s["label"], "count": stats["stage_counts"].get(s["id"], 0), "color": s["color"]}
        for s in [
            {"id": "spark", "label": "Spark", "color": "#FFB800"},
            {"id": "assess", "label": "Assess", "color": "#4A90E2"},
            {"id": "score", "label": "Scored", "color": "#00D4FF"},
            {"id": "pilot", "label": "Pilot", "color": "#00FF88"},
            {"id": "measure", "label": "Measure", "color": "#9b59b6"},
            {"id": "scale", "label": "Scaled", "color": "#FF6B35"},
        ]
    ]

    return {
        "stats": stats,
        "readiness": readiness,
        "recent": recent,
        "top_depts": top_depts,
        "phase_data": phase_data,
        "funnel_data": funnel_data,
    }
