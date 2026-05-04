# CUI // SP-CTI — NDC Network Path Analyzer
"""BFS-based path engine for Network Design Canvas topology graphs.

find_paths() discovers all simple paths between two nodes, applies ACL
filtering per edge, and returns a structured reachability verdict.
"""
from __future__ import annotations

from collections import deque
from typing import Any


def _resolve_node_id(query: str, nodes: dict) -> str | None:
    from tools.network.twin import _resolve_node_id as _twin_resolve
    return _twin_resolve(query, nodes)


def _get_edge_endpoints(edge: dict) -> tuple[str | None, str | None]:
    src = edge.get("source") or edge.get("src")
    dst = edge.get("target") or edge.get("dst")
    return src, dst


def _is_acl_blocked(edges: list[dict]) -> bool:
    """Return True if any edge in the path has an ACL deny rule."""
    for edge in edges:
        acl_rules = edge.get("acl_rules")
        if not acl_rules:
            continue
        if isinstance(acl_rules, bool):
            if acl_rules:
                return True
            continue
        if isinstance(acl_rules, list):
            for rule in acl_rules:
                if isinstance(rule, dict):
                    action = rule.get("action", "").lower()
                    if action in ("deny", "block", "drop", "reject"):
                        return True
    return False


def _extract_protocols(edges: list[dict]) -> list[str]:
    seen: set[str] = set()
    for edge in edges:
        proto = edge.get("protocol") or edge.get("protocols")
        if isinstance(proto, str) and proto:
            seen.add(proto)
        elif isinstance(proto, list):
            seen.update(p for p in proto if isinstance(p, str) and p)
    return sorted(seen)


def _build_adjacency(edges: list[dict]) -> dict[str, list[tuple[str, dict]]]:
    adj: dict[str, list[tuple[str, dict]]] = {}
    for edge in edges:
        src, dst = _get_edge_endpoints(edge)
        if not src or not dst:
            continue
        adj.setdefault(src, []).append((dst, edge))
        adj.setdefault(dst, []).append((src, edge))
    return adj


def find_paths(
    src_id: str,
    dst_id: str,
    graph: dict[str, Any],
    max_depth: int = 10,
) -> dict[str, Any]:
    """Find all simple paths between src_id and dst_id in graph.

    Args:
        src_id: Source node ID (fuzzy-resolved if not found directly).
        dst_id: Destination node ID (fuzzy-resolved if not found directly).
        graph: Dict with "nodes" (dict) and "edges" (list).
        max_depth: Maximum number of hops allowed per path.

    Returns:
        {src, dst, paths: [{hops, hop_count, acl_blocked, protocols}],
         reachable, blocked_by_acl, path_count}
    """
    nodes: dict = graph.get("nodes", {})
    edges: list[dict] = graph.get("edges", [])

    src = src_id if src_id in nodes else _resolve_node_id(src_id, nodes)
    dst = dst_id if dst_id in nodes else _resolve_node_id(dst_id, nodes)

    base = {"src": src or src_id, "dst": dst or dst_id}

    if not src or not dst:
        return {**base, "paths": [], "reachable": False, "blocked_by_acl": False, "path_count": 0}

    if src == dst:
        return {**base, "paths": [{"hops": [src], "hop_count": 0, "acl_blocked": False, "protocols": []}],
                "reachable": True, "blocked_by_acl": False, "path_count": 1}

    adj = _build_adjacency(edges)

    # BFS queue: (current_node, path, path_edges, visited)
    queue: deque[tuple[str, list[str], list[dict], frozenset[str]]] = deque()
    queue.append((src, [src], [], frozenset({src})))

    found_paths: list[tuple[list[str], list[dict]]] = []

    while queue:
        current, path, path_edges, visited = queue.popleft()

        if len(path) - 1 >= max_depth:
            continue

        for neighbor, edge in adj.get(current, []):
            if neighbor in visited:
                continue

            new_path = path + [neighbor]
            new_edges = path_edges + [edge]

            if neighbor == dst:
                found_paths.append((new_path, new_edges))
            else:
                queue.append((neighbor, new_path, new_edges, visited | {neighbor}))

    processed: list[dict] = []
    any_open = False
    any_acl_blocked = False

    for hops, hop_edges in found_paths:
        acl_blocked = _is_acl_blocked(hop_edges)
        protocols = _extract_protocols(hop_edges)
        if acl_blocked:
            any_acl_blocked = True
        else:
            any_open = True
        processed.append({
            "hops": hops,
            "hop_count": len(hops) - 1,
            "acl_blocked": acl_blocked,
            "protocols": protocols,
        })

    return {
        "src": src,
        "dst": dst,
        "paths": processed,
        "reachable": any_open,
        "blocked_by_acl": any_acl_blocked,
        "path_count": len(processed),
    }
