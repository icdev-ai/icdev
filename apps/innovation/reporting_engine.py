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
        with open(_COMP_CFG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Org AI Readiness Score (used by both innovation dashboard and academy page)
#
# aca-numbers-demo, measured live 2026-10-05: the page read "0.0/100 RED
# READINESS" and placed the only learner at "L4 — AI Expert" — a Recruit with no
# completed mission. Three defects:
#   * the cohort level bucketed fa_users.xp, the running total, of which 7385 of
#     7635 was daily-login attendance — while the learner's RANK is computed from
#     earned XP (fa_xp_ledger, is_attendance=0). Level and rank now share that basis.
#   * every completion query matched status='complete'; the academy writes
#     'completed' (MISSION_STATUS_COMPLETED), so no completion was ever counted.
#   * components with no population (no AADC scores, no leadership users, no
#     SRE-AI missions in the catalogue) scored 0 and dragged the composite to 0.0.
#     They are now reported as unmeasured, and with no completion evidence at all
#     the score is "insufficient data" instead of a RED verdict.
# ---------------------------------------------------------------------------

_DEFAULT_LEVELS = [
    {"id": "L1", "xp_min": 0, "xp_max": 499},
    {"id": "L2", "xp_min": 500, "xp_max": 1999},
    {"id": "L3", "xp_min": 2000, "xp_max": 4999},
    {"id": "L4", "xp_min": 5000, "xp_max": 9999},
    {"id": "L5", "xp_min": 10000, "xp_max": 999999},
]

_COMPLETED = "completed"  # apps.forge_academy.constants.MISSION_STATUS_COMPLETED


def competency_level(earned_xp: int, missions_completed: int,
                     levels: list[dict] | None = None) -> str:
    """Pure: the L-level ('l1'..'l5') a learner's EVIDENCE supports.

    Banded on earned XP — the same figure the rank badge uses, so "L4 — AI Expert"
    can only appear beside an Architect rank. A learner with no completed mission
    holds L1 regardless of XP: every level above it is gated on completed work
    (args/academy_competencies.yaml ``gate``), and XP alone is not that evidence.
    """
    levels = levels or _DEFAULT_LEVELS
    if missions_completed <= 0:
        return str(levels[0]["id"]).lower()
    chosen = levels[0]
    for lvl in levels:
        if int(earned_xp) >= int(lvl.get("xp_min", 0)):
            chosen = lvl
    return str(chosen["id"]).lower()


def composite_score(components: dict, evidence_count: int) -> dict:
    """Pure: weighted score over MEASURED components only.

    Returns ``{score, coverage_pct, insufficient_reason}``. ``score`` is None when
    there is nothing to score — no measurable component, or no completion/design
    evidence anywhere in the cohort — because 0.0 would assert "measured, and
    bad" about an organisation nothing has been measured about.
    """
    total_w = sum(float(c.get("weight") or 0) for c in components.values())
    measured = {k: c for k, c in components.items() if c.get("value") is not None}
    measured_w = sum(float(c.get("weight") or 0) for c in measured.values())
    coverage = round(measured_w / total_w * 100, 1) if total_w else 0.0
    if not measured or measured_w <= 0:
        return {"score": None, "coverage_pct": coverage,
                "insufficient_reason": "No readiness component has a population to measure yet."}
    if evidence_count <= 0:
        return {"score": None, "coverage_pct": coverage,
                "insufficient_reason": ("Insufficient data: no learner has completed a mission "
                                        "or scored an AADC design yet, so there is no evidence to score.")}
    score = sum(float(c["value"]) * float(c["weight"]) for c in measured.values()) / measured_w
    return {"score": round(min(100.0, score), 1), "coverage_pct": coverage,
            "insufficient_reason": None}


def _scalar(conn, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def compute_org_readiness() -> dict:
    """Compute the composite Org AI Readiness Score (0-100), or say it cannot.

    Returns:
        {
          "score": float | None,   # None = insufficient data
          "score_display": str,    # "42.5" or an em dash
          "tier": str,             # "red" | "yellow" | "green" | "insufficient"
          "tier_label": str,
          "tier_color": str,
          "guidance": str,
          "phase_guidance": str,
          "coverage_pct": float,   # % of component weight that was measurable
          "components": {dim: {"value": float|None, "weight": float, "label": str,
                               "note": str}},
          "cohort": {"total_users": int, "l1".."l5": int, "basis": str},
          "skill_gaps": [{"skill": str, "unlock_pct": float}],
        }
    """
    cfg = _load_competency_cfg()
    comp_cfg = cfg.get("org_readiness", {}).get("components", {})
    thresholds = cfg.get("org_readiness", {}).get("thresholds", {"red": 40, "yellow": 65, "green": 80})
    levels = cfg.get("levels") or _DEFAULT_LEVELS

    def _comp(key: str, default_w: float, label: str, value, note: str = "") -> dict:
        return {"value": None if value is None else round(float(value), 1),
                "weight": comp_cfg.get(key, {}).get("weight", default_w),
                "label": label, "note": note}

    components: dict = {}
    cohort = {"total_users": 0, "l1": 0, "l2": 0, "l3": 0, "l4": 0, "l5": 0,
              "basis": "earned XP from graded work, capped at L1 until a mission is completed; "
                       "daily-login attendance excluded"}
    skill_gaps: list = []
    evidence = 0

    try:
        from apps.forge_academy.db import TAKEABLE_MISSION_SQL, earned_xp_by_user
    except Exception:  # pragma: no cover - academy app absent
        TAKEABLE_MISSION_SQL, earned_xp_by_user = "1=1", None

    try:
        with get_connection() as conn:
            users = [dict(r) for r in conn.execute("SELECT id, xp, role FROM fa_users").fetchall()]
            total = len(users)
            cohort["total_users"] = total

            done_by_user: dict = {}
            for r in conn.execute(
                "SELECT mp.user_id, COUNT(DISTINCT mp.mission_id) FROM fa_mission_progress mp "
                "JOIN fa_missions m ON m.id=mp.mission_id "
                "WHERE mp.status=%s AND " + TAKEABLE_MISSION_SQL + " GROUP BY mp.user_id",
                (_COMPLETED,),
            ).fetchall():
                done_by_user[int(r[0])] = int(r[1])
            evidence += sum(done_by_user.values())

            try:
                if earned_xp_by_user is None:
                    raise RuntimeError("academy ledger helper unavailable")
                earned = earned_xp_by_user(conn, [u["id"] for u in users])
            except Exception:
                # Pre-ledger database: rank falls back to the total (blueprint._level_ctx);
                # the level does the same, and the completed-mission cap still applies.
                earned = {u["id"]: int(u.get("xp") or 0) for u in users}
            for u in users:
                lvl = competency_level(earned.get(u["id"], 0), done_by_user.get(u["id"], 0), levels)
                cohort[lvl] = cohort.get(lvl, 0) + 1

            # Component 1: share of the cohort with a completed Tier 2 mission.
            t2_value = None
            if total > 0:
                t2_users = _scalar(
                    conn,
                    "SELECT COUNT(DISTINCT mp.user_id) FROM fa_mission_progress mp "
                    "JOIN fa_missions m ON m.id=mp.mission_id "
                    "WHERE m.tier=2 AND mp.status=%s AND " + TAKEABLE_MISSION_SQL,
                    (_COMPLETED,)) or 0
                t2_value = t2_users / total * 100
            components["tier2_completion_pct"] = _comp(
                "tier2_completion_pct", 0.35, "Tier 2 Mission Completion", t2_value,
                "" if t2_value is not None else "No learners yet.")

            # Component 2: AADC design scores — only when designs have been scored.
            # Probed through the catalogue, not by a failing SELECT: on PostgreSQL
            # a failed statement aborts the transaction the queries below share.
            aadc_value, aadc_note = None, "No AADC designs have been scored."
            if _table_exists(conn, "aadc_design_scores"):
                row = conn.execute(
                    "SELECT COUNT(*), AVG(current_score) FROM aadc_design_scores").fetchone()
                if row and int(row[0] or 0) > 0:
                    aadc_value, aadc_note = float(row[1] or 0), f"{int(row[0])} scored designs."
                    evidence += int(row[0])
            else:
                aadc_note = "AADC scoring is not installed on this deployment."
            components["aadc_readiness_score"] = _comp(
                "aadc_readiness_score", 0.25, "AADC Design Readiness", aadc_value, aadc_note)

            # Component 3: SRE-AI production ops — measurable only if such missions exist.
            ops_value = None
            ops_note = "No Production AI Ops (sre_ai) missions are in the catalogue."
            sre_missions = _scalar(
                conn,
                "SELECT COUNT(*) FROM fa_missions m WHERE m.topic='sre_ai' AND "
                + TAKEABLE_MISSION_SQL) or 0
            if sre_missions and total > 0:
                sre_users = _scalar(
                    conn,
                    "SELECT COUNT(DISTINCT mp.user_id) FROM fa_mission_progress mp "
                    "JOIN fa_missions m ON m.id=mp.mission_id "
                    "WHERE m.topic='sre_ai' AND mp.status=%s", (_COMPLETED,)) or 0
                ops_value, ops_note = sre_users / total * 100, ""
            components["production_ai_ops_coverage"] = _comp(
                "production_ai_ops_coverage", 0.20, "Production AI Ops Coverage",
                ops_value, ops_note)

            # Component 4: leadership track — measurable only with leadership users.
            lead_value, lead_note = None, "No learners hold the leadership role."
            total_leaders = sum(1 for u in users if u.get("role") == "leadership")
            if total_leaders > 0:
                leaders_done = _scalar(
                    conn,
                    "SELECT COUNT(DISTINCT mp.user_id) FROM fa_mission_progress mp "
                    "JOIN fa_missions m ON m.id=mp.mission_id "
                    "JOIN fa_users u ON u.id=mp.user_id "
                    "WHERE u.role='leadership' AND m.topic='leadership' AND mp.status=%s",
                    (_COMPLETED,)) or 0
                lead_value, lead_note = leaders_done / total_leaders * 100, ""
            components["leadership_track_completion"] = _comp(
                "leadership_track_completion", 0.20, "Leadership Track Completion",
                lead_value, lead_note)

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

    result = composite_score(components, evidence)
    score = result["score"]
    phase_guidance_map = cfg.get("org_readiness", {}).get("modernization_phase_guidance", {})

    if score is None:
        tier, tier_color, tier_label = "insufficient", "#8899aa", "INSUFFICIENT DATA"
        guidance = result["insufficient_reason"]
        phase_guidance = ("A readiness score appears once learners complete missions. "
                          "Until then this page reports the cohort, not a verdict.")
    else:
        if score >= thresholds.get("green", 80):
            tier, tier_color = "green", "#00FF88"
        elif score >= thresholds.get("yellow", 65):
            tier, tier_color = "yellow", "#FFB800"
        else:
            tier, tier_color = "red", "#FF4444"
        tier_label = f"{tier.upper()} READINESS"
        if score < 40:
            guidance, phase_key = "Phase 0 work required — build AI foundations before pilots", "below_40"
        elif score < 65:
            guidance, phase_key = "Ready for Phase 1 quick-win AI projects", "40_to_65"
        elif score < 80:
            guidance, phase_key = "Ready for Phase 2 AI augmentation initiatives", "65_to_80"
        else:
            guidance, phase_key = "Ready for Phase 3+ AI-first transformation", "above_80"
        phase_guidance = phase_guidance_map.get(phase_key, guidance)

    return {
        "score": score,
        "score_display": "—" if score is None else f"{score}",
        "tier": tier,
        "tier_label": tier_label,
        "tier_color": tier_color,
        "guidance": guidance,
        "phase_guidance": phase_guidance,
        "coverage_pct": result["coverage_pct"],
        "components": components,
        "cohort": cohort,
        "skill_gaps": skill_gaps,
    }


def _table_exists(conn, name: str) -> bool:
    """Catalogue probe that never raises inside the caller's transaction."""
    try:
        if getattr(conn, "_backend", "") == "postgresql":
            row = conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()
            return bool(row and row[0])
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s", (name,)).fetchone()
        return bool(row)
    except Exception:
        return False


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
