# CUI // SP-CTI
"""Agentic AI Design Canvas — Architectural Pattern Detector (Phase 8).

Recognizes named AI architectural patterns from the node/edge graph of a design.
8 patterns: BASIC_RAG, AGENTIC_RAG, AUTONOMOUS_AGENT, HITL_SUPERVISED,
MULTI_AGENT_ORCHESTRATOR, SAFETY_FIRST, PIPELINE_CHAIN, COGNITIVE_ARCHITECTURE.
"""

from __future__ import annotations

from tools.agentic_ai_canvas.constants import (
    AGENT_NODES,
    MEMORY_NODES,
    MODEL_NODES,
    SAFETY_NODES,
    TOOL_MCP_NODES,
)

_RAG_TYPES = {"vector-db", "knowledge-graph", "doc-store", "rag-retriever"}
_ORCHESTRATOR_TYPES = {"orchestrator"}
_PLANNING_TYPES = {"planner", "reasoner", "reflection-agent"}
_GUARD_TYPES = SAFETY_NODES
_HITL_TYPES = {"hitl-gate", "approval-workflow"}
_LLM_TYPES = MODEL_NODES

_PATTERNS: list[dict] = [
    {
        "id": "BASIC_RAG",
        "name": "Basic RAG",
        "description": "LLM retrieval-augmented generation with knowledge base; no autonomous agents.",
        "required": ["llm_present", "rag_present"],
        "bonus": ["has_guard"],
        "penalty": ["has_agent"],
        "missing_suggests": ["input-sanitizer", "output-validator", "pii-detector"],
    },
    {
        "id": "AGENTIC_RAG",
        "name": "Agentic RAG",
        "description": "RAG pipeline driven by one or more agents with tool use.",
        "required": ["llm_present", "rag_present", "has_agent"],
        "bonus": ["has_hitl", "has_guard"],
        "penalty": [],
        "missing_suggests": ["hitl-gate", "circuit-breaker"],
    },
    {
        "id": "AUTONOMOUS_AGENT",
        "name": "Autonomous Agent",
        "description": "One or more agents with tools; high autonomy (L3+) and no HITL supervision.",
        "required": ["has_agent", "has_tool", "high_autonomy"],
        "bonus": ["has_guard"],
        "penalty": ["has_hitl"],
        "missing_suggests": ["hitl-gate", "circuit-breaker", "audit-logger"],
    },
    {
        "id": "HITL_SUPERVISED",
        "name": "HITL-Supervised",
        "description": "Agent(s) with mandatory human-in-the-loop gates before consequential actions.",
        "required": ["has_agent", "has_hitl"],
        "bonus": ["has_guard", "has_audit"],
        "penalty": [],
        "missing_suggests": ["audit-logger", "output-validator"],
    },
    {
        "id": "MULTI_AGENT_ORCHESTRATOR",
        "name": "Multi-Agent Orchestrator",
        "description": "Two or more agents coordinated by an orchestrator/supervisor node.",
        "required": ["multi_agent", "has_orchestrator"],
        "bonus": ["has_hitl", "has_guard"],
        "penalty": [],
        "missing_suggests": ["hitl-gate", "circuit-breaker", "trace-collector"],
    },
    {
        "id": "SAFETY_FIRST",
        "name": "Safety-First",
        "description": "Safety nodes and guardrails outnumber or equal all other node types.",
        "required": ["safety_dominant"],
        "bonus": ["has_audit", "has_hitl"],
        "penalty": [],
        "missing_suggests": [],
    },
    {
        "id": "PIPELINE_CHAIN",
        "name": "Pipeline Chain",
        "description": "Sequential chain of LLM calls or tool steps with no autonomous agents.",
        "required": ["llm_present", "chain_present"],
        "bonus": ["has_guard"],
        "penalty": ["has_agent"],
        "missing_suggests": ["input-sanitizer", "output-validator"],
    },
    {
        "id": "COGNITIVE_ARCHITECTURE",
        "name": "Cognitive Architecture",
        "description": "Full cognitive loop: memory, planning, agents, and reflection nodes.",
        "required": ["has_agent", "has_memory", "has_planning"],
        "bonus": ["has_hitl", "has_guard"],
        "penalty": [],
        "missing_suggests": ["hitl-gate", "trace-collector", "circuit-breaker"],
    },
]


