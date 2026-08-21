# CUI // SP-CTI
"""Agentic AI Design Canvas — Safety redundancy graph analysis (Phase 3).

Computes which agent nodes have at least one safety or governance predecessor.
Protected  = agent reachable from a safety/governance node.
Unprotected = agent with no safety node anywhere upstream.
"""

from __future__ import annotations

from tools.agentic_ai_canvas.constants import AGENT_NODES, GOVERNANCE_NODES, SAFETY_NODES

_PROTECTIVE_TYPES = SAFETY_NODES | GOVERNANCE_NODES


def analyze_safety_redundancy(nodes: list[dict], edges: list[dict]) -> dict:
    """Return safety coverage metrics for all agent nodes in the design."""
    radj: dict[str, list[str]] = {}
    for e in edges:
        radj.setdefault(e.get("target", ""), []).append(e.get("source", ""))

    safety_ids = {n["id"] for n in nodes if n.get("type", "") in _PROTECTIVE_TYPES}
    agent_nodes = [n for n in nodes if n.get("type", "") in AGENT_NODES]

    protected: list[dict] = []
    unprotected: list[dict] = []
    coverage_map: dict[str, list[str]] = {}

    for agent in agent_nodes:
        aid = agent["id"]
        visited: set[str] = set()
        queue = [aid]
        protecting: list[str] = []
        while queue:
            current = queue.pop(0)
            for pred_id in radj.get(current, []):
                if pred_id in visited:
                    continue
                visited.add(pred_id)
                if pred_id in safety_ids:
                    protecting.append(pred_id)
                else:
                    queue.append(pred_id)
        coverage_map[aid] = protecting
        if protecting:
            protected.append(agent)
        else:
            unprotected.append(agent)

    total = len(agent_nodes)
    # NOT ASSESSED, never 100.0 (rem-hyg-13). `total` is the number of agent
    # nodes on the design, so zero means the design has no agents to protect —
    # an EMPTY canvas, not a fully-redundant one. The old `else 100.0` reported
    # perfect safety redundancy for a design nobody had drawn yet.
    score = round(len(protected) / total * 100, 1) if total > 0 else None

    return {
        "score": score,
        "protected_agents": [_agent_dict(a) for a in protected],
        "unprotected_agents": [_agent_dict(a) for a in unprotected],
        "coverage_map": coverage_map,
        "safety_chains": _find_safety_chains(nodes, edges, safety_ids),
        "total_agents": total,
    }


def _agent_dict(n: dict) -> dict:
    return {"id": n["id"], "label": n.get("label", n["id"]), "type": n.get("type", "")}


def _find_safety_chains(
    nodes: list[dict], edges: list[dict], safety_ids: set[str]
) -> list[list[str]]:
    """Return connected sequences of safety nodes (multi-layer safety pipelines)."""
    adj: dict[str, list[str]] = {}
    for e in edges:
        s, t = e.get("source", ""), e.get("target", "")
        if s in safety_ids and t in safety_ids:
            adj.setdefault(s, []).append(t)

    visited: set[str] = set()
    chains: list[list[str]] = []
    for nid in safety_ids:
        if nid in visited:
            continue
        chain: list[str] = []
        queue = [nid]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            chain.append(current)
            queue.extend(adj.get(current, []))
        if len(chain) > 1:
            chains.append(chain)
    return chains
