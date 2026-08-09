"""ICDEV™ Studio — Chat-to-Workflow Engine.

Converts natural language descriptions into workflow YAML using
Ollama/Qwen3 via LLMRouter.  The system prompt includes a compact
tool catalog so the LLM knows valid tool paths and categories.

Usage (API):
    from tools.studio.workflow_chat import generate_workflow_yaml
    result = generate_workflow_yaml("Build a FedRAMP ATO pipeline with ISSO approval")
    if result["status"] == "ok":
        yaml_str = result["yaml"]
"""
# CUI // SP-CTI

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_SYSTEM_PROMPT_TEMPLATE = """\
You are a workflow builder for ICDEV™, a DoD/GovCon AI platform.
Your ONLY output is valid YAML — no markdown fences, no explanation, no extra text.

Available tool categories and representative tools:
{catalog}

YAML schema (use exactly this structure):
description: "<short description of what this workflow does>"
category: "<compliance|security|build|deploy|testing|govcon|mbse|general>"
steps:
  - id: step_1
    name: "<human-readable step name>"
    tool: "<tool path, e.g. tools/compliance/fips199_categorizer.py — or empty for human/approval nodes>"
    depends_on: []
    args: {{}}
    timeout: 300
    required: true
    node_type: "tool"
  - id: step_2
    name: "<name>"
    tool: ""
    depends_on: [step_1]
    node_type: "human"
    role: "isso"
  - id: step_3
    name: "<name>"
    tool: ""
    depends_on: [step_2]
    node_type: "mcp"
    mcp_tool: "<registered MCP tool name, e.g. scan_dependencies>"
    mcp_params: {{}}
  - id: step_4
    name: "<name>"
    tool: ""
    depends_on: [step_3]
    node_type: "agent"
    prompt: "<the task the agent should carry out>"
    agent_tools: [worktree_read]

node_type values: tool, human, approval, mcp, agent
role values (human/approval): stakeholder, program_manager, isso, contracting_officer, developer, reviewer, approver
approval_policy (approval nodes only): any, all, majority
mcp_tool (mcp nodes only): name of a registered MCP tool — required for node_type: mcp
mcp_params (mcp nodes only): mapping of arguments forwarded to that tool
prompt (agent nodes only): the task for the agent — required for node_type: agent
agent_tools (agent nodes only): toolset bundle names bounding what the agent may
  call — required for node_type: agent; use worktree_read for read-only analysis
  and worktree_build when the step must edit files

Rules:
- Each step id must be unique (step_1, step_2, step_3, ...)
- depends_on contains step ids that must complete before this step
- For tool nodes, use a plausible path matching the category — do not invent paths
- Output ONLY the YAML block, starting with "description:"
"""


def _build_catalog_summary() -> str:
    """Return a compact text summary of the tool catalog for the system prompt."""
    try:
        from tools.studio.workflow_editor import get_tool_catalog  # noqa: PLC0415

        catalog = get_tool_catalog()
        lines: list[str] = []
        for cat_key, cat_data in catalog.items():
            label = cat_data.get("label", cat_key)
            tools = cat_data.get("tools", [])
            sample_paths = [t.get("tool", "") for t in tools[:5] if t.get("tool")]
            lines.append(f"  {label}: {', '.join(sample_paths) or '(no paths)'}")
        return "\n".join(lines)
    except Exception:
        return "  (tool catalog unavailable — use generic tool paths)"


def generate_workflow_yaml(user_message: str, conversation_history: list | None = None) -> dict:
    """Call Ollama/Qwen3 to generate workflow YAML from a natural language description.

    Args:
        user_message: Natural language description of the desired workflow.
        conversation_history: Optional prior turns for multi-turn refinement.
            Each item is {"role": "user"|"assistant", "content": "..."}.

    Returns:
        {"status": "ok", "yaml": str, "steps_count": int}
        {"status": "error", "error": str, "raw": str}
    """
    try:
        from tools.llm.provider import LLMRequest  # noqa: PLC0415
        from tools.llm.router import LLMRouter  # noqa: PLC0415

        catalog_summary = _build_catalog_summary()
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(catalog=catalog_summary)

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if conversation_history:
            messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_message})

        router = LLMRouter()
        request = LLMRequest(messages=messages)
        response = router.invoke("chat_response", request)
        raw: str = (response.content if response.content else str(response)).strip()

        # Strip markdown code fences if the LLM added them despite instructions
        if raw.startswith("```"):
            lines = raw.split("\n")
            end = next((i for i in range(len(lines) - 1, 0, -1) if lines[i].startswith("```")), len(lines))
            raw = "\n".join(lines[1:end]).strip()

        data = yaml.safe_load(raw)
        if not isinstance(data, dict) or "steps" not in data:
            return {
                "status": "error",
                "error": "LLM output is not a valid workflow YAML (missing 'steps')",
                "raw": raw,
            }

        return {
            "status": "ok",
            "yaml": raw,
            "steps_count": len(data.get("steps", [])),
            "description": data.get("description", ""),
            "category": data.get("category", "general"),
        }

    except ImportError as exc:
        return {"status": "error", "error": f"LLM module not available: {exc}", "raw": ""}
    except yaml.YAMLError as exc:
        return {"status": "error", "error": f"LLM produced invalid YAML: {exc}", "raw": raw if "raw" in dir() else ""}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "raw": ""}
