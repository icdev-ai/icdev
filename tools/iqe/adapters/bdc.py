# CUI // SP-CTI
"""IQE Boundary Design Canvas collection adapters.

Importing this module registers four collections on the module-level Executor:
  bdc.designs      — Boundary designs (boundary_designs); filter by classification.
  bdc.assessments  — Assessment results per design (bd_assessments); filter by
                     grade, score, cat1_findings, etc.
  bdc.isas         — Interconnection Security Agreements (bd_isa_tracker);
                     filter by status, expiry_date, owner.
  bdc.alerts       — Active ISA/boundary alerts (bd_alerts); filter by
                     alert_type, severity, acknowledged.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _bdc_conn(conn: Any) -> tuple[Any, bool]:
    """Return (connection, owned) — owned=True means caller must close it."""
    if conn is not None:
        return conn, False
    from tools.boundary_canvas.db.init_db import get_connection  # noqa: PLC0415
    return get_connection(), True


def designs_adapter(conn: Any) -> list[dict]:
    """Return rows from boundary_designs."""
    c, owned = _bdc_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, name, description, classification, template_id, "
            "created_at, updated_at FROM boundary_designs"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def assessments_adapter(conn: Any) -> list[dict]:
    """Return rows from bd_assessments enriched with design name."""
    c, owned = _bdc_conn(conn)
    try:
        cur = c.execute(
            "SELECT a.id, a.design_id, d.name AS design_name, a.assessment_type, "
            "a.score, a.grade, a.cat1_findings, a.cat2_findings, a.cat3_findings, "
            "a.created_at "
            "FROM bd_assessments a "
            "LEFT JOIN boundary_designs d ON d.id = a.design_id"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def isas_adapter(conn: Any) -> list[dict]:
    """Return rows from bd_isa_tracker."""
    c, owned = _bdc_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, design_id, interconnection_id, isa_doc_id, "
            "status, expiry_date, review_date, owner, notes, "
            "created_at, updated_at FROM bd_isa_tracker"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def alerts_adapter(conn: Any) -> list[dict]:
    """Return rows from bd_alerts."""
    c, owned = _bdc_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, design_id, isa_id, alert_type, severity, "
            "days_until_expiry, message, acknowledged, acknowledged_by, "
            "created_at FROM bd_alerts"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def ai_decisions_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return AI decision records for BDC from canvas_ai_decisions (main icdev.db)."""
    try:
        from tools.db.storage import get_connection as _main_conn  # noqa: PLC0415
        with _main_conn() as _c:
            cur = _c.execute(
                "SELECT * FROM canvas_ai_decisions WHERE canvas_type='bdc' ORDER BY created_at DESC"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


register_collection("bdc.designs", designs_adapter)
register_collection("bdc.assessments", assessments_adapter)
register_collection("bdc.isas", isas_adapter)
register_collection("bdc.alerts", alerts_adapter)
register_collection("bdc.ai_decisions", ai_decisions_adapter)
