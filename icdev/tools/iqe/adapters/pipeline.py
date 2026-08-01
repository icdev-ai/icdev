# CUI // SP-CTI
"""IQE pipeline collection adapters.

Importing this module registers three collections on the module-level Executor:
  pipeline.snapshots — Pipeline DAG snapshots (pdc_snapshots canvas table);
                       filter by pipeline_id.
  pipeline.nodes     — Flattened node list from snapshots (one row per node);
                       filter by pipeline_id or node_type.
  pipeline.edges     — Flattened edge list from snapshots (one row per edge);
                       filter by pipeline_id or source/target node id.

Snapshots live in ``pdc_snapshots`` in the pipeline canvas DB (written by
``tools.pipeline.twin.take_snapshot``). When called with ``conn=None`` (the
production IQE dispatch path), each adapter opens the canvas connection via
``tools.pipeline.db.init_db.get_connection`` — which already disables RLS — so
IQE returns real data instead of reading the empty shared ``pipeline_snapshots``
table (pdx-data-01). Every adapter tolerates a missing table on a fresh DB and
returns ``[]`` rather than raising (mirrors ``ai_decisions_adapter``).

Each snapshot's graph is stored as a single ``graph_json`` blob of shape
``{"nodes": [...], "edges": [...]}``; the node/edge adapters parse it out.
"""
from __future__ import annotations

import json
from typing import Any

from tools.iqe.executor import register_collection


def _canvas_conn() -> Any:
    """Open the pipeline canvas connection (RLS already disabled)."""
    from tools.pipeline.db.init_db import get_connection  # noqa: PLC0415
    return get_connection()


def snapshots_adapter(conn: Any) -> list[dict]:
    """Return rows from pdc_snapshots (canvas twin snapshot store)."""
    opened = False
    if conn is None:
        conn = _canvas_conn()
        opened = True
    try:
        cur = conn.execute(
            "SELECT id, pipeline_id, label, graph_json, "
            "node_count, edge_count, created_by, created_at "
            "FROM pdc_snapshots"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:  # noqa: BLE001 — table may not exist on a fresh DB
        return []
    finally:
        if opened:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def nodes_adapter(conn: Any) -> list[dict]:
    """Return one row per node across all pdc_snapshots.

    Each row merges snapshot metadata (snapshot_id, pipeline_id, created_at)
    with the node's own fields. The JSON 'type' key is surfaced as 'node_type'
    so callers can filter with ``WHERE r.node_type == 'build-runner'`` without
    clashing with SQL/IQE reserved words.
    """
    opened = False
    if conn is None:
        conn = _canvas_conn()
        opened = True
    try:
        cur = conn.execute(
            "SELECT id, pipeline_id, graph_json, created_at FROM pdc_snapshots"
        )
        cols = [d[0] for d in cur.description]
        raw_rows = cur.fetchall()
    except Exception:  # noqa: BLE001 — table may not exist on a fresh DB
        return []
    finally:
        if opened:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    rows: list[dict] = []
    for raw in raw_rows:
        snap = dict(zip(cols, raw))
        try:
            graph = json.loads(snap["graph_json"] or "{}")
            nodes = graph.get("nodes") or []
        except (ValueError, TypeError):
            nodes = []
        for node in nodes:
            row: dict = {
                "snapshot_id": snap["id"],
                "pipeline_id": snap["pipeline_id"],
                "created_at": snap["created_at"],
            }
            node_copy = dict(node)
            if "type" in node_copy:
                row["node_type"] = node_copy.pop("type")
            row.update(node_copy)
            rows.append(row)
    return rows


def edges_adapter(conn: Any) -> list[dict]:
    """Return one row per edge across all pdc_snapshots.

    Each row merges snapshot metadata (snapshot_id, pipeline_id, created_at)
    with the edge's own fields (source, target, and any additional attributes).
    """
    opened = False
    if conn is None:
        conn = _canvas_conn()
        opened = True
    try:
        cur = conn.execute(
            "SELECT id, pipeline_id, graph_json, created_at FROM pdc_snapshots"
        )
        cols = [d[0] for d in cur.description]
        raw_rows = cur.fetchall()
    except Exception:  # noqa: BLE001 — table may not exist on a fresh DB
        return []
    finally:
        if opened:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    rows: list[dict] = []
    for raw in raw_rows:
        snap = dict(zip(cols, raw))
        try:
            graph = json.loads(snap["graph_json"] or "{}")
            edges = graph.get("edges") or []
        except (ValueError, TypeError):
            edges = []
        for edge in edges:
            row: dict = {
                "snapshot_id": snap["id"],
                "pipeline_id": snap["pipeline_id"],
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
