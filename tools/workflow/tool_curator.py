# [TEMPLATE: CUI // SP-CTI]
"""Tool Curation Enforcement — restrict available tools per goal phase.

Adapted from the Agent Harness pattern of phase-level tool curation:
each phase receives only the tools it needs, reducing token overhead and
preventing off-script tool calls.

FORGE goals already *suggest* tools per step, but don't *enforce* it.
This module adds programmatic enforcement: given a goal name and phase,
it returns the allowed tool list and can validate whether a tool call
is permitted.

Usage:
    python tools/workflow/tool_curator.py --goal build_app --phase testing --json
    python tools/workflow/tool_curator.py --validate --goal build_app --phase testing --tool test_orchestrator
    python tools/workflow/tool_curator.py --list-goals --json

Architecture Decision:
    Curated tool sets are defined in args/tool_curation.yaml.  If no
    curation config exists for a goal/phase, all tools are allowed
    (permissive fallback — never blocks unexpectedly).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

CONFIG_PATH = BASE_DIR / "args" / "tool_curation.yaml"


def _load_config() -> Dict[str, Any]:
    """Load tool curation configuration from YAML."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml

        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {}


def get_curated_tools(goal: str, phase: str, config: Dict[str, Any] = None) -> Optional[List[str]]:
    """Return the list of allowed tools for a goal phase.

    Args:
        goal: Goal name (e.g. "build_app", "compliance_scan").
        phase: Phase/step within the goal (e.g. "testing", "deploy").
        config: Optional override config; loaded from YAML if None.

    Returns:
        List of allowed tool names, or None if no curation is defined
        (meaning all tools are permitted).
    """
    cfg = config or _load_config()
    goals = cfg.get("goals", {})
    goal_cfg = goals.get(goal, {})
    phases = goal_cfg.get("phases", {})
    phase_cfg = phases.get(phase, {})
    tools = phase_cfg.get("tools")
    if tools is None:
        # Check for goal-level default tools
        tools = goal_cfg.get("default_tools")
    return tools


def validate_tool(goal: str, phase: str, tool: str, config: Dict[str, Any] = None) -> Tuple[bool, str]:
    """Check whether a tool is allowed for the given goal phase.

    Args:
        goal: Goal name.
        phase: Phase/step name.
        tool: Tool name to validate.
        config: Optional override config.

    Returns:
        (allowed: bool, reason: str).
    """
    curated = get_curated_tools(goal, phase, config)
    if curated is None:
        return True, "no_curation_defined"
    if tool in curated:
        return True, "tool_in_curated_set"

    # Check wildcard patterns (e.g. "compliance/*")
    for pattern in curated:
        if pattern.endswith("/*"):
            prefix = pattern[:-2]
            if tool.startswith(prefix):
                return True, f"tool_matches_pattern:{pattern}"

    return False, f"tool_not_in_curated_set_for_{goal}/{phase}"


def get_phase_context(goal: str, phase: str, config: Dict[str, Any] = None) -> Dict[str, Any]:
    """Return full phase context including tools, description, and constraints.

    Args:
        goal: Goal name.
        phase: Phase/step name.
        config: Optional override config.

    Returns:
        Dict with tools, description, phase_type, and enforcement mode.
    """
    cfg = config or _load_config()
    goals = cfg.get("goals", {})
    goal_cfg = goals.get(goal, {})
    phases = goal_cfg.get("phases", {})
    phase_cfg = phases.get(phase, {})

    tools = get_curated_tools(goal, phase, cfg)
    enforcement = cfg.get("enforcement", "warn")  # "warn", "block", "off"

    return {
        "goal": goal,
        "phase": phase,
        "tools": tools,
        "tool_count": len(tools) if tools else None,
        "description": phase_cfg.get("description", ""),
        "phase_type": phase_cfg.get("type", "llm_agent"),
        "enforcement": enforcement,
        "curated": tools is not None,
        "classification": "CUI",
    }


def list_goals(config: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """List all goals with curation defined."""
    cfg = config or _load_config()
    goals = cfg.get("goals", {})
    result = []
    for goal_name, goal_cfg in goals.items():
        phases = goal_cfg.get("phases", {})
        result.append(
            {
                "goal": goal_name,
                "description": goal_cfg.get("description", ""),
                "phase_count": len(phases),
                "phases": list(phases.keys()),
                "default_tools": goal_cfg.get("default_tools"),
            }
        )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ICDEV™ Tool Curation Enforcement — phase-level tool restriction")
    parser.add_argument("--goal", help="Goal name")
    parser.add_argument("--phase", help="Phase/step within the goal")
    parser.add_argument("--tool", help="Tool name to validate (with --validate)")
    parser.add_argument("--validate", action="store_true", help="Validate whether a tool is allowed")
    parser.add_argument("--list-goals", action="store_true", help="List all goals with curation defined")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.list_goals:
        goals = list_goals()
        if args.json:
            print(json.dumps({"goals": goals, "classification": "CUI"}, indent=2))
        else:
            for g in goals:
                print(f"{g['goal']}: {g['phase_count']} phases — {g['description']}")
                for p in g["phases"]:
                    print(f"  - {p}")
        return

    if args.validate:
        if not all([args.goal, args.phase, args.tool]):
            print("ERROR: --validate requires --goal, --phase, and --tool", file=sys.stderr)
            sys.exit(1)
        allowed, reason = validate_tool(args.goal, args.phase, args.tool)
        if args.json:
            print(
                json.dumps(
                    {
                        "allowed": allowed,
                        "reason": reason,
                        "goal": args.goal,
                        "phase": args.phase,
                        "tool": args.tool,
                        "classification": "CUI",
                    },
                    indent=2,
                )
            )
        else:
            status = "ALLOWED" if allowed else "BLOCKED"
            print(f"{status}: {args.tool} in {args.goal}/{args.phase} ({reason})")
        sys.exit(0 if allowed else 1)

    if args.goal and args.phase:
        ctx = get_phase_context(args.goal, args.phase)
        if args.json:
            print(json.dumps(ctx, indent=2))
        else:
            print(f"Goal: {ctx['goal']} / Phase: {ctx['phase']}")
            print(f"Enforcement: {ctx['enforcement']}")
            print(f"Curated: {ctx['curated']}")
            if ctx["tools"]:
                print(f"Allowed tools ({ctx['tool_count']}):")
                for t in ctx["tools"]:
                    print(f"  - {t}")
            else:
                print("All tools allowed (no curation defined)")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
