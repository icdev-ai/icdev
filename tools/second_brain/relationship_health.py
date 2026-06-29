# CUI // SP-CTI
"""Relationship health scoring and smart nudge generation for the daily briefing."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Staleness thresholds per relationship type: (amber_days, red_days)
_THRESHOLDS: dict[str, tuple[int, int]] = {
    "boss":        (7,  14),
    "customer":    (14, 30),
    "stakeholder": (14, 30),
    "direct":      (7,  14),
    "peer":        (21, 45),
    "vendor":      (30, 60),
    "other":       (30, 60),
}
_DEFAULT_THRESHOLD = (21, 45)


def score_relationship(rel: dict[str, Any], last_interaction_date: str | None) -> dict[str, Any]:
    """Return health score dict for a single relationship.

    Returns:
        {
          "status": "green" | "amber" | "red",
          "days_since": int | None,
          "label": str,
          "nudge": str | None,
        }
    """
    rtype = rel.get("relationship_type", "other")
    amber_days, red_days = _THRESHOLDS.get(rtype, _DEFAULT_THRESHOLD)

    last_date_str = last_interaction_date or rel.get("last_contact_at") or ""
    if not last_date_str:
        return {
            "status": "amber",
            "days_since": None,
            "label": "No contact logged",
            "nudge": f"No interactions logged with {rel.get('name', 'this contact')} yet.",
        }

    try:
        last_dt = datetime.fromisoformat(last_date_str[:10]).replace(tzinfo=timezone.utc)
    except Exception:
        return {"status": "amber", "days_since": None, "label": "Unknown", "nudge": None}

    days = (datetime.now(timezone.utc) - last_dt).days

    if days <= amber_days:
        return {"status": "green", "days_since": days, "label": f"{days}d ago", "nudge": None}

    if days <= red_days:
        return {
            "status": "amber",
            "days_since": days,
            "label": f"{days}d ago",
            "nudge": (
                f"{rel.get('name', 'This contact')} ({rtype}) — last contact {days} days ago. "
                f"Consider a quick check-in."
            ),
        }

    return {
        "status": "red",
        "days_since": days,
        "label": f"{days}d ago",
        "nudge": (
            f"⚠ {rel.get('name', 'This contact')} ({rtype}) — {days} days without contact. "
            f"This relationship may be drifting."
        ),
    }


def get_relationship_health_map(user_id: str, tenant_id: str = "default") -> list[dict[str, Any]]:
    """Return all relationships with a 'health' key attached, sorted red→amber→green."""
    try:
        from tools.second_brain.profile import get_relationships
        from tools.second_brain.interactions import get_interactions
        rels = get_relationships(user_id, tenant_id) or []
    except Exception as exc:
        logger.debug("[rel_health] could not load relationships: %s", exc)
        return []

    result: list[dict[str, Any]] = []
    for rel in rels:
        try:
            interactions = get_interactions(rel["id"], user_id, tenant_id, limit=1)
            last_date = interactions[0]["date"] if interactions else None
        except Exception:
            last_date = None

        health = score_relationship(rel, last_date)
        result.append({**rel, "health": health})

    order = {"red": 0, "amber": 1, "green": 2}
    result.sort(key=lambda x: order.get(x["health"]["status"], 3))
    return result


def generate_relationship_nudges(user_id: str, tenant_id: str = "default") -> list[dict[str, Any]]:
    """Return nudge dicts for amber/red relationships — consumed by the daily briefing."""
    health_map = get_relationship_health_map(user_id, tenant_id)
    nudges: list[dict[str, Any]] = []
    for rel in health_map:
        h = rel.get("health", {})
        if h.get("status") in ("amber", "red") and h.get("nudge"):
            nudges.append({
                "name": rel.get("name", ""),
                "relationship_type": rel.get("relationship_type", ""),
                "status": h["status"],
                "days_since": h.get("days_since"),
                "nudge": h["nudge"],
                "relationship_id": rel.get("id", ""),
            })
    return nudges[:5]
