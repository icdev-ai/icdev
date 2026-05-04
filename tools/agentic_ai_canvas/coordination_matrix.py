# CUI // SP-CTI
"""Agentic AI Design Canvas — Multi-agent coordination matrix (Phase 3).

Builds an N×N matrix showing how agents communicate:
  direct   — direct edge between two agents
  indirect — two-hop path through a non-agent intermediary
  none     — no reachability

Classifies overall topology: mesh / hub-spoke / pipeline / hierarchical / single / none.
"""

from __future__ import annotations

from tools.agentic_ai_canvas.constants import AGENT_NODES


def build_coordination_matrix(nodes: list[dict], edges: list[dict]) -> dict:
    """Return coordination matrix, topology label, hub nodes, and isolated agents."""
    agent_nodes = [n for n in nodes if n.get("type", "") in AGENT_NODES]
    if not agent_nodes:
        return {
            "agents": [],
            "matrix": [],
            "topology": "none",
            "hub_nodes": [],
            "isolated_agents": [],
        }

    agent_ids = {a["id"] for a in agent_nodes}

    # Direct agent-to-agent edges
    direct_pairs: set[tuple[str, str]] = set()
    all_adj: dict[str, list[str]] = {}
    for e in edges:
        s, t = e.get("source", ""), e.get("target", "")
        all_adj.setdefault(s, []).append(t)
        if s in agent_ids and t in agent_ids:
            direct_pairs.add((s, t))

    # Indirect: agent → non-agent → agent (two-hop)
    indirect_pairs: set[tuple[str, str]] = set()
    for a in agent_ids:
        for mid in all_adj.get(a, []):
            if mid not in agent_ids:
                for b in all_adj.get(mid, []):
                    if b in agent_ids and b != a and (a, b) not in direct_pairs:
                        indirect_pairs.add((a, b))

    # Build N×N matrix
    matrix: list[list[str]] = []
    for row in agent_nodes:
        row_data: list[str] = []
        for col in agent_nodes:
            if row["id"] == col["id"]:
                row_data.append("self")
            elif (row["id"], col["id"]) in direct_pairs:
                row_data.append("direct")
            elif (row["id"], col["id"]) in indirect_pairs:
                row_data.append("indirect")
            else:
                row_data.append("none")
        matrix.append(row_data)

    n = len(agent_nodes)
    topology = _classify_topology(agent_nodes, direct_pairs, indirect_pairs, n)

    # Hub = highest out-degree agent
    out_degree: dict[str, int] = {a["id"]: 0 for a in agent_nodes}
    for s, _ in direct_pairs:
        out_degree[s] = out_degree.get(s, 0) + 1
    max_deg = max(out_degree.values()) if out_degree else 0
    hub_ids = {aid for aid, deg in out_degree.items() if deg == max_deg and deg > 0}
    hub_nodes = [{"id": a["id"], "label": a.get("label", a["id"])} for a in agent_nodes if a["id"] in hub_ids]

    # Isolated = no direct edges at all
    connected_ids = {s for s, _ in direct_pairs} | {t for _, t in direct_pairs}
    isolated = [{"id": a["id"], "label": a.get("label", a["id"])} for a in agent_nodes if a["id"] not in connected_ids]

    return {
        "agents": [{"id": a["id"], "label": a.get("label", a["id"]), "type": a.get("type", "")} for a in agent_nodes],
        "matrix": matrix,
        "topology": topology,
        "hub_nodes": hub_nodes,
        "isolated_agents": isolated,
    }


def _classify_topology(
    agent_nodes: list[dict],
    direct_pairs: set[tuple[str, str]],
    indirect_pairs: set[tuple[str, str]],
    n: int,
) -> str:
    if n == 0:
        return "none"
    if n == 1:
        return "single"

    all_pairs = direct_pairs | indirect_pairs
    # Fully connected mesh
    if len(all_pairs) >= n * (n - 1):
        return "mesh"

    # Hub-spoke: any node connects directly to n-1 others
    out_counts: dict[str, int] = {}
    for s, _ in direct_pairs:
        out_counts[s] = out_counts.get(s, 0) + 1
    if any(c >= n - 1 for c in out_counts.values()):
        return "hub-spoke"

    # Pipeline: each agent has at most 1 outgoing direct edge
    if all(c <= 1 for c in out_counts.values()):
        return "pipeline"

    return "hierarchical"
