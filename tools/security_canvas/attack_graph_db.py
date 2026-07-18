# CUI // SP-CTI
"""SDC Attack Graph DB — 6-function CRUD layer for attack_graph_nodes + attack_graph_edges.

Tables are append-only (NIST AU). Nodes represent assets in the attack graph;
edges represent exploitable transitions between assets keyed to MITRE ATT&CK TTPs.

Connection: uses get_canvas_connection() (RLS DISABLED), not the RLS-aware
get_connection(). attack_graph_nodes/attack_graph_edges carry a `classification`
column but NO `tenant_id` column, so the global RLS predicate that
get_connection() attaches inside a Flask request context would inject
`tenant_id = %s` and raise UndefinedColumn on every SELECT (or, when only a
classification is set, silently filter out higher-clearance nodes). These are
ICDEV-migration tables (migration 028) that live in the shared `icdev` database
on both backends, so get_canvas_connection() — with no canvas .db override —
keeps the correct DB target (shared icdev on PG, data/icdev.db on SQLite) while
bypassing RLS, matching the security_canvas RLS-bypass pattern. See shx-db-05.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_canvas_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(v: Any) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return v if v is not None else "[]"


# ── nodes ────────────────────────────────────────────────────────────────────


def add_node(
    asset_id: str,
    classification: str = "CUI",
    value: float = 1.0,
    label: str | None = None,
    meta_json: dict | str | None = None,
) -> str:
    """Insert an attack graph node; returns the new node id."""
    node_id = str(uuid.uuid4())
    with get_canvas_connection() as conn:
        conn.execute(
            "INSERT INTO attack_graph_nodes "
            "(id, asset_id, classification, value, label, meta_json, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",  # nosec B608
            (
                node_id,
                asset_id,
                classification,
                float(value),
                label,
                _dumps(meta_json or {}),
                _now(),
            ),
        )
        conn.commit()
    return node_id


def get_node(node_id: str) -> dict | None:
    """Return a single node by id, or None if not found."""
    with get_canvas_connection() as conn:
        row = conn.execute(
            "SELECT * FROM attack_graph_nodes WHERE id = %s",  # nosec B608
            (node_id,),
        ).fetchone()
    return dict(row) if row else None


def list_nodes(
    asset_id: str | None = None,
    classification: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Return nodes, optionally filtered by asset_id and/or classification."""
    clauses: list[str] = []
    params: list[Any] = []
    if asset_id is not None:
        clauses.append("asset_id = %s")
        params.append(asset_id)
    if classification is not None:
        clauses.append("classification = %s")
        params.append(classification)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM attack_graph_nodes {where} ORDER BY created_at DESC LIMIT %s"  # nosec B608
    params.append(limit)
    with get_canvas_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── edges ────────────────────────────────────────────────────────────────────


def add_edge(
    src_node_id: str,
    dst_node_id: str,
    ttp_id: str,
    cost: float = 1.0,
    prereqs_json: list | str | None = None,
    meta_json: dict | str | None = None,
) -> str:
    """Insert an attack graph edge; returns the new edge id."""
    edge_id = str(uuid.uuid4())
    with get_canvas_connection() as conn:
        conn.execute(
            "INSERT INTO attack_graph_edges "
            "(id, src_node_id, dst_node_id, ttp_id, cost, prereqs_json, meta_json, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",  # nosec B608
            (
                edge_id,
                src_node_id,
                dst_node_id,
                ttp_id,
                float(cost),
                _dumps(prereqs_json if prereqs_json is not None else []),
                _dumps(meta_json or {}),
                _now(),
            ),
        )
        conn.commit()
    return edge_id


def get_edge(edge_id: str) -> dict | None:
    """Return a single edge by id, or None if not found."""
    with get_canvas_connection() as conn:
        row = conn.execute(
            "SELECT * FROM attack_graph_edges WHERE id = %s",  # nosec B608
            (edge_id,),
        ).fetchone()
    return dict(row) if row else None


def list_edges(
    src_node_id: str | None = None,
    dst_node_id: str | None = None,
    ttp_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Return edges, optionally filtered by src, dst, and/or ttp_id."""
    clauses: list[str] = []
    params: list[Any] = []
    if src_node_id is not None:
        clauses.append("src_node_id = %s")
        params.append(src_node_id)
    if dst_node_id is not None:
        clauses.append("dst_node_id = %s")
        params.append(dst_node_id)
    if ttp_id is not None:
        clauses.append("ttp_id = %s")
        params.append(ttp_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM attack_graph_edges {where} ORDER BY created_at DESC LIMIT %s"  # nosec B608
    params.append(limit)
    with get_canvas_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
