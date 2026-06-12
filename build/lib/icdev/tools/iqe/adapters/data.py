# CUI // SP-CTI
"""IQE data collection adapters.

Importing this module registers two collections on the module-level Executor:
  data.lineage.edges   — Lineage edges from dd_lineage; filter by
                         classification or source_node_id.
  data.classifications — Design-level classification assignments from
                         data_designs; filter by classification or id.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def lineage_edges_adapter(conn: Any) -> list[dict]:
    """Return rows from dd_lineage."""
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, design_id, source_node_id, target_node_id, "
        "lineage_type, column_name, transform_desc, classification, created_at "
        "FROM dd_lineage"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def classifications_adapter(conn: Any) -> list[dict]:
    """Return design-level classification assignments from data_designs."""
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, name, classification, created_at "
        "FROM data_designs"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def ai_decisions_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return AI decision records for DDC from canvas_ai_decisions (main icdev.db)."""
    try:
        from tools.db.storage import get_connection as _main_conn  # noqa: PLC0415
        with _main_conn() as _c:
            cur = _c.execute(
                "SELECT * FROM canvas_ai_decisions WHERE canvas_type='ddc' ORDER BY created_at DESC"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


register_collection("data.lineage.edges", lineage_edges_adapter)
register_collection("data.classifications", classifications_adapter)
register_collection("data.ai_decisions", ai_decisions_adapter)


def mapping_sessions_adapter(conn: Any = None) -> list[dict]:
    """Return dd_mapping_sessions rows from data_canvas.db."""
    try:
        from tools.data_canvas.db.init_db import get_connection as _dc_conn  # noqa: PLC0415
        _conn = _dc_conn()
    except Exception:
        if conn is None:
            from tools.db.storage import get_connection  # noqa: PLC0415
            _conn = get_connection()
        else:
            _conn = conn
    try:
        cur = _conn.execute(
            "SELECT id, name, source_format, target_format, status, "
            "field_count, confirmed_count, rejected_count, "
            "classification, tenant_id, created_at, updated_at "
            "FROM dd_mapping_sessions ORDER BY updated_at DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        _conn.close()


def field_mappings_adapter(conn: Any = None) -> list[dict]:
    """Return dd_field_mappings enriched with session name from data_canvas.db."""
    try:
        from tools.data_canvas.db.init_db import get_connection as _dc_conn  # noqa: PLC0415
        _conn = _dc_conn()
    except Exception:
        if conn is None:
            from tools.db.storage import get_connection  # noqa: PLC0415
            _conn = get_connection()
        else:
            _conn = conn
    try:
        cur = _conn.execute(
            "SELECT fm.id, fm.session_id, s.name AS session_name, "
            "fm.source_field, fm.source_type, fm.target_field, fm.target_type, "
            "fm.confidence, fm.match_method, fm.status, fm.transform_expr, "
            "fm.classification, fm.tenant_id, fm.created_at "
            "FROM dd_field_mappings fm "
            "LEFT JOIN dd_mapping_sessions s ON fm.session_id = s.id "
            "ORDER BY fm.confidence DESC LIMIT 1000"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        _conn.close()


register_collection("data.mapping_sessions", mapping_sessions_adapter)
register_collection("data.field_mappings", field_mappings_adapter)
