# CUI // SP-CTI
"""IQE Agentic AI Design Canvas collection adapters.

Importing this module registers three collections on the module-level Executor:
  aadc.designs     — Agentic AI designs (aadc_designs); filter by
                     classification, domain, autonomy_max, hitl_required.
  aadc.assessments — Assessment results (aadc_assessments); filter by
                     design_id, score, omb_compliant, safety_impacting.
  aadc.artifacts   — Generated ATO/compliance artifacts (aadc_artifacts);
                     filter by artifact_type, design_id.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _aadc_conn(conn: Any) -> tuple[Any, bool]:
    if conn is not None:
        return conn, False
    from tools.agentic_ai_canvas.db.init_db import get_connection  # noqa: PLC0415
    return get_connection(), True


def designs_adapter(conn: Any) -> list[dict]:
    """Return rows from aadc_designs."""
    c, owned = _aadc_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, name, description, domain, classification, "
            "autonomy_max, safety_impacting, rights_impacting, hitl_required, "
            "created_at, updated_at FROM aadc_designs"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def assessments_adapter(conn: Any) -> list[dict]:
    """Return rows from aadc_assessments enriched with design name."""
    c, owned = _aadc_conn(conn)
    try:
        cur = c.execute(
            "SELECT a.id, a.design_id, d.name AS design_name, "
            "a.score, a.nist_rmf_score, a.owasp_score, a.omb_compliant, "
            "a.autonomy_max, a.safety_impacting, a.rights_impacting, a.created_at "
            "FROM aadc_assessments a "
            "LEFT JOIN aadc_designs d ON d.id = a.design_id"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def artifacts_adapter(conn: Any) -> list[dict]:
    """Return rows from aadc_artifacts."""
    c, owned = _aadc_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, design_id, artifact_type, title, created_at "
            "FROM aadc_artifacts"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def ai_decisions_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return AI decision records for AADC from canvas_ai_decisions (main icdev.db)."""
    try:
        from tools.db.storage import get_connection as _main_conn  # noqa: PLC0415
        with _main_conn() as _c:
            cur = _c.execute(
                "SELECT * FROM canvas_ai_decisions WHERE canvas_type='aadc' ORDER BY created_at DESC"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


register_collection("aadc.designs", designs_adapter)
register_collection("aadc.assessments", assessments_adapter)
register_collection("aadc.artifacts", artifacts_adapter)
register_collection("aadc.ai_decisions", ai_decisions_adapter)
