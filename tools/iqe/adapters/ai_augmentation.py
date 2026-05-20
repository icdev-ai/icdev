# CUI // SP-CTI
"""IQE AI Augmentation collection adapters.

Importing this module registers three collections on the module-level Executor:
  ai_augmentation.opportunities — AAC opportunities with scores (aac_opportunities JOIN aac_scores)
  ai_augmentation.scans         — AAC scan records (aac_scans)
  ai_augmentation.roadmaps      — AAC roadmap records (aac_roadmaps)
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    return conn


def opportunities_adapter(conn: Any) -> list[dict]:
    """Return rows from aac_opportunities joined with aac_scores."""
    conn = _conn(conn)
    cur = conn.execute(
        "SELECT o.id, o.scan_id, o.module_path, o.function_name, o.language, "
        "o.pattern_type, o.ai_paradigm, o.il_recommended_model, "
        "s.composite_score, s.value_score, s.feasibility_score, "
        "s.risk_score, s.effort_days "
        "FROM aac_opportunities o "
        "LEFT JOIN aac_scores s ON s.opportunity_id = o.id "
        "ORDER BY s.composite_score DESC"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def scans_adapter(conn: Any) -> list[dict]:
    """Return rows from aac_scans."""
    conn = _conn(conn)
    cur = conn.execute(
        "SELECT scan_id, input_type, input_ref, language_profile, "
        "total_files, total_loc, status, created_at, completed_at "
        "FROM aac_scans ORDER BY created_at DESC"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def roadmaps_adapter(conn: Any) -> list[dict]:
    """Return rows from aac_roadmaps."""
    conn = _conn(conn)
    cur = conn.execute(
        "SELECT scan_id, roadmap_id, title, total_effort_days, created_at "
        "FROM aac_roadmaps ORDER BY created_at DESC"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


register_collection("ai_augmentation.opportunities", opportunities_adapter)
register_collection("ai_augmentation.scans", scans_adapter)
register_collection("ai_augmentation.roadmaps", roadmaps_adapter)
