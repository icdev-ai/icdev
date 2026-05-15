
"""
Step 1: Amazon Strands Agents — Tool Decorator + Agent Pattern
Goal: Implement the @tool decorator and StrandsAgent that uses registered tools.
"""

import inspect
from typing import Callable, Any


# ── @tool decorator (Strands pattern) ────────────────────────────────────────

_REGISTERED_TOOLS: dict[str, dict] = {}
_TOOL_HANDLERS: dict[str, Callable] = {}

TYPE_MAP = {
    str: "string", int: "integer", float: "number",
    bool: "boolean", list: "array", dict: "object",
}


def tool(func: Callable) -> Callable:
    """TODO: Implement the @tool decorator (Strands-style).

    When applied to a function:
    1. Read the function name, docstring, and parameter annotations
    2. Build a tool_def dict: {"name": ..., "description": ..., "inputSchema": {"type": "object", "properties": {...}}}
    3. For each parameter (excluding 'return'), map its annotation using TYPE_MAP
       (default to "string" if no annotation)
    4. Register in _REGISTERED_TOOLS and _TOOL_HANDLERS
    5. Return the function unchanged

    Use inspect.signature(func).parameters to get parameter info.
    Use inspect.get_annotations(func) or func.__annotations__ for types.
    """
    # YOUR CODE HERE
    return func


# ── Tool implementations (decorated) ─────────────────────────────────────────

@tool
def scan_system(system_name: str, scan_depth: str) -> dict:
    """Scan a system for STIG compliance issues at the specified depth."""
    depths = {"quick": 10, "standard": 50, "full": 200}
    controls_checked = depths.get(scan_depth, 50)
    cat1_count = 2 if scan_depth != "quick" else 0
    return {
        "system": system_name,
        "controls_checked": controls_checked,
        "cat1_findings": cat1_count,
        "scan_depth": scan_depth,
        "status": "CRITICAL" if cat1_count > 0 else "ACCEPTABLE",
    }


@tool
def create_remediation_plan(system_name: str, finding_count: int) -> str:
    """Create a prioritized remediation plan for open findings."""
    urgency = "URGENT" if finding_count > 0 else "ROUTINE"
    return (
        f"Remediation Plan — {system_name} ({urgency})\n"
        f"1. Prioritize {finding_count} CAT I findings (30-day SLA)\n"
        f"2. Schedule configuration review with system owner\n"
        f"3. Submit POA&M entries to ISSO within 5 business days\n"
        f"4. Verify fixes and re-scan to confirm closure"
    )


@tool
def notify_isso(system_name: str, finding_summary: str) -> bool:
    """Send notification to ISSO with finding summary (simulated)."""
    print(f"[notify] ISSO notified for {system_name}: {finding_summary[:60]}")
    return True


# ── Strands-style Agent ───────────────────────────────────────────────────────

class StrandsAgent:
    """Simplified Strands-style agent — routes tasks to registered tools."""

    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt

    def _decide_tools(self, task: str) -> list[tuple[str, dict]]:
        """Simulate LLM tool selection — returns list of (tool_name, args) to call."""
        task_lower = task.lower()
        calls = []
        if "scan" in task_lower or "assessment" in task_lower:
            system = "ICDEV-Prod" if "prod" in task_lower else "ICDEV-Dev"
            depth = "full" if "full" in task_lower else "standard"
            calls.append(("scan_system", {"system_name": system, "scan_depth": depth}))
        if "plan" in task_lower or "remediat" in task_lower or calls:
            system = "ICDEV-Prod" if "prod" in task_lower else "ICDEV-Dev"
            calls.append(("create_remediation_plan", {"system_name": system, "finding_count": 2}))
        if "notif" in task_lower or "isso" in task_lower or calls:
            system = "ICDEV-Prod" if "prod" in task_lower else "ICDEV-Dev"
            calls.append(("notify_isso", {"system_name": system, "finding_summary": "2 CAT I findings require immediate attention."}))
        return calls

    def run(self, task: str) -> dict:
        """TODO: Run the agent loop.

        1. Call self._decide_tools(task) to get the list of tool calls
        2. For each (tool_name, args): look up in _TOOL_HANDLERS and call it
        3. Collect results in a list
        4. Return dict: {"task": task, "tools_called": [...names...], "results": [...results...], "steps": len(results)}

        Print each step: "→ Calling <tool_name>(<args>)"
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    agent = StrandsAgent(
        system_prompt="You are a STIG compliance analyst for a DoD IL4 system."
    )
    print("=== Tool Registry ===")
    for name, t in _REGISTERED_TOOLS.items():
        props = list(t["inputSchema"]["properties"].keys())
        print(f"  @tool {name}({', '.join(props)})")

    print("\n=== Agent Run ===")
    result = agent.run("Run a full compliance assessment for ICDEV-Prod and create a remediation plan.")
    if result:
        print(f"Tools called: {result['tools_called']}")
        print(f"Steps completed: {result['steps']}")
