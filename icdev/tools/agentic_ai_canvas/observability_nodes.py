# CUI // SP-CTI
"""Agentic AI Design Canvas — Observability node assessment rules (Phase 4).

Checks that trace-collector, span-recorder, and metrics-emitter nodes are
properly wired. Inspired by Haystack ProxyTracer and OpenTelemetry patterns.
"""

from __future__ import annotations

from tools.agentic_ai_canvas.constants import (
    AGENT_NODES,
    OBSERVABILITY_NODES,
    P4_OBSERVABILITY_CHECKS,
)


def _node_types(nodes: list[dict]) -> set[str]:
    return {n.get("type", "") for n in nodes}


def _build_adjacency(edges: list[dict]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for e in edges:
        adj.setdefault(e.get("source", ""), set()).add(e.get("target", ""))
    return adj


def _reachable(start: str, adj: dict[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    stack = [start]
    while stack:
        n = stack.pop()
        if n in visited:
            continue
        visited.add(n)
        stack.extend(adj.get(n, set()) - visited)
    return visited


def check_observability_coverage(nodes: list[dict],
                                  edges: list[dict]) -> tuple[list[dict], float]:
    """Evaluate Phase 4 observability checks.

    Returns (findings, score_pct) where score is 0-100.
    Only scored when observability nodes are present; otherwise returns ([], None).
    """
    types = _node_types(nodes)
    has_obs = bool(types & OBSERVABILITY_NODES)

    # If no observability nodes at all, skip scoring (not penalised)
    if not has_obs:
        return [], None  # type: ignore[return-value]

    findings: list[dict] = []
    total_weight = sum(c["weight"] for c in P4_OBSERVABILITY_CHECKS)
    passed_weight = 0.0

    for check in P4_OBSERVABILITY_CHECKS:
        passed = bool(types & set(check["required_any"]))
        if passed:
            passed_weight += check["weight"]
        else:
            findings.append({
                "id": f"p4-{check['id']}",
                "framework": "NIST AI RMF (Phase 4)",
                "function": check["function"],
                "category": check["category"],
                "severity": "MEDIUM",
                "title": check["title"],
                "detail": check["description"],
                "recommendation": f"Add a {check['required_any'][0]} node to the design.",
            })

    score = round((passed_weight / total_weight) * 100, 1) if total_weight else 0.0
    return findings, score


def get_observability_coverage_map(nodes: list[dict],
                                    edges: list[dict]) -> list[dict]:
    """Return per-agent observability coverage (which agents have trace/span nodes downstream)."""
    adj = _build_adjacency(edges)
    node_map = {n["id"]: n for n in nodes}
    result = []

    for node in nodes:
        if node.get("type") not in AGENT_NODES:
            continue
        reachable = _reachable(node["id"], adj)
        reachable_types = {node_map.get(r, {}).get("type") for r in reachable}
        result.append({
            "agent_id": node["id"],
            "agent_label": node.get("label", node["id"]),
            "has_trace": "trace-collector" in reachable_types,
            "has_span": "span-recorder" in reachable_types,
            "has_metrics": "metrics-emitter" in reachable_types,
        })
    return result
