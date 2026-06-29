# CUI // SP-CTI
"""Unified Second Brain search — keyword across all personal data."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger
logger = get_logger(__name__)


def unified_search(
    query: str, user_id: str, tenant_id: str = "default", limit: int = 30
) -> dict[str, list[dict]]:
    """Search all Second Brain data sources for *query*.

    Returns dict keyed by source type; each value is a list of result dicts:
        {title, snippet, url, source, meta}
    Empty buckets are excluded.
    """
    if not query or len(query.strip()) < 2:
        return {}

    q = query.strip()
    results: dict[str, list[dict]] = {
        "objectives":    _search_objectives(q, user_id, tenant_id, limit=8),
        "relationships": _search_relationships(q, user_id, tenant_id, limit=8),
        "interactions":  _search_interactions(q, user_id, tenant_id, limit=8),
        "knowledge":     _search_knowledge(q, user_id, tenant_id, limit=8),
        "briefings":     _search_briefings(q, user_id, tenant_id, limit=5),
    }
    return {k: v for k, v in results.items() if v}


def _like(q: str) -> str:
    return f"%{q}%"


def _search_objectives(q: str, user_id: str, tenant_id: str, limit: int) -> list[dict]:
    try:
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        from tools.db.storage import get_canvas_connection, sql_placeholder
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            ph = sql_placeholder(conn)
            lq = _like(q)
            rows = conn.execute(
                f"SELECT id, title, description, horizon, status FROM user_objectives "
                f"WHERE user_id={ph} AND tenant_id={ph} AND (title LIKE {ph} OR description LIKE {ph}) "
                f"ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END LIMIT {limit}",
                (user_id, tenant_id, lq, lq),
            ).fetchall()
        return [
            {
                "id": r[0], "title": r[1],
                "snippet": (r[2] or "")[:100],
                "url": "/me/objectives", "source": "objectives",
                "meta": f"{r[3] or ''} · {r[4] or ''}".strip(" ·"),
            }
            for r in rows
        ]
    except Exception as exc:
        logger.debug("[search] objectives: %s", exc)
        return []


def _search_relationships(q: str, user_id: str, tenant_id: str, limit: int) -> list[dict]:
    try:
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        from tools.db.storage import get_canvas_connection, sql_placeholder
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            ph = sql_placeholder(conn)
            lq = _like(q)
            rows = conn.execute(
                f"SELECT id, name, title, org, relationship_type FROM user_relationships "
                f"WHERE user_id={ph} AND tenant_id={ph} "
                f"AND (name LIKE {ph} OR org LIKE {ph} OR notes LIKE {ph} OR title LIKE {ph}) "
                f"ORDER BY name LIMIT {limit}",
                (user_id, tenant_id, lq, lq, lq, lq),
            ).fetchall()
        return [
            {
                "id": r[0], "title": r[1],
                "snippet": f"{r[2] or ''} · {r[3] or ''}".strip(" ·"),
                "url": "/me/customers", "source": "relationships",
                "meta": r[4] or "",
            }
            for r in rows
        ]
    except Exception as exc:
        logger.debug("[search] relationships: %s", exc)
        return []


def _search_interactions(q: str, user_id: str, tenant_id: str, limit: int) -> list[dict]:
    try:
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        from tools.db.storage import get_canvas_connection, sql_placeholder
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            ph = sql_placeholder(conn)
            lq = _like(q)
            rows = conn.execute(
                f"SELECT i.id, i.title, i.notes, i.interaction_date, r.name "
                f"FROM user_relationship_interactions i "
                f"LEFT JOIN user_relationships r ON r.id = i.relationship_id "
                f"WHERE i.user_id={ph} AND i.tenant_id={ph} "
                f"AND (i.title LIKE {ph} OR i.notes LIKE {ph}) "
                f"ORDER BY i.interaction_date DESC LIMIT {limit}",
                (user_id, tenant_id, lq, lq),
            ).fetchall()
        return [
            {
                "id": r[0], "title": r[1],
                "snippet": (r[2] or "")[:100],
                "url": "/me/customers", "source": "interactions",
                "meta": f"{r[3] or ''} · {r[4] or ''}".strip(" ·"),
            }
            for r in rows
        ]
    except Exception as exc:
        logger.debug("[search] interactions: %s", exc)
        return []


def _search_knowledge(q: str, user_id: str, tenant_id: str, limit: int) -> list[dict]:
    try:
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        from tools.db.storage import get_canvas_connection, sql_placeholder
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            ph = sql_placeholder(conn)
            lq = _like(q)
            rows = conn.execute(
                f"SELECT id, title, summary, source_url, created_at FROM user_knowledge_items "
                f"WHERE user_id={ph} AND tenant_id={ph} AND status='done' "
                f"AND (title LIKE {ph} OR summary LIKE {ph} OR raw_content LIKE {ph}) "
                f"ORDER BY created_at DESC LIMIT {limit}",
                (user_id, tenant_id, lq, lq, lq),
            ).fetchall()
        return [
            {
                "id": r[0],
                "title": r[1] or r[3] or "Untitled",
                "snippet": (r[2] or "")[:100],
                "url": r[3] or "/me/learn",
                "source": "knowledge",
                "meta": str(r[4] or "")[:10],
            }
            for r in rows
        ]
    except Exception as exc:
        logger.debug("[search] knowledge: %s", exc)
        return []


def _search_briefings(q: str, user_id: str, tenant_id: str, limit: int) -> list[dict]:
    import json as _j
    try:
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        from tools.db.storage import get_canvas_connection, sql_placeholder
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            ph = sql_placeholder(conn)
            lq = _like(q)
            rows = conn.execute(
                f"SELECT id, briefing_date, content_json FROM user_daily_briefings "
                f"WHERE user_id={ph} AND tenant_id={ph} AND content_json LIKE {ph} "
                f"ORDER BY briefing_date DESC LIMIT {limit}",
                (user_id, tenant_id, lq),
            ).fetchall()
        results = []
        for r in rows:
            try:
                content = _j.loads(r[2] or "{}")
                snippet = content.get("greeting") or content.get("summary") or ""
            except Exception:
                snippet = ""
            results.append({
                "id": r[0],
                "title": f"Briefing {r[1]}",
                "snippet": snippet[:100],
                "url": "/me/briefing/today",
                "source": "briefings",
                "meta": str(r[1]),
            })
        return results
    except Exception as exc:
        logger.debug("[search] briefings: %s", exc)
        return []
