# CUI // SP-CTI
"""IQE Migration Design Canvas collection adapters.

Importing this module registers three collections on the module-level Executor:
  mc.designs     — Migration designs (migration_designs); filter by
                   migration_type, classification, status.
  mc.waves       — Wave plans per design (mc_wave_plans); filter by
                   strategy, status, wave_number, risk_score.
  mc.assessments — Assessment results (mc_assessments); filter by
                   grade, score, readiness_score, cat1_findings.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _mc_conn(conn: Any) -> tuple[Any, bool]:
    if conn is not None:
        return conn, False
    from tools.migration_canvas.db.init_db import get_connection  # noqa: PLC0415
    return get_connection(), True


def designs_adapter(conn: Any) -> list[dict]:
    """Return rows from migration_designs."""
    c, owned = _mc_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, name, description, migration_type, classification, "
            "created_at, updated_at FROM migration_designs"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def waves_adapter(conn: Any) -> list[dict]:
    """Return rows from mc_wave_plans enriched with design name."""
    c, owned = _mc_conn(conn)
    try:
        cur = c.execute(
            "SELECT w.id, w.design_id, d.name AS design_name, w.wave_number, "
            "w.name, w.strategy, w.status, w.estimated_hours, w.risk_score, "
            "w.start_date, w.end_date, w.created_at "
            "FROM mc_wave_plans w "
            "LEFT JOIN migration_designs d ON d.id = w.design_id"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def assessments_adapter(conn: Any) -> list[dict]:
    """Return rows from mc_assessments enriched with design name."""
    c, owned = _mc_conn(conn)
    try:
        cur = c.execute(
            "SELECT a.id, a.design_id, d.name AS design_name, a.assessment_type, "
            "a.score, a.grade, a.cat1_findings, a.cat2_findings, a.cat3_findings, "
            "a.readiness_score, a.created_at "
            "FROM mc_assessments a "
            "LEFT JOIN migration_designs d ON d.id = a.design_id"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def ai_decisions_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return AI decision records for MC from canvas_ai_decisions (main icdev.db)."""
    try:
        from tools.db.storage import get_connection as _main_conn  # noqa: PLC0415
        with _main_conn() as _c:
            cur = _c.execute(
                "SELECT * FROM canvas_ai_decisions WHERE canvas_type='mc' ORDER BY created_at DESC"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


register_collection("mc.designs", designs_adapter)
register_collection("mc.waves", waves_adapter)
register_collection("mc.assessments", assessments_adapter)
register_collection("mc.ai_decisions", ai_decisions_adapter)
