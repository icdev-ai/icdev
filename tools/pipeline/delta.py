# CUI // SP-CTI — PDC Pipeline Twin: structural delta computer
"""Compute structural diff between two pipeline DAG snapshots.

Snapshots are read from the canonical twin snapshot store, ``pdc_snapshots``
in the pipeline canvas DB (written by ``tools.pipeline.twin.take_snapshot`` on
every save). This module reads through the canvas connection
(``tools.pipeline.db.init_db.get_connection``), which already disables RLS —
``pdc_snapshots`` has no ``tenant_id``/``classification`` columns, so the shared
``get_connection`` would attach the global row-level predicate and raise
``UndefinedColumn`` (pdx-data-01).

Public API
----------
compute_delta(snapshot_id_a, snapshot_id_b) -> delta dict
    Given two snapshot IDs (a = baseline, b = proposed), return:
    {
        "snapshot_a": str,
        "snapshot_b": str,
        "nodes": {"added": [...], "removed": [...], "modified": [...]},
        "edges": {"added": [...], "removed": [...], "modified": [...]},
    }
    All output lists are sorted by stable ID for deterministic ordering.
    Nodes with the same 'id' but changed properties appear in 'modified'
    (stable-ID matching handles what would otherwise look like rename pairs).
"""
from __future__ import annotations

import json
from typing import Any


def _edge_key(edge: dict) -> str:
    """Stable key for an edge: explicit 'id' preferred, else 'source-->target'."""
    return edge.get("id") or f"{edge.get('source', '')}-->{edge.get('target', '')}"


def _diff_collection(
    baseline: list[dict],
    proposed: list[dict],
    *,
    key_fn,
) -> dict[str, list]:
    b = {key_fn(x): x for x in baseline}
    p = {key_fn(x): x for x in proposed}

    added = sorted([p[k] for k in p if k not in b], key=key_fn)
    removed = sorted([b[k] for k in b if k not in p], key=key_fn)
    modified = sorted(
        [{"baseline": b[k], "proposed": p[k]} for k in b if k in p and b[k] != p[k]],
        key=lambda x: key_fn(x["proposed"]),
    )
    return {"added": added, "removed": removed, "modified": modified}


def _load_snapshot(snapshot_id: str) -> dict[str, Any] | None:
    """Return a snapshot's graph as ``{"nodes": [...], "edges": [...]}``, or None.

    Reads ``graph_json`` from ``pdc_snapshots`` (the canvas twin snapshot store)
    via the canvas connection, which already disables RLS. The stored
    ``graph_json`` is a single ``{"nodes": [...], "edges": [...]}`` blob, so the
    nodes/edges lists are parsed out of it here.
    """
    from tools.pipeline.db.init_db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT graph_json FROM pdc_snapshots WHERE id = %s",
            (snapshot_id,),
        ).fetchone()
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — cached/shared canvas conns may no-op close
            pass

    if row is None:
        return None
    graph = json.loads(row["graph_json"] or '{"nodes": [], "edges": []}')
    return {"nodes": graph.get("nodes") or [], "edges": graph.get("edges") or []}


def compute_delta(snapshot_id_a: str, snapshot_id_b: str) -> dict[str, Any]:
    """Return structural diff between baseline snapshot a and proposed snapshot b.

    Raises:
        ValueError: If either snapshot ID is not found in the DB.
    """
    snap_a = _load_snapshot(snapshot_id_a)
    if snap_a is None:
        raise ValueError(f"Snapshot not found: {snapshot_id_a!r}")
    snap_b = _load_snapshot(snapshot_id_b)
    if snap_b is None:
        raise ValueError(f"Snapshot not found: {snapshot_id_b!r}")

    nodes_a: list[dict] = snap_a["nodes"]
    edges_a: list[dict] = snap_a["edges"]
    nodes_b: list[dict] = snap_b["nodes"]
    edges_b: list[dict] = snap_b["edges"]

    return {
        "snapshot_a": snapshot_id_a,
        "snapshot_b": snapshot_id_b,
        "nodes": _diff_collection(nodes_a, nodes_b, key_fn=lambda n: n["id"]),
        "edges": _diff_collection(edges_a, edges_b, key_fn=_edge_key),
    }
