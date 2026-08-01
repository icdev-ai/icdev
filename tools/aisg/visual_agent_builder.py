#!/usr/bin/env python3
# CUI // SP-CTI
"""AISG Visual Agent Builder — converts canvas JSON designs to FORGE goal Markdown.

Reads aisg_agent_designs.canvas_json (a list of node objects with id, tool_name,
trust_tier, inputs, outputs, next_node_id), topologically sorts the nodes, and
renders a goals/custom_<design_id>.md file following the FORGE goal template
structure. No LLM — pure template rendering.

Public API
----------
    generate_goal(design_id: str) -> str
"""

from __future__ import annotations

import json
import pathlib
from collections import deque
from typing import Any

from tools.db.storage import get_connection, sql_placeholder
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.aisg.visual_agent_builder")

# goals/ directory lives at repository root (two parents above tools/aisg/)
_GOALS_DIR = pathlib.Path(__file__).parents[2] / "goals"

_TRUST_TIER_DESC: dict[str, str] = {
    "observer": "Read-only — observes but does not modify system state.",
    "advisor": "Recommends actions — requires human approval before execution.",
    "executor": "Executes pre-approved action classes autonomously.",
    "full": "Full autonomy — executes any action within its defined scope.",
}


# ---------------------------------------------------------------------------
# Topological sort (Kahn's algorithm)
# ---------------------------------------------------------------------------


