# CUI // SP-CTI
"""AI Skills Gap Tracker — maturity assessment sourced from live AISG tables.

Sources ONLY real data (no canned skill levels, recommendations, or roster):
  * skill domains + proficiency -> ``aisg_skills`` (proficiency_levels_json)
  * recommendations             -> derived from the real per-skill gaps
  * team roster                 -> distinct actors in ``aisg_roi_events``

When ``aisg_skills`` has no rows the page shows an honest "not assessed" empty
state instead of fabricated maturity scores.
"""
from __future__ import annotations

import json

from tools.db.storage import get_connection

from tools.logging.icdev_logger import get_logger
logger = get_logger("icdev.aisg.skills_tracker")

# Legend only — maps an integer maturity level to its display label/badge.
_MATURITY_LEVELS = {
    1: {"label": "Exploring", "color": "text-dim", "badge_class": "badge-secondary"},
    2: {"label": "Piloting", "color": "text-warning", "badge_class": "badge-warning"},
    3: {"label": "Adopting", "color": "text-info", "badge_class": "badge-info"},
    4: {"label": "Scaling", "color": "text-success", "badge_class": "badge-success"},
    5: {"label": "Mastering", "color": "text-accent", "badge_class": "badge-primary"},
}


def _coerce_int(value):
    try:
        if value is None or value == "":
            return None
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _parse_levels(row: dict):
    """Extract (current_level, target_level) from a skill's proficiency JSON.

    Accepts an object ``{"current": n, "target": m}`` or a list of level dicts.
    Returns (None, None) when nothing usable is present.
    """
    raw = row.get("proficiency_levels_json")
    if not raw:
        return None, None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None, None

    if isinstance(parsed, dict):
        return _coerce_int(parsed.get("current")), _coerce_int(parsed.get("target"))
    if isinstance(parsed, list) and parsed:
        current = target = None
        for entry in parsed:
            if isinstance(entry, dict):
                if entry.get("current") is not None:
                    current = _coerce_int(entry.get("current"))
                if entry.get("target") is not None:
                    target = _coerce_int(entry.get("target"))
        return current, target
    return None, None


def _load_skill_domains(conn) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT id, name, description, skill_type, proficiency_levels_json, status "
            "FROM aisg_skills WHERE status != 'archived' ORDER BY name"
        ).fetchall()
    except Exception as exc:
        logger.warning("skills_tracker: aisg_skills query failed: %s", exc)
        return []

    domains: list[dict] = []
    for r in rows:
        d = dict(r)
        current, target = _parse_levels(d)
        gap = (target - current) if (current is not None and target is not None) else 0
        domains.append({
            "id": d.get("id"),
            "name": d.get("name") or d.get("id") or "Unnamed Skill",
            "description": d.get("description") or "",
            "category": d.get("skill_type") or "general",
            "current_level": current,
            "target_level": target,
            "gap": max(0, gap),
        })
    return domains


def _build_recommendations(domains: list[dict]) -> list[dict]:
    """Recommendations derived only from real gaps — no static content."""
    recs: list[dict] = []
    for s in sorted(domains, key=lambda x: -x["gap"]):
        if s["gap"] <= 0:
            continue
        action = (
            f"Close the {s['gap']}-level gap in {s['name']} "
            f"(current {s['current_level']} → target {s['target_level']}) "
            "via a learning track."
        )
        recs.append({
            "skill": s["name"],
            "action": action,
            "priority": "high" if s["gap"] >= 2 else "medium",
            "link": "/ai-learning",
            "gap": s["gap"],
        })
    return recs[:5]


def _load_team(conn, limit: int = 8) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT triggered_by, COUNT(*) AS cnt FROM aisg_roi_events "
            "WHERE triggered_by IS NOT NULL AND triggered_by != '' "
            "GROUP BY triggered_by ORDER BY cnt DESC LIMIT %s",
            (limit,),
        ).fetchall()
    except Exception as exc:
        logger.warning("skills_tracker: aisg_roi_events roster query failed: %s", exc)
        return []

    team: list[dict] = []
    for r in rows:
        d = dict(r)
        cnt = d.get("cnt") or 0
        team.append({
            "name": d.get("triggered_by") or "—",
            "maturity": f"{cnt} automated action(s)",
        })
    return team


def get_skills_data() -> dict:
    """Return skill domains, maturity badge, and recommendations (live data only)."""
    result = {
        "skill_domains": [],
        "maturity_label": "Not assessed",
        "maturity_badge_class": "badge-secondary",
        "maturity_score": None,
        "avg_level": None,
        "total_skills": 0,
        "recommendations": [],
        "team_members": [],
        "maturity_levels": _MATURITY_LEVELS,
        "data_source_error": None,
    }

    try:
        conn = get_connection()
    except Exception as exc:
        logger.error("skills_tracker: database unavailable: %s", exc)
        result["data_source_error"] = str(exc)
        return result

    try:
        domains = _load_skill_domains(conn)
        # Show the widest gaps first.
        domains.sort(key=lambda x: -x["gap"])
        result["skill_domains"] = domains
        result["total_skills"] = len(domains)

        levels = [s["current_level"] for s in domains if s["current_level"] is not None]
        if levels:
            avg = round(sum(levels) / len(levels), 1)
            maturity_int = max(1, min(5, round(avg)))
            result["avg_level"] = avg
            result["maturity_score"] = maturity_int
            result["maturity_label"] = _MATURITY_LEVELS[maturity_int]["label"]
            result["maturity_badge_class"] = _MATURITY_LEVELS[maturity_int]["badge_class"]

        result["recommendations"] = _build_recommendations(domains)
        result["team_members"] = _load_team(conn)
    except Exception as exc:
        logger.exception("skills_tracker: unexpected failure building skills data")
        result["data_source_error"] = str(exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return result
