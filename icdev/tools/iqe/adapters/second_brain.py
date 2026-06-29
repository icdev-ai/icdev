# CUI // SP-CTI
"""IQE adapter for the Second Brain canvas — registers collections and handles queries.

Importing this module registers six read-only collections on the module-level
Executor. Each adapter_fn(conn) returns all rows; the IQE executor handles
WHERE/SELECT projection. second_brain tables have no classification/tenant_id
columns so these adapters use the higher-level module APIs directly (no conn).
"""
from __future__ import annotations
from typing import Any

from tools.iqe.executor import register_collection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

COLLECTIONS = [
    "second_brain.profile",
    "second_brain.objectives",
    "second_brain.briefings",
    "second_brain.relationships",
    "second_brain.commitments",
    "second_brain.knowledge",
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
        if collection == "second_brain.commitments":
            return _query_commitments(q, user_id, tenant_id)
        if collection == "second_brain.knowledge":
            return _query_knowledge(q, user_id, tenant_id)
    except Exception as exc:
        logger.warning("[second_brain IQE] query error: %s", exc)
    return []


# ── profile ──────────────────────────────────────────────────────────────────

def _query_profile(q: str, user_id: str, tenant_id: str) -> list[dict]:
    try:
        from tools.second_brain.profile import get_profile
        profile = get_profile(user_id, tenant_id) or {}
    except Exception:
        try:
            from tools.second_brain.profile import get_full_profile
            full = get_full_profile(user_id, tenant_id) or {}
            profile = full.get("profile", {})
        except Exception:
            profile = {}
    if not profile:
        return [{"type": "text", "content": "No profile found. Complete onboarding at /me/profile."}]
    return [{
        "type": "profile",
        "content": {
            "name": profile.get("full_name") or profile.get("name", ""),
            "title": profile.get("title", ""),
            "org": profile.get("org_name", ""),
            "timezone": profile.get("timezone", "UTC"),
            "briefing_time": profile.get("briefing_time", "08:00"),
        },
        "summary": profile.get("profile_summary", ""),
    }]


# ── objectives ────────────────────────────────────────────────────────────────

def _query_objectives(q: str, user_id: str, tenant_id: str) -> list[dict]:
    from tools.second_brain.profile import get_objectives
    objs = get_objectives(user_id, tenant_id) or []
    if not objs:
        return [{"type": "text", "content": "No objectives found. Add them at /me/objectives."}]
    q_lower = q.lower()
    # Filter by keywords if query is specific
    words = [w for w in q_lower.split() if len(w) > 3]
    if words:
        filtered = [o for o in objs if any(w in (o.get("title") or "").lower() or w in (o.get("description") or "").lower() for w in words)]
        if not filtered:
            filtered = objs  # fall back to all
    else:
        filtered = objs
    return [{
        "type": "objective",
        "id": o.get("id", ""),
        "title": o.get("title", ""),
        "horizon": o.get("horizon", ""),
        "status": o.get("status", "active"),
        "progress": o.get("auto_progress_pct", 0),
        "url": "/me/objectives",
    } for o in filtered[:10]]


# ── briefings ─────────────────────────────────────────────────────────────────

def _query_briefings(q: str, user_id: str, tenant_id: str) -> list[dict]:
    # Try today's briefing first
    try:
        from tools.second_brain.briefing import get_todays_briefing
        briefing = get_todays_briefing(user_id, tenant_id)
        if briefing:
            return [{
                "type": "briefing",
                "date": briefing.get("date", "today"),
                "greeting": briefing.get("greeting", ""),
                "focus": briefing.get("focus", ""),
                "meetings_count": len(briefing.get("meetings", [])),
                "tasks_count": len(briefing.get("tasks", [])),
                "url": "/me/briefing/today",
            }]
    except Exception:
        pass
    # Fall back to search across stored briefings
    try:
        from tools.second_brain.search import _search_briefings
        items = _search_briefings(q or "briefing", user_id, tenant_id, limit=5)
        if items:
            return [{
                "type": "briefing",
                "date": i.get("meta", ""),
                "snippet": i.get("snippet", "")[:150],
                "url": "/me/briefing/today",
            } for i in items]
    except Exception:
        pass
    return [{"type": "text", "content": "No briefing found. Generate one at /me/briefing/today."}]


# ── relationships ─────────────────────────────────────────────────────────────

def _query_relationships(q: str, user_id: str, tenant_id: str) -> list[dict]:
    from tools.second_brain.profile import get_relationships
    rels = get_relationships(user_id, tenant_id) or []
    if not rels:
        return [{"type": "text", "content": "No relationships found. Add contacts at /me/customers."}]
    q_lower = q.lower()

    # Detect "stale / follow up" intent
    stale_intent = any(kw in q_lower for kw in ("haven't", "not talked", "follow up", "recently", "overdue", "contact"))

    try:
        from tools.second_brain.relationship_health import score_relationship
        health_fn = score_relationship
    except Exception:
        health_fn = None

    results = []
    for r in rels:
        name = r.get("name", "")
        org = r.get("org", "") or ""
        rtype = r.get("relationship_type", "")
        notes = r.get("notes", "") or ""
        last = r.get("last_contact_at", "")

        health = health_fn(r, last) if health_fn else {"status": "unknown", "label": ""}

        if stale_intent:
            if health.get("status") == "green":
                continue
        elif len(q_lower) > 3:
            words = [w for w in q_lower.split() if len(w) > 3]
            search_text = f"{name} {org} {rtype} {notes}".lower()
            if words and not any(w in search_text for w in words):
                continue

        results.append({
            "type": "relationship",
            "name": name,
            "title": r.get("title", ""),
            "relationship_type": rtype,
            "org": org,
            "email": r.get("email", ""),
            "health": health.get("label", ""),
            "health_status": health.get("status", ""),
            "url": "/me/customers",
        })

    if stale_intent:
        results.sort(key=lambda x: x.get("health_status") == "red", reverse=True)
    return results[:10]


# ── commitments ───────────────────────────────────────────────────────────────

def _query_commitments(q: str, user_id: str, tenant_id: str) -> list[dict]:
    try:
        from tools.second_brain.proactive_advisor import generate_commitment_alerts
        alerts = generate_commitment_alerts(user_id, tenant_id)
    except Exception:
        return [{"type": "text", "content": "Commitment data unavailable. Ensure relationships with commitment_date are set at /me/customers."}]

    if not alerts:
        return [{"type": "text", "content": "No upcoming commitments found. Add commitment dates to your relationships at /me/customers."}]

    q_lower = q.lower()
    if "overdue" in q_lower or "late" in q_lower:
        alerts = [a for a in alerts if a.get("urgency") == "overdue"]
    elif "week" in q_lower or "soon" in q_lower:
        alerts = [a for a in alerts if a.get("urgency") in ("overdue", "critical", "high")]

    return [{
        "type": "commitment",
        "name": a.get("name", ""),
        "label": a.get("label", "Commitment"),
        "date": a.get("date", ""),
        "urgency": a.get("urgency", ""),
        "days_until": a.get("days_until"),
        "url": "/me/customers",
    } for a in alerts[:10]]


# ── knowledge ─────────────────────────────────────────────────────────────────

def _query_knowledge(q: str, user_id: str, tenant_id: str) -> list[dict]:
    try:
        from tools.second_brain.personal_rag import search_personal_rag, get_items
        if len(q.strip()) >= 2:
            items = search_personal_rag(q, user_id, tenant_id, limit=8)
        else:
            items = get_items(user_id, tenant_id, limit=8)
    except Exception:
        return [{"type": "text", "content": "Knowledge base unavailable."}]

    if not items:
        return [{"type": "text", "content": "No knowledge items found. Add URLs or notes at /me/learn."}]

    return [{
        "type": "knowledge",
        "title": i.get("title", "Untitled"),
        "snippet": (i.get("summary") or "")[:150],
        "source_url": i.get("url") or i.get("source_url", ""),
        "status": i.get("status", ""),
        "url": "/me/learn",
    } for i in items[:10]]


# ── Executor registration ─────────────────────────────────────────────────────
# These adapter_fn(conn) wrappers are called by the IQE executor after nl_to_iqe
# generates a ForeachNode. conn is unused — second_brain uses higher-level APIs.

def _profile_adapter(conn: Any) -> list[dict]:
    return _query_profile("", "default", "default")


def _objectives_adapter(conn: Any) -> list[dict]:
    return _query_objectives("", "default", "default")


def _briefings_adapter(conn: Any) -> list[dict]:
    return _query_briefings("", "default", "default")


def _relationships_adapter(conn: Any) -> list[dict]:
    return _query_relationships("", "default", "default")


def _commitments_adapter(conn: Any) -> list[dict]:
    return _query_commitments("", "default", "default")


def _knowledge_adapter(conn: Any) -> list[dict]:
    return _query_knowledge("", "default", "default")


register_collection("second_brain.profile", _profile_adapter)
register_collection("second_brain.objectives", _objectives_adapter)
register_collection("second_brain.briefings", _briefings_adapter)
register_collection("second_brain.relationships", _relationships_adapter)
register_collection("second_brain.commitments", _commitments_adapter)
register_collection("second_brain.knowledge", _knowledge_adapter)
