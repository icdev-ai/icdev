# CUI // SP-CTI
"""IQE FORGE IGNITE Innovation canvas collection adapters.

Importing this module registers three collections on the module-level Executor:
  innovation.ideas       — Innovation idea pipeline (innov_ideas); filter by status, phase_tag.
  innovation.assessments — Assessment records (innov_assessments); filter by idea_id, human_approved.
  innovation.pilots      — Pilot records (innov_pilots); filter by idea_id, status.

Tables are created by apps/innovation/db.py. Adapters return [] gracefully
if tables are absent.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _innov_conn(conn: Any) -> tuple[Any, bool]:
    if conn is not None:
        return conn, False
    from tools.db.storage import get_canvas_connection  # noqa: PLC0415
    return get_canvas_connection(), True


def _safe_fetch(conn: Any, sql: str, params: tuple = ()) -> list[dict]:
    """Execute sql and return rows; return [] if the table does not exist yet."""
    try:
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def ideas_adapter(conn: Any) -> list[dict]:
    """Return rows from innov_ideas ordered by score descending."""
    c, owned = _innov_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, title, status, phase_tag, score_total, submitter_name, "
            "submitter_role, department, problem_statement, recommended_approach, "
            "created_at, updated_at "
            "FROM innov_ideas ORDER BY score_total DESC, updated_at DESC LIMIT 200",
        )
    finally:
        if owned:
            try:
                c.close()
            except Exception:
                pass


def assessments_adapter(conn: Any) -> list[dict]:
    """Return rows from innov_assessments."""
    c, owned = _innov_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, idea_id, assessed_by, business_value_score, "
            "strategic_alignment_score, technical_feasibility_score, "
            "data_readiness_score, time_to_value_score, risk_score, "
            "human_approved, created_at "
            "FROM innov_assessments ORDER BY created_at DESC LIMIT 200",
        )
    finally:
        if owned:
            try:
                c.close()
            except Exception:
                pass


def pilots_adapter(conn: Any) -> list[dict]:
    """Return rows from innov_pilots."""
    c, owned = _innov_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, idea_id, pilot_name, hypothesis, success_criteria, "
            "status, start_date, end_date, created_at "
            "FROM innov_pilots ORDER BY created_at DESC LIMIT 200",
        )
    finally:
        if owned:
            try:
                c.close()
            except Exception:
                pass


register_collection("innovation.ideas", ideas_adapter)
register_collection("innovation.assessments", assessments_adapter)
register_collection("innovation.pilots", pilots_adapter)
