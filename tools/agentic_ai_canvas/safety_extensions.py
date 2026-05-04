# CUI // SP-CTI
"""Agentic AI Design Canvas — Safety extension checks (Phase 4).

Rules:
  - Trusted monitor: if any autonomous-agent exists, a trusted-monitor must be present (MNG-3).
  - PII field detector: if pii-field-detector is used, it must connect to redaction-engine (MNG-4).
"""

from __future__ import annotations

from tools.agentic_ai_canvas.constants import P4_SAFETY_EXT_CHECKS


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


def check_safety_extensions(nodes: list[dict],
                              edges: list[dict]) -> tuple[list[dict], float]:
    """Evaluate Phase 4 safety extension checks.

    Returns (findings, score_pct). Returns ([], None) if neither
    autonomous-agent nor pii-field-detector are present.
    """
    node_map = {n["id"]: n for n in nodes}
    types = {n.get("type", "") for n in nodes}

    has_autonomous = bool(types & {"autonomous-agent"})
    has_pii_field = "pii-field-detector" in types

    if not has_autonomous and not has_pii_field:
        return [], None  # type: ignore[return-value]

    adj = _build_adjacency(edges)
    findings: list[dict] = []
    total_weight = sum(c["weight"] for c in P4_SAFETY_EXT_CHECKS)
    passed_weight = 0.0

    for check in P4_SAFETY_EXT_CHECKS:
        passed = False

        if check["check"] == "autonomous_agent_has_trusted_monitor":
            if not has_autonomous:
                passed = True
            else:
                # trusted-monitor must be present somewhere in the design
                # (it's an out-of-band monitor — doesn't need to be directly downstream)
                passed = "trusted-monitor" in types

        elif check["check"] == "pii_field_wired_to_redaction":
            if not has_pii_field:
                passed = True
            else:
                pii_ids = [n["id"] for n in nodes if n.get("type") == "pii-field-detector"]
                passed = all(
                    "redaction-engine" in {node_map.get(d, {}).get("type")
                                           for d in _reachable(pid, adj)}
                    for pid in pii_ids
                )

        if passed:
            passed_weight += check["weight"]
        else:
            findings.append({
                "id": f"p4-{check['id']}",
                "framework": "NIST AI RMF (Phase 4)",
                "function": check["function"],
                "category": check["category"],
                "severity": "HIGH" if check["weight"] >= 10 else "MEDIUM",
                "title": check["title"],
                "detail": check["description"],
                "recommendation": f"See check {check['id']} remediation guidance.",
            })

    score = round((passed_weight / total_weight) * 100, 1) if total_weight else 0.0
    return findings, score
