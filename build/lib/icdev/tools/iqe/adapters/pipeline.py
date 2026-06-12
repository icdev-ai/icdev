# CUI // SP-CTI
"""IQE pipeline collection adapters.

Importing this module registers three collections on the module-level Executor:
  pipeline.snapshots — Pipeline DAG snapshots (pipeline_snapshots table);
                       filter by pipeline_id or snapshot_type (status).
  pipeline.nodes     — Flattened node list from snapshots (one row per node);
                       filter by pipeline_id or node_type.
  pipeline.edges     — Flattened edge list from snapshots (one row per edge);
                       filter by pipeline_id or source/target node id.
"""
from __future__ import annotations

import json
from typing import Any

from tools.iqe.executor import register_collection


def snapshots_adapter(conn: Any) -> list[dict]:
    """Return rows from pipeline_snapshots."""
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, pipeline_id, snapshot_type, label, "
        "nodes_json, edges_json, meta_json, created_by, created_at "
        "FROM pipeline_snapshots"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def nodes_adapter(conn: Any) -> list[dict]:
    """Return one row per node across all pipeline_snapshots.

    Each row merges snapshot metadata (snapshot_id, pipeline_id,
    snapshot_type, created_at) with the node's own fields.  The JSON
    'type' key is surfaced as 'node_type' so callers can filter with
    ``WHERE r.node_type == 'build-runner'`` without clashing with
    SQL/IQE reserved words.
    """
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, pipeline_id, snapshot_type, nodes_json, created_at "
        "FROM pipeline_snapshots"
    )
    cols = [d[0] for d in cur.description]
    rows: list[dict] = []
    for raw in cur.fetchall():
        snap = dict(zip(cols, raw))
        try:
            nodes = json.loads(snap["nodes_json"] or "[]")
        except (ValueError, TypeError):
            nodes = []
        for node in nodes:
            row: dict = {
                "snapshot_id": snap["id"],
                "pipeline_id": snap["pipeline_id"],
                "snapshot_type": snap["snapshot_type"],
                "created_at": snap["created_at"],
            }
            node_copy = dict(node)
            if "type" in node_copy:
                row["node_type"] = node_copy.pop("type")
            row.update(node_copy)
            rows.append(row)
    return rows


def edges_adapter(conn: Any) -> list[dict]:
    """Return one row per edge across all pipeline_snapshots.

    Each row merges snapshot metadata (snapshot_id, pipeline_id,
    snapshot_type, created_at) with the edge's own fields (source, target,
    and any additional edge attributes).
    """
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, pipeline_id, snapshot_type, edges_json, created_at "
        "FROM pipeline_snapshots"
    )
    cols = [d[0] for d in cur.description]
    rows: list[dict] = []
    for raw in cur.fetchall():
        snap = dict(zip(cols, raw))
        try:
            edges = json.loads(snap["edges_json"] or "[]")
        except (ValueError, TypeError):
            edges = []
        for edge in edges:
            row: dict = {
                "snapshot_id": snap["id"],
                "pipeline_id": snap["pipeline_id"],
                "snapshot_type": snap["snapshot_type"],
                "created_at": snap["created_at"],
            }
            row.update(edge)
            rows.append(row)
    return rows


def ai_decisions_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return AI decision records for PDC from canvas_ai_decisions (main icdev.db)."""
    try:
        from tools.db.storage import get_connection as _main_conn  # noqa: PLC0415
        with _main_conn() as _c:
            cur = _c.execute(
                "SELECT * FROM canvas_ai_decisions WHERE canvas_type='pdc' ORDER BY created_at DESC"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


register_collection("pipeline.snapshots", snapshots_adapter)
register_collection("pipeline.nodes", nodes_adapter)
register_collection("pipeline.edges", edges_adapter)
register_collection("pipeline.ai_decisions", ai_decisions_adapter)
