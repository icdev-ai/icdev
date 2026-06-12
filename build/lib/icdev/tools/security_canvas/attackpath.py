# CUI // SP-CTI
"""ICDEV™ Security Design Canvas — Attack Path Twin data helpers (dt-sdc-twin-07).

Pure functions that read `sdc_attack_snapshots` and return structured summary
dicts for the /security/attackpath dashboard page.

No Flask, no LLM — deterministic reads only.
"""
from __future__ import annotations

import json
from typing import Any


def get_attackpath_summary(conn: Any) -> dict:
    """Return a summary dict from all sdc_attack_snapshots rows.

    Args:
        conn: A DB connection with row-dict access (sqlite3.Row or StorageConnection).

    Returns::

        {
            "total_snapshots": int,
            "total_nodes": int,
            "total_edges": int,
            "max_risk_score": int | float,
            "snapshots": [
                {
                    "id": str,
                    "component_id": str,
                    "created_at": str,
                    "node_count": int,
                    "edge_count": int,
                    "max_risk_score": int | float,
                    "nodes": [...],
                    "edges": [...],
                }
            ]
        }
    """
    cur = conn.execute(
        "SELECT id, component_id, nodes_json, edges_json, created_at "
        "FROM sdc_attack_snapshots ORDER BY created_at DESC"
    )
    col_names = [d[0] for d in cur.description]
    rows = cur.fetchall()

    total_nodes = 0
    total_edges = 0
    max_risk = 0
    snapshots: list[dict] = []

    for raw in rows:
        row = dict(zip(col_names, raw))
        try:
            nodes = json.loads(row.get("nodes_json") or "[]")
        except (ValueError, TypeError):
            nodes = []
        try:
            edges = json.loads(row.get("edges_json") or "[]")
        except (ValueError, TypeError):
            edges = []

        snap_max_risk = 0
        for edge in edges:
            rs = edge.get("risk_score", 0)
            try:
                rs = float(rs)
            except (TypeError, ValueError):
                rs = 0
            if rs > snap_max_risk:
                snap_max_risk = rs
            if rs > max_risk:
                max_risk = rs

        total_nodes += len(nodes)
        total_edges += len(edges)

        snapshots.append(
            {
                "id": row["id"],
                "component_id": row["component_id"],
                "created_at": row.get("created_at", ""),
                "node_count": len(nodes),
                "edge_count": len(edges),
                "max_risk_score": snap_max_risk,
                "nodes": nodes,
                "edges": edges,
            }
        )

    return {
        "total_snapshots": len(snapshots),
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "max_risk_score": max_risk,
        "snapshots": snapshots,
    }


def enumerate_paths(snapshots: list[dict]) -> list[dict]:
    """BFS path enumeration across all snapshot edges combined.

    Returns a list of ``{"src": str, "goal": str, "path": [str, ...], "hops": int}``
    for every source→leaf pair reachable in the merged adjacency graph.
    Capped at 200 paths to avoid runaway expansion on dense graphs.
    """
    from collections import deque

    adj: dict[str, list[str]] = {}
    for snap in snapshots:
        for edge in snap.get("edges", []):
            s = edge.get("source") or edge.get("src")
            t = edge.get("target") or edge.get("goal")
            if s and t:
                adj.setdefault(s, []).append(t)

    if not adj:
        return []

    all_nodes = set(adj.keys()) | {n for nbrs in adj.values() for n in nbrs}
    leaf_nodes = {n for n in all_nodes if n not in adj}

    results: list[dict] = []
    for src in adj:
        queue: deque[tuple[str, list[str]]] = deque([(src, [src])])
        while queue and len(results) < 200:
            node, path = queue.popleft()
            if node in leaf_nodes or node not in adj:
                if len(path) > 1:
                    results.append(
                        {
                            "src": src,
                            "goal": node,
                            "path": path,
                            "hops": len(path) - 1,
                        }
                    )
            else:
                for nxt in adj.get(node, []):
                    if nxt not in path:
                        queue.append((nxt, path + [nxt]))

    return results[:200]