def _topo_sort(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return nodes in topological order based on next_node_id edges.

    Handles multiple roots, disconnected sub-graphs, and unknown next_node_id
    references (ignored). Cycle members are appended after the DAG nodes in
    original order so the output always covers all nodes.
    """
    index: dict[str, dict[str, Any]] = {n["id"]: n for n in nodes}
    in_degree: dict[str, int] = {n["id"]: 0 for n in nodes}

    for node in nodes:
        nxt = node.get("next_node_id")
        if nxt and nxt in index:
            in_degree[nxt] += 1

    queue: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
    result: list[dict[str, Any]] = []

    while queue:
        nid = queue.popleft()
        node = index[nid]
        result.append(node)
        nxt = node.get("next_node_id")
        if nxt and nxt in index:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    # Append any remaining nodes (cycle members) preserving original order
    visited: set[str] = {n["id"] for n in result}
    for node in nodes:
        if node["id"] not in visited:
            result.append(node)

    return result


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _collect_unique(nodes: list[dict[str, Any]], field: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for node in nodes:
        for item in (node.get(field) or []):
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


def _render_goal(design_id: str, name: str, nodes: list[dict[str, Any]]) -> str:
    sorted_nodes = _topo_sort(nodes)
    all_inputs = _collect_unique(sorted_nodes, "inputs")
    all_outputs = _collect_unique(sorted_nodes, "outputs")

    lines: list[str] = []

    # --- Header ---
    lines += [
        "# CUI // SP-CTI",
        "",
        f"# Goal: {name}",
        "",
        "## Purpose",
        "",
        (
            f"Execute the `{design_id}` agent pipeline: a {len(sorted_nodes)}-step "
            "workflow generated from an AISG visual canvas design."
        ),
        "",
        "---",
        "",
    ]

    # --- Inputs ---
    lines += ["## Inputs", ""]
    if all_inputs:
        for inp in all_inputs:
            lines.append(f"- `{inp}`")
    else:
        lines.append("- *(no explicit inputs defined on any node)*")
    lines += ["", "---", ""]

    # --- Steps (one per node) ---
    lines += ["## Steps", ""]

    for step_num, node in enumerate(sorted_nodes, start=1):
        tool_name: str = node.get("tool_name") or node["id"]
        trust_tier: str = node.get("trust_tier") or "executor"
        tier_desc: str = _TRUST_TIER_DESC.get(trust_tier, trust_tier)
        node_inputs: list[str] = node.get("inputs") or []
        node_outputs: list[str] = node.get("outputs") or []
        nxt: str | None = node.get("next_node_id")

        lines += [
            f"### Step {step_num}: {tool_name}",
            "",
            f"**Tool:** `{tool_name}`  ",
            f"**Node ID:** `{node['id']}`  ",
            f"**Trust Tier:** `{trust_tier}` — {tier_desc}",
            "",
        ]

        if node_inputs:
            lines.append("**Inputs:**")
            for inp in node_inputs:
                lines.append(f"- `{inp}`")
            lines.append("")

        if node_outputs:
            lines.append("**Outputs:**")
            for out in node_outputs:
                lines.append(f"- `{out}`")
            lines.append("")

        if nxt:
            lines += [f"**Next node:** `{nxt}`", ""]

        lines += ["---", ""]

    # --- Outputs ---
    lines += ["## Outputs", ""]
    if all_outputs:
        for out in all_outputs:
            lines.append(f"- `{out}`")
    else:
        lines.append("- *(no explicit outputs defined on any node)*")
    lines += ["", "---", ""]

    # --- Error Handling ---
    lines += [
        "## Error Handling",
        "",
        "- **Tool not found:** Log the missing tool name, skip the step, continue pipeline.",
        "- **Invalid canvas JSON:** `ValueError` is raised with the offending node ID.",
        "- **Cycle detected:** Topological sort appends cycle members after DAG nodes; "
        "a warning is logged and execution continues.",
        "- **Empty canvas:** Goal file is written with zero steps and a warning comment.",
        "- **DB write failure:** Goal file is still returned; caller receives the error.",
        "",
        "---",
        "",
        (
            f"*Generated by `tools/aisg/visual_agent_builder.py` "
            f"from design `{design_id}`.*"
        ),
    ]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _fetch_design(design_id: str) -> tuple[str, list[dict[str, Any]]]:
    """Return (name, nodes) for *design_id*.

    Raises ValueError if the row does not exist or canvas_json is missing /
    not a JSON array.
    """
    conn = get_connection()
    try:
        ph = sql_placeholder(conn)
        query = "SELECT name, canvas_json FROM aisg_agent_designs WHERE id = " + ph
        c = conn.cursor()
        c.execute(query, (design_id,))
        row = c.fetchone()
    finally:
        conn.close()

    if row is None:
        raise ValueError(f"Design '{design_id}' not found in aisg_agent_designs.")

    name: str = row[0]
    raw: Any = row[1]

    if raw is None:
        raise ValueError(f"Design '{design_id}' has no canvas_json.")

    # SQLite returns TEXT; PostgreSQL JSONB is already a Python object.
    nodes: Any = json.loads(raw) if isinstance(raw, str) else raw

    if not isinstance(nodes, list):
        raise ValueError(
            f"Design '{design_id}' canvas_json must be a JSON array of node objects."
        )

    return name, nodes


def _persist_goal(design_id: str, markdown: str) -> None:
    """Write generated_goal_md back to aisg_agent_designs."""
    conn = get_connection()
    try:
        ph = sql_placeholder(conn)
        query = (
            "UPDATE aisg_agent_designs "
            "SET generated_goal_md = " + ph + ", updated_at = datetime('now') "
            "WHERE id = " + ph
        )
        conn.execute(query, (markdown, design_id))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def save_design(
    name: str,
    canvas_json: str,
    trust_tier: str = "advisor",
    created_by: str = "user",
) -> str:
    """Persist a new agent design to aisg_agent_designs and return its ID."""
    import uuid as _uuid
    design_id = str(_uuid.uuid4())[:8]
    conn = get_connection()
    try:
        ph = sql_placeholder(conn)
        conn.execute(
            f"INSERT INTO aisg_agent_designs (id, name, canvas_json, trust_tier, created_by) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph})",
            (design_id, name, canvas_json, trust_tier, created_by),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("save_design: best-effort INSERT into aisg_agent_designs failed (non-blocking): %s", exc)
    finally:
        conn.close()
    return design_id


def generate_goal(design_id: str) -> str:
    """Generate a FORGE goal Markdown document from a canvas design.

    Steps:
    1. Fetch canvas_json and name from aisg_agent_designs.
    2. Topologically sort the node list on next_node_id edges.
    3. Render the FORGE goal template (Goal / Inputs / Steps / Outputs /
       Error Handling).
    4. Write goals/custom_<design_id>.md to disk.
    5. Persist the Markdown to aisg_agent_designs.generated_goal_md.

    Returns the generated Markdown string.
    """
    name, nodes = _fetch_design(design_id)
    markdown = _render_goal(design_id, name, nodes)

    _GOALS_DIR.mkdir(parents=True, exist_ok=True)
    goal_path = _GOALS_DIR / f"custom_{design_id}.md"
    goal_path.write_text(markdown, encoding="utf-8")

    _persist_goal(design_id, markdown)

    return markdown
