# CUI // SP-CTI
"""IQE adapter for the Second Brain canvas — registers collections and handles queries."""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

COLLECTIONS = [
    "second_brain.profile",
    "second_brain.objectives",
    "second_brain.briefings",
    "second_brain.relationships",
]


def get_collections() -> list[str]:
    return COLLECTIONS


def query(q: str, collection: str, user_id: str = "default", tenant_id: str = "default") -> list[dict]:
    """Dispatch an IQE natural-language query to the appropriate second_brain collection."""
    try:
        if collection == "second_brain.profile":
            return _query_profile(q, user_id, tenant_id)
        if collection == "second_brain.objectives":
            return _query_objectives(q, user_id, tenant_id)
        if collection == "second_brain.briefings":
            return _query_briefings(q, user_id, tenant_id)
        if collection == "second_brain.relationships":
            return _query_relationships(q, user_id, tenant_id)
    except Exception as exc:
        logger.warning("[second_brain IQE] query error: %s", exc)
    return []


def _query_profile(q: str, user_id: str, tenant_id: str) -> list[dict]:
    from tools.second_brain.profile import get_full_profile
    profile = get_full_profile(user_id, tenant_id) or {}
    if not profile:
        return [{"type": "text", "content": "No profile found. Complete onboarding at /me."}]
    return [{"type": "profile", "content": profile.get("profile", {}), "summary": profile.get("profile", {}).get("profile_summary", "")}]


def _query_objectives(q: str, user_id: str, tenant_id: str) -> list[dict]:
    from tools.second_brain.profile import get_objectives
    objs = get_objectives(user_id, tenant_id)
    if not objs:
        return [{"type": "text", "content": "No objectives found. Add them at /me/objectives."}]
    q_lower = q.lower()
    filtered = [o for o in objs if q_lower in (o.get("title") or "").lower()] or objs
    return [{"type": "objective", "id": o["id"], "title": o["title"], "horizon": o.get("horizon"), "status": o.get("status")} for o in filtered[:10]]


def _query_briefings(q: str, user_id: str, tenant_id: str) -> list[dict]:
    from tools.second_brain.briefing import get_todays_briefing
    briefing = get_todays_briefing(user_id, tenant_id)
    if not briefing:
        return [{"type": "text", "content": "No briefing for today. Generate one at /me."}]
    return [{"type": "briefing", "date": briefing.get("date"), "greeting": briefing.get("greeting"), "focus": briefing.get("focus"), "meetings_count": len(briefing.get("meetings", [])), "tasks_count": len(briefing.get("tasks", []))}]


def _query_relationships(q: str, user_id: str, tenant_id: str) -> list[dict]:
    from tools.second_brain.profile import get_relationships
    rels = get_relationships(user_id, tenant_id)
    q_lower = q.lower()
    filtered = [r for r in rels if q_lower in (r.get("name") or "").lower() or q_lower in (r.get("title") or "").lower()] or rels
    return [{"type": "relationship", "name": r["name"], "title": r.get("title"), "relationship_type": r.get("relationship_type"), "email": r.get("email")} for r in filtered[:10]]
