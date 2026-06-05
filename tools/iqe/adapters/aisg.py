# CUI // SP-CTI
"""IQE AI Skills & Growth (AISG) collection adapters.

Registers 4 collections on the module-level Executor over the live PG
`aisg_*` tables (no new tables — all already exist):
  aisg.roadmaps — AISG enablement roadmaps (aisg_roadmaps)
  aisg.skills   — AISG skill catalog with usage rollup (aisg_skills + aisg_skill_usage)
  aisg.roi      — AISG ROI tracking periods (aisg_roi_tracking + aisg_roi_events rollup)
  aisg.patterns — AISG reusable AI patterns (aisg_patterns)

All tables are RLS-aware (classification/tenant_id), so reads go through the
main ICDEV `get_connection()`.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _aisg_conn(conn: Any) -> tuple[Any, bool]:
    """Return (connection, owned). Reuses an injected conn or opens an RLS-aware one."""
    if conn is not None:
        return conn, False
    from tools.db.storage import get_connection  # noqa: PLC0415
    return get_connection(), True


def roadmaps_adapter(conn: Any) -> list[dict]:
    """Return rows from aisg_roadmaps."""
    c, owned = _aisg_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, name, description, roadmap_type, status, "
            "classification, created_at, updated_at "
            "FROM aisg_roadmaps ORDER BY created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def skills_adapter(conn: Any) -> list[dict]:
    """Return aisg_skills enriched with usage rollup from aisg_skill_usage."""
    c, owned = _aisg_conn(conn)
    try:
        cur = c.execute(
            "SELECT s.id, s.name, s.skill_type, s.description, s.status, "
            "s.classification, s.created_at, s.updated_at, "
            "COALESCE(SUM(u.use_count), 0) AS total_uses, "
            "COUNT(DISTINCT u.user_email) AS user_count, "
            "MAX(u.last_used_at) AS last_used_at "
            "FROM aisg_skills s "
            "LEFT JOIN aisg_skill_usage u ON u.skill_name = s.name "
            "GROUP BY s.id, s.name, s.skill_type, s.description, s.status, "
            "s.classification, s.created_at, s.updated_at "
            "ORDER BY total_uses DESC, s.name"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def roi_adapter(conn: Any) -> list[dict]:
    """Return aisg_roi_tracking periods joined to roadmap names."""
    c, owned = _aisg_conn(conn)
    try:
        cur = c.execute(
            "SELECT t.id, t.roadmap_id, r.name AS roadmap_name, t.action_type, "
            "t.count, t.minutes_saved, t.cost_saved_usd, "
            "t.period_start, t.period_end, t.classification, t.created_at "
            "FROM aisg_roi_tracking t "
            "LEFT JOIN aisg_roadmaps r ON r.id = t.roadmap_id "
            "ORDER BY t.period_end DESC NULLS LAST, t.created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def patterns_adapter(conn: Any) -> list[dict]:
    """Return rows from aisg_patterns."""
    c, owned = _aisg_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, name, category, description, use_case, "
            "is_builtin, tags, classification, created_at, updated_at "
            "FROM aisg_patterns ORDER BY category, name"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


register_collection("aisg.roadmaps", roadmaps_adapter)
register_collection("aisg.skills",   skills_adapter)
register_collection("aisg.roi",      roi_adapter)
register_collection("aisg.patterns", patterns_adapter)
