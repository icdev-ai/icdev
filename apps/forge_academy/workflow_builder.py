# CUI // SP-CTI
"""FORGE Academy workflow builder — thin wrapper over visual_agent_builder."""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def build_workflow(name: str, nodes: list, edges: list, user_email: str) -> dict:
    """Convert node+edge graph from the UI into a FORGE goal markdown + design record."""
    canvas_json = {"nodes": nodes, "edges": edges, "version": 1}
    try:
        from tools.aisg.visual_agent_builder import create_design, generate_goal_md
        goal_md = generate_goal_md(canvas_json, name)
        design_id = create_design(name, canvas_json, user_email, "suggest")
        return {
            "status": "ok",
            "design_id": design_id,
            "goal_md": goal_md,
            "node_count": len(nodes),
            "edge_count": len(edges),
        }
    except Exception as exc:
        _log.warning("workflow_builder failed: %s", exc)
        return {
            "status": "partial",
            "design_id": None,
            "goal_md": _mock_goal_md(name, nodes),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "note": str(exc),
        }


def _mock_goal_md(name: str, nodes: list) -> str:
    lines = [f"# Goal: {name}", "", "## Steps"]
    for i, node in enumerate(nodes, 1):
        label = node.get("label", node.get("id", f"step-{i}"))
        lines.append(f"{i}. {label}")
    return "\n".join(lines)


def get_node_templates() -> list:
    """Return node palette for the workflow canvas UI."""
    return [
        {"type": "trigger", "label": "Trigger", "icon": "⚡", "color": "#FF6B35"},
        {"type": "llm_call", "label": "LLM Call", "icon": "🧠", "color": "#4a90d9"},
        {"type": "rag_retrieve", "label": "RAG Retrieve", "icon": "🔍", "color": "#00D4FF"},
        {"type": "tool_call", "label": "Tool Call", "icon": "🔧", "color": "#00FF88"},
        {"type": "decision", "label": "Decision", "icon": "◆", "color": "#9b59b6"},
        {"type": "output", "label": "Output", "icon": "📤", "color": "#FFB800"},
        {"type": "memory_read", "label": "Memory Read", "icon": "💾", "color": "#6c6c80"},
        {"type": "memory_write", "label": "Memory Write", "icon": "📝", "color": "#6c6c80"},
        {"type": "api_call", "label": "API Call", "icon": "🌐", "color": "#4a90d9"},
        {"type": "agent", "label": "Sub-Agent", "icon": "🤖", "color": "#FF6B35"},
    ]
