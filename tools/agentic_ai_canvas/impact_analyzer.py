# CUI // SP-CTI
"""Agentic AI Design Canvas — Cascade Impact Analyzer (Phase 8).

For each node in a design, computes:
  - blast_radius: how many nodes are downstream (direct + transitive)
  - is_spof: single point of failure (all paths flow through it)
  - vulnerability_score: based on node type risk profile
  - resilience_reduction: % drop in overall resilience if node removed

Summary: resilience_score, critical_nodes, spofs, overall_risk_level.
"""

from __future__ import annotations

from tools.agentic_ai_canvas.constants import (
    GOVERNANCE_NODES,
    MODEL_NODES,
    SAFETY_NODES,
    TOOL_MCP_NODES,
)

_HIGH_RISK_TYPES = MODEL_NODES | {"orchestrator", "autonomous-agent", "sub-agent",
                                   "semi-auto-agent", "researcher-agent", "writer-agent",
                                   "analyst-agent", "reviewer-agent"}
_MEDIUM_RISK_TYPES = TOOL_MCP_NODES | {"vector-db", "knowledge-graph", "doc-store"}
_LOW_RISK_TYPES = SAFETY_NODES | GOVERNANCE_NODES | {"audit-logger", "trace-collector",
                                                      "metrics-emitter", "span-recorder"}


def analyze_impact(nodes: list[dict], edges: list[dict]) -> dict:
    """
    Run cascade impact analysis on a design graph.

    Returns:
        {
          node_impacts: [{node_id, label, type, blast_radius, is_spof,
                          vulnerability_score, resilience_reduction}],
          summary: {resilience_score, critical_nodes, spofs,
                    overall_risk_level, node_count, edge_count},
        }
    """
    if not nodes:
        return {"node_impacts": [], "summary": _empty_summary(0, 0)}

    node_ids = {n["id"] for n in nodes}
    adj = _build_adjacency(edges, node_ids)
    rev_adj = _build_adjacency(edges, node_ids, reverse=True)

    total_reachable = _total_reachable_from_all_sources(adj, node_ids)

    node_impacts = []
    for n in nodes:
        nid = n["id"]
        ntype = n.get("type", "")
        label = n.get("label", nid[:8])

        downstream = _reachable(nid, adj)
        blast = len(downstream)

        vuln = _vulnerability_score(ntype)
        spof = _is_spof(nid, node_ids, adj, rev_adj)
        rr = _resilience_reduction(blast, spof, vuln, total_reachable)

        node_impacts.append({
            "node_id": nid,
            "label": label,
            "type": ntype,
            "blast_radius": blast,
            "is_spof": spof,
            "vulnerability_score": vuln,
            "resilience_reduction": rr,
        })

    node_impacts.sort(key=lambda x: (x["blast_radius"], x["vulnerability_score"]), reverse=True)

    spofs = [ni["node_id"] for ni in node_impacts if ni["is_spof"]]
    critical = [ni["node_id"] for ni in node_impacts[:3] if ni["blast_radius"] > 0]

    resilience = _resilience_score(node_impacts, len(nodes))
    risk_level = _risk_level(resilience, len(spofs))

    return {
        "node_impacts": node_impacts,
        "summary": {
            "resilience_score": resilience,
            "critical_nodes": critical,
            "spofs": spofs,
            "overall_risk_level": risk_level,
            "node_count": len(nodes),
            "edge_count": len(edges),
        },
    }


def _build_adjacency(edges: list[dict], node_ids: set, reverse: bool = False) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {nid: set() for nid in node_ids}
    for e in edges:
        src = e.get("source", e.get("from_node", ""))
        tgt = e.get("target", e.get("to_node", ""))
        if src in node_ids and tgt in node_ids:
            if reverse:
                adj[tgt].add(src)
            else:
                adj[src].add(tgt)
    return adj


def _reachable(start: str, adj: dict[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    stack = list(adj.get(start, []))
    while stack:
        nid = stack.pop()
        if nid not in visited:
            visited.add(nid)
            stack.extend(adj.get(nid, []) - visited)
    return visited


def _total_reachable_from_all_sources(adj: dict[str, set[str]], node_ids: set) -> int:
    sources = {nid for nid in node_ids if not any(nid in v for v in adj.values())}
    if not sources:
        sources = node_ids
    total: set[str] = set()
    for s in sources:
        total |= _reachable(s, adj)
    return max(1, len(total))


def _is_spof(nid: str, node_ids: set, adj: dict, rev_adj: dict) -> bool:
    predecessors = rev_adj.get(nid, set())
    successors = adj.get(nid, set())
    if not predecessors or not successors:
        return False
    for pred in predecessors:
        for succ in successors:
            if not _path_exists_without(pred, succ, nid, adj):
                return True
    return False


def _path_exists_without(start: str, end: str, excluded: str, adj: dict) -> bool:
    if start == excluded:
        return False
    visited: set[str] = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        if cur == end:
            return True
        if cur in visited:
            continue
        visited.add(cur)
        for nb in adj.get(cur, []):
            if nb != excluded and nb not in visited:
                stack.append(nb)
    return False


def _vulnerability_score(ntype: str) -> int:
    if ntype in _HIGH_RISK_TYPES:
        return 8
    if ntype in _MEDIUM_RISK_TYPES:
        return 5
    if ntype in _LOW_RISK_TYPES:
        return 1
    return 3


def _resilience_reduction(blast: int, spof: bool, vuln: int, total_reachable: int) -> int:
    base = round((blast / total_reachable) * 100 * (vuln / 8))
    if spof:
        base = min(100, base + 20)
    return min(100, max(0, base))


def _resilience_score(node_impacts: list[dict], total_nodes: int) -> int:
    if not node_impacts or total_nodes == 0:
        return 100
    spof_count = sum(1 for n in node_impacts if n["is_spof"])
    high_vuln = sum(
        1 for n in node_impacts
        if n["vulnerability_score"] >= 8 and n["blast_radius"] >= 2
    )
    spof_penalty = min(40, spof_count * 12)
    vuln_penalty = min(30, high_vuln * 8)
    return max(0, 100 - spof_penalty - vuln_penalty)


def _risk_level(resilience: int, spof_count: int) -> str:
    if resilience < 40 or spof_count >= 3:
        return "CRITICAL"
    if resilience < 60 or spof_count >= 1:
        return "HIGH"
    if resilience < 80:
        return "MEDIUM"
    return "LOW"


def _empty_summary(node_count: int, edge_count: int) -> dict:
    return {
        "resilience_score": 100,
        "critical_nodes": [],
        "spofs": [],
        "overall_risk_level": "LOW",
        "node_count": node_count,
        "edge_count": edge_count,
    }