def detect_patterns(nodes: list[dict], edges: list[dict]) -> dict:
    """
    Detect named AI architectural patterns in a design.

    Returns:
        {
          patterns: [{id, name, confidence, matched_flags, missing_nodes, description}],
          dominant: str (top pattern id or 'UNCLASSIFIED'),
          flags: dict of boolean structural flags
        }
    """
    flags = _compute_flags(nodes, edges)
    results = []
    for p in _PATTERNS:
        conf, matched, missing = _score_pattern(p, flags, nodes)
        results.append({
            "id": p["id"],
            "name": p["name"],
            "description": p["description"],
            "confidence": conf,
            "matched_flags": matched,
            "missing_nodes": missing,
        })

    results.sort(key=lambda x: x["confidence"], reverse=True)
    dominant = results[0]["id"] if results and results[0]["confidence"] >= 40 else "UNCLASSIFIED"

    return {
        "patterns": results,
        "dominant": dominant,
        "flags": flags,
    }


def _compute_flags(nodes: list[dict], edges: list[dict]) -> dict:
    types = {n.get("type", "") for n in nodes}
    agent_count = sum(1 for n in nodes if n.get("type") in AGENT_NODES)
    llm_count = sum(1 for n in nodes if n.get("type") in _LLM_TYPES)
    guard_count = sum(1 for n in nodes if n.get("type") in _GUARD_TYPES)
    safety_count = sum(1 for n in nodes if n.get("type") in SAFETY_NODES)
    total_non_safety = max(1, len(nodes) - safety_count - guard_count)

    max_autonomy = 0
    for n in nodes:
        lvl = n.get("autonomy_level") or n.get("properties", {}).get("autonomy_level", 0)
        try:
            max_autonomy = max(max_autonomy, int(lvl))
        except (TypeError, ValueError):
            pass

    return {
        "llm_present": llm_count > 0,
        "rag_present": bool(types & _RAG_TYPES),
        "has_agent": agent_count > 0,
        "multi_agent": agent_count >= 2,
        "has_tool": bool(types & TOOL_MCP_NODES),
        "has_hitl": bool(types & _HITL_TYPES),
        "has_guard": guard_count > 0,
        "has_audit": "audit-logger" in types,
        "has_memory": bool(types & MEMORY_NODES),
        "has_planning": bool(types & _PLANNING_TYPES),
        "has_orchestrator": bool(types & _ORCHESTRATOR_TYPES),
        "high_autonomy": max_autonomy >= 3,
        "chain_present": llm_count >= 2 or (llm_count >= 1 and len(edges) >= 2),
        "safety_dominant": (safety_count + guard_count) >= total_non_safety,
    }


def _score_pattern(pattern: dict, flags: dict, nodes: list[dict]) -> tuple[int, list[str], list[str]]:
    required = pattern["required"]
    bonus_keys = pattern["bonus"]
    penalty_keys = pattern["penalty"]
    missing_suggests = pattern["missing_suggests"]

    met = [r for r in required if flags.get(r)]
    if len(met) < len(required):
        pct_met = len(met) / max(1, len(required))
        return max(0, int(pct_met * 30)), met, _nodes_missing(missing_suggests, nodes)

    base = 60
    for b in bonus_keys:
        if flags.get(b):
            base += 10
    for p in penalty_keys:
        if flags.get(p):
            base -= 15

    return max(0, min(100, base)), met, _nodes_missing(missing_suggests, nodes)


def _nodes_missing(suggests: list[str], nodes: list[dict]) -> list[str]:
    types = {n.get("type", "") for n in nodes}
    return [s for s in suggests if s not in types]
