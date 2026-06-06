# CUI // SP-CTI
"""Agentic AI Design Canvas — Agent behavior simulation engine (Phase 3).

BFS-traces execution from a start node through the design graph.
Halts at HITL gates and circuit-breakers (human-in-the-loop required).
Marks filter nodes (guardrails, PII detectors) as "filtered" but continues.
"""

from __future__ import annotations

from collections import deque

from tools.agentic_ai_canvas.constants import AGENT_NODES, GOVERNANCE_NODES, SAFETY_NODES

_HALT_TYPES = {"hitl-gate", "approval-workflow", "caio-override", "circuit-breaker"}
_FILTER_TYPES = {
    "guardrail", "pii-detector", "toxicity-filter", "input-sanitizer",
    "rate-limiter", "redaction-engine", "pii-field-detector",
}
_ALL_SAFETY = SAFETY_NODES | GOVERNANCE_NODES


def simulate_execution(
    nodes: list[dict],
    edges: list[dict],
    start_node_id: str,
    input_payload: dict | None = None,
    max_steps: int = 50,
) -> dict:
    """
    BFS-trace execution from start_node_id.

    Returns:
        trace        — ordered list of activated steps
        decisions    — list of halt/filter decision points
        halted_by    — node ID that stopped the run (empty if completed)
        steps_count  — number of nodes visited
        status       — 'complete' | 'halted' | 'no-path' | 'error'
    """
    node_by_id = {n["id"]: n for n in nodes}
    if start_node_id not in node_by_id:
        return {
            "trace": [],
            "decisions": [],
            "halted_by": "invalid-start",
            "halted_by_label": "",
            "steps_count": 0,
            "status": "error",
        }

    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(e.get("source", ""), []).append(e.get("target", ""))

    trace: list[dict] = []
    decisions: list[dict] = []
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(start_node_id, 0)])

    while queue and len(trace) < max_steps:
        node_id, depth = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)

        node = node_by_id.get(node_id, {})
        ntype = node.get("type", "")
        nlabel = node.get("label", node_id)

        step: dict = {
            "step": len(trace) + 1,
            "node_id": node_id,
            "node_type": ntype,
            "node_label": nlabel,
            "depth": depth,
            "action": _action_for(ntype),
            "outcome": "activated",
        }

        if ntype in _HALT_TYPES:
            step["outcome"] = "halted"
            decisions.append({
                "step": step["step"],
                "node_id": node_id,
                "node_label": nlabel,
                "type": "halt",
                "decision": "halt",
                "reason": f"{ntype} requires human review — execution suspended.",
            })
            trace.append(step)
            return {
                "trace": trace,
                "decisions": decisions,
                "halted_by": node_id,
                "halted_by_label": nlabel,
                "steps_count": len(trace),
                "status": "halted",
            }

        if ntype in _FILTER_TYPES:
            step["outcome"] = "filtered"
            decisions.append({
                "step": step["step"],
                "node_id": node_id,
                "node_label": nlabel,
                "type": "filter",
                "decision": "proceed",
                "reason": f"{ntype} applied — output sanitized, continuing.",
            })

        trace.append(step)
        for next_id in adj.get(node_id, []):
            if next_id not in visited:
                queue.append((next_id, depth + 1))

    return {
        "trace": trace,
        "decisions": decisions,
        "halted_by": "",
        "halted_by_label": "",
        "steps_count": len(trace),
        "status": "complete" if trace else "no-path",
    }


def _action_for(ntype: str) -> str:
    if ntype in AGENT_NODES:
        return "process"
    if ntype in _FILTER_TYPES:
        return "filter"
    if ntype in _HALT_TYPES:
        return "halt"
    if ntype in _ALL_SAFETY:
        return "check"
    return "pass"
