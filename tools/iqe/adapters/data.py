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


register_collection("data.lineage.edges", lineage_edges_adapter)
register_collection("data.classifications", classifications_adapter)
