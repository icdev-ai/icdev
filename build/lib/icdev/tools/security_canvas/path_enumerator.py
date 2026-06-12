# [CUI // SP-CTI]
"""ICDEV™ Security Design Canvas — Minimum-cost path enumerator.

Dijkstra-style K-shortest simple paths from a source node to a goal node
in an SDC attack graph.  Cross-IL traversal is blocked by default.
"""
from __future__ import annotations

import heapq
import itertools
from typing import Any

# DoD Impact Level ordering for cross-IL detection
_IL_ORDER: dict[str, int] = {
    "IL0": 0,
    "IL2": 2,
    "IL4": 4,
    "IL5": 5,
    "IL6": 6,
    "SECRET": 6,  # nosec B105 — classification label, not a password
}

_DEFAULT_EDGE_COST = 1.0


def _node_il(node: dict[str, Any]) -> str | None:
    """Extract il_level from a node dict (top-level or inside config)."""
    return node.get("il_level") or node.get("config", {}).get("il_level")


def _build_adj(
    graph: dict[str, Any],
) -> tuple[dict[str, list[tuple[str, float]]], dict[str, str | None]]:
    """Return (adjacency_list, node_il_map).

    adjacency_list: node_id -> [(neighbor_id, edge_cost)]
    node_il_map:    node_id -> il_level string or None
    """
    il_map: dict[str, str | None] = {}
    for node in graph.get("nodes", []):
        il_map[node["id"]] = _node_il(node)

    # Propagate boundary il_level to contained assets that have no il_level yet
    for boundary in graph.get("boundaries", []):
        il = boundary.get("il_level")
        if il:
            for asset_id in boundary.get("contained_assets", []):
                if il_map.get(asset_id) is None:
                    il_map[asset_id] = il

    adj: dict[str, list[tuple[str, float]]] = {}
    for edge in graph.get("edges", []):
        src = edge.get("source") or edge.get("src")
        dst = edge.get("target") or edge.get("dst")
        if not src or not dst:
            continue
        cost = float(edge.get("cost", _DEFAULT_EDGE_COST))
        adj.setdefault(src, []).append((dst, cost))

    return adj, il_map


def _cross_il(il_a: str | None, il_b: str | None) -> bool:
    """Return True when the two IL levels are known and differ."""
    if not il_a or not il_b:
        return False
    return _IL_ORDER.get(il_a, -1) != _IL_ORDER.get(il_b, -1)


def enumerate(  # noqa: A001 — intentional shadowing of builtin for DSL clarity
    graph: dict[str, Any],
    src: str,
    goal: str,
    k: int = 5,
    allow_cross_il: bool = False,
) -> list[dict[str, Any]]:
    """Return up to *k* minimum-cost simple paths from *src* to *goal*.

    Each entry in the returned list is::

        {"path": [node_id, ...], "cost": float}

    The list is sorted ascending by cost.  Cycles are prevented by tracking
    the set of visited nodes per in-progress path.  Cross-IL edges are
    silently skipped unless *allow_cross_il* is True.

    Returns an empty list when no path exists.
    """
    if src == goal:
        return [{"path": [src], "cost": 0.0}]

    adj, il_map = _build_adj(graph)

    results: list[dict[str, Any]] = []
    # heap entries: (cost, tie_breaker, path_list, visited_frozenset)
    _tie = itertools.count()
    heap: list[tuple[float, int, list[str], frozenset[str]]] = [
        (0.0, next(_tie), [src], frozenset({src}))
    ]

    while heap and len(results) < k:
        cost, _, path, visited = heapq.heappop(heap)
        current = path[-1]

        if current == goal:
            results.append({"path": list(path), "cost": cost})
            continue

        for neighbor, edge_cost in adj.get(current, []):
            if neighbor in visited:
                # Cycle — skip to keep paths simple
                continue
            if not allow_cross_il and _cross_il(il_map.get(current), il_map.get(neighbor)):
                # Cross-IL traversal blocked
                continue
            heapq.heappush(
                heap,
                (
                    cost + edge_cost,
                    next(_tie),
                    path + [neighbor],
                    visited | {neighbor},
                ),
            )

    return results
