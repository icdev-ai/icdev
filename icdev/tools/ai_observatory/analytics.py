# CUI // SP-CTI
"""AI Observatory analytics — query and aggregate canvas AI decisions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from tools.db.storage import get_connection


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_decision_stats(days: int = 7) -> dict[str, Any]:
    """Counts, avg confidence, and breakdowns by canvas/model for the last N days."""
    cutoff = (_now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with get_connection() as conn:
            row = conn.execute(
                """SELECT COUNT(*) as total,
                          AVG(confidence) as avg_confidence,
                          SUM(CASE WHEN confidence < 0.3 THEN 1 ELSE 0 END) as low_conf,
                          SUM(CASE WHEN decision_type = 'confabulation_flag' THEN 1 ELSE 0 END) as confab_flags
                   FROM canvas_ai_decisions WHERE created_at >= %s""",
                (cutoff,),
            ).fetchone()
            by_canvas = conn.execute(
                """SELECT canvas_type, COUNT(*) as cnt
                   FROM canvas_ai_decisions WHERE created_at >= %s
                   GROUP BY canvas_type ORDER BY cnt DESC""",
                (cutoff,),
            ).fetchall()
            by_model = conn.execute(
                """SELECT COALESCE(model_used, 'unknown') as model, COUNT(*) as cnt
                   FROM canvas_ai_decisions WHERE created_at >= %s
                   GROUP BY model ORDER BY cnt DESC""",
                (cutoff,),
            ).fetchall()
            by_type = conn.execute(
                """SELECT decision_type, COUNT(*) as cnt
                   FROM canvas_ai_decisions WHERE created_at >= %s
                   GROUP BY decision_type ORDER BY cnt DESC""",
                (cutoff,),
            ).fetchall()
    except Exception:
        return {"total": 0, "avg_confidence": None, "low_conf": 0, "confab_flags": 0,
                "by_canvas": [], "by_model": [], "by_type": []}

    r = dict(row) if row else {}
    return {
        "total": r.get("total", 0) or 0,
        "avg_confidence": round(r["avg_confidence"], 3) if r.get("avg_confidence") is not None else None,
        "low_conf": r.get("low_conf", 0) or 0,
        "confab_flags": r.get("confab_flags", 0) or 0,
        "top_canvas": dict(by_canvas[0])["canvas_type"] if by_canvas else None,
        "by_canvas": [dict(r) for r in by_canvas],
        "by_model": [dict(r) for r in by_model],
        "by_type": [dict(r) for r in by_type],
        "days": days,
    }


def get_decisions(
    canvas: str | None = None,
    decision_type: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Filtered list of AI decisions, newest first."""
    try:
        clauses = []
        params: list = []
        if canvas:
            clauses.append("canvas_type = %s")
            params.append(canvas)
        if decision_type:
            clauses.append("decision_type = %s")
            params.append(decision_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with get_connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM canvas_ai_decisions {where} ORDER BY created_at DESC LIMIT %s",  # nosec B608
                params,
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_confidence_heatmap() -> list[dict]:
    """Average confidence per canvas × decision_type pair."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """SELECT canvas_type, decision_type,
                          AVG(confidence) as avg_confidence,
                          COUNT(*) as count
                   FROM canvas_ai_decisions
                   WHERE confidence IS NOT NULL
                   GROUP BY canvas_type, decision_type
                   ORDER BY canvas_type, decision_type"""
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_confabulation_flags(days: int = 7) -> list[dict]:
    """Decisions with decision_type='confabulation_flag' in the last N days."""
    cutoff = (_now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM canvas_ai_decisions
                   WHERE decision_type = 'confabulation_flag'
                   AND created_at >= %s
                   ORDER BY created_at DESC""",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
