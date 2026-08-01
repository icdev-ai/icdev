# CUI // SP-CTI
"""IQE Quality Design Canvas collection adapters.

Importing this module registers four collections on the module-level Executor:
  qdc.designs      — Quality gate topology designs (qdc_designs).
  qdc.assessments  — Assessment results per design (qdc_assessments); filter by
                     score, uqs_score, grade.
  qdc.gate_results — Gate execution results per design (qdc_gate_results); filter
                     by gate_id, status, dimension.
  qdc.ai_decisions — AI decision records for QDC from canvas_ai_decisions.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _qdc_conn(conn: Any) -> tuple[Any, bool]:
    """Return (connection, owned) — owned=True means caller must close it."""
    if conn is not None:
        return conn, False
    from tools.qdc_canvas.db.init_db import get_connection  # noqa: PLC0415
    return get_connection(), True


def designs_adapter(conn: Any) -> list[dict]:
    """Return rows from qdc_designs."""
    c, owned = _qdc_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, name, description, template_id, created_at, updated_at "
            "FROM qdc_designs"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def assessments_adapter(conn: Any) -> list[dict]:
    """Return rows from qdc_assessments enriched with design name."""
    c, owned = _qdc_conn(conn)
    try:
        cur = c.execute(
            "SELECT a.id, a.design_id, d.name AS design_name, a.assessment_type, "
            "a.score, a.uqs_score, a.created_at "
            "FROM qdc_assessments a "
            "LEFT JOIN qdc_designs d ON d.id = a.design_id"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def gate_results_adapter(conn: Any) -> list[dict]:
    """Return rows from qdc_gate_results."""
    c, owned = _qdc_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, design_id, gate_id, status, evidence_json, executed_at "
            "FROM qdc_gate_results ORDER BY executed_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def ai_decisions_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return AI decision records for QDC from canvas_ai_decisions (main icdev.db)."""
    try:
        from tools.db.storage import get_connection as _main_conn  # noqa: PLC0415
        with _main_conn() as _c:
            cur = _c.execute(
                "SELECT * FROM canvas_ai_decisions WHERE canvas_type='qdc' ORDER BY created_at DESC"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def twin_snapshots_adapter(conn: Any) -> list[dict]:
    """Return QDC twin snapshots (qdc_twin_snapshots) — the digital-twin surface."""
    c, owned = _qdc_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, design_id, label, node_count, edge_count, created_by, created_at "
            "FROM qdc_twin_snapshots ORDER BY created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


register_collection("qdc.designs", designs_adapter)
register_collection("qdc.assessments", assessments_adapter)
register_collection("qdc.gate_results", gate_results_adapter)
register_collection("qdc.ai_decisions", ai_decisions_adapter)
register_collection("qdc.twin_snapshots", twin_snapshots_adapter)
