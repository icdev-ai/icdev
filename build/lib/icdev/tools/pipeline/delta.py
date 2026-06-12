# CUI // SP-CTI — PDC Pipeline Twin: structural delta computer
"""Compute structural diff between two pipeline DAG snapshots.

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

from tools.pipeline.snapshot_db import get_snapshot


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


def compute_delta(snapshot_id_a: str, snapshot_id_b: str) -> dict[str, Any]:
    """Return structural diff between baseline snapshot a and proposed snapshot b.

    Raises:
        ValueError: If either snapshot ID is not found in the DB.
    """
    snap_a = get_snapshot(snapshot_id_a)
    if snap_a is None:
        raise ValueError(f"Snapshot not found: {snapshot_id_a!r}")
    snap_b = get_snapshot(snapshot_id_b)
    if snap_b is None:
        raise ValueError(f"Snapshot not found: {snapshot_id_b!r}")

    nodes_a: list[dict] = json.loads(snap_a.get("nodes_json") or "[]")
    edges_a: list[dict] = json.loads(snap_a.get("edges_json") or "[]")
    nodes_b: list[dict] = json.loads(snap_b.get("nodes_json") or "[]")
    edges_b: list[dict] = json.loads(snap_b.get("edges_json") or "[]")

    return {
        "snapshot_a": snapshot_id_a,
        "snapshot_b": snapshot_id_b,
        "nodes": _diff_collection(nodes_a, nodes_b, key_fn=lambda n: n["id"]),
        "edges": _diff_collection(edges_a, edges_b, key_fn=_edge_key),
    }
