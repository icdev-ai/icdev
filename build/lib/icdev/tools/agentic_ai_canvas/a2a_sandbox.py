# CUI // SP-CTI
"""Agentic AI Design Canvas — A2A bridge + sandbox-exec assessment (Phase 4).

Rules:
  - Every a2a-bridge must have an audit-logger downstream (GOV-3).
  - Every sandbox-exec must have a guardrail or input-sanitizer upstream (GOV-4).
"""

from __future__ import annotations

from tools.agentic_ai_canvas.constants import P4_A2A_CHECKS


def _build_adjacency(edges: list[dict]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for e in edges:
        adj.setdefault(e.get("source", ""), set()).add(e.get("target", ""))
    return adj


def _build_reverse_adjacency(edges: list[dict]) -> dict[str, set[str]]:
    radj: dict[str, set[str]] = {}
    for e in edges:
        radj.setdefault(e.get("target", ""), set()).add(e.get("source", ""))
    return radj


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


def check_a2a_sandbox(nodes: list[dict],
                       edges: list[dict]) -> tuple[list[dict], float]:
    """Evaluate Phase 4 A2A + sandbox checks.

    Returns (findings, score_pct). If neither a2a-bridge nor sandbox-exec
    nodes are present, returns ([], None) — not penalised.
    """
    node_map = {n["id"]: n for n in nodes}
    types = {n.get("type", "") for n in nodes}

    has_a2a = "a2a-bridge" in types
    has_sandbox = "sandbox-exec" in types

    if not has_a2a and not has_sandbox:
        return [], None  # type: ignore[return-value]

    adj = _build_adjacency(edges)
    radj = _build_reverse_adjacency(edges)

    findings: list[dict] = []
    total_weight = sum(c["weight"] for c in P4_A2A_CHECKS)
    passed_weight = 0.0

    for check in P4_A2A_CHECKS:
        passed = False

        if check["check"] == "a2a_bridge_has_audit_logger":
            if not has_a2a:
                passed = True
            else:
                a2a_ids = [n["id"] for n in nodes if n.get("type") == "a2a-bridge"]
                passed = all(
                    "audit-logger" in {node_map.get(d, {}).get("type")
                                       for d in _reachable(aid, adj)}
                    for aid in a2a_ids
                )

        elif check["check"] == "sandbox_exec_has_guardrail":
            if not has_sandbox:
                passed = True
            else:
                sb_ids = [n["id"] for n in nodes if n.get("type") == "sandbox-exec"]
                guard_types = {"guardrail", "input-sanitizer"}
                passed = all(
                    bool({node_map.get(u, {}).get("type")
                          for u in radj.get(sid, set())} & guard_types)
                    for sid in sb_ids
                )

        if passed:
            passed_weight += check["weight"]
        else:
            findings.append({
                "id": f"p4-{check['id']}",
                "framework": "NIST AI RMF (Phase 4)",
                "function": check["function"],
                "category": check["category"],
                "severity": "HIGH",
                "title": check["title"],
                "detail": check["description"],
                "recommendation": f"See check {check['id']} remediation guidance.",
            })

    score = round((passed_weight / total_weight) * 100, 1) if total_weight else 0.0
    return findings, score
