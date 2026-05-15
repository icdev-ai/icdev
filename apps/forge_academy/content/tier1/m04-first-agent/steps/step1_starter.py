
from __future__ import annotations
"""
Step 1: The Agent Loop
Goal: Implement run_agent() — a basic decide-act-observe loop using the provided tools.
"""


# ── Tool definitions ────────────────────────────────────────────────────────

def tool_stig_lookup(control_id: str) -> dict:
    """Look up a STIG control by ID."""
    db = {
        "V-220706": {"title": "SSH PermitRootLogin must be disabled", "severity": "CAT1", "fix": "Set PermitRootLogin no in /etc/ssh/sshd_config"},
        "V-220707": {"title": "SSH idle timeout must be set", "severity": "CAT2", "fix": "Set ClientAliveInterval 600 in sshd_config"},
        "AC-2": {"title": "Account Management", "severity": "CAT2", "fix": "Implement account lifecycle management procedures"},
    }
    return db.get(control_id, {"error": f"Control {control_id} not found"})


def tool_calculate_risk(severity: str, open_count: int) -> dict:
    """Calculate overall risk score from findings."""
    weights = {"CAT1": 10, "CAT2": 5, "CAT3": 1, "high": 10, "medium": 5, "low": 1}
    weight = weights.get(severity.upper() if severity.upper() in weights else severity.lower(), 3)
    score = weight * open_count
    if score >= 50:
        level = "CRITICAL"
    elif score >= 20:
        level = "HIGH"
    elif score >= 10:
        level = "MEDIUM"
    else:
        level = "LOW"
    return {"risk_score": score, "risk_level": level, "open_findings": open_count}


def tool_generate_summary(findings: list) -> str:
    """Generate a plain-language summary of findings."""
    cat1 = sum(1 for f in findings if f.get("severity") == "CAT1")
    cat2 = sum(1 for f in findings if f.get("severity") == "CAT2")
    return (f"Security Assessment Summary: {len(findings)} total findings. "
            f"{cat1} critical (CAT I) require immediate remediation. "
            f"{cat2} moderate (CAT II) require remediation within 90 days.")


TOOLS = {
    "stig_lookup": tool_stig_lookup,
    "calculate_risk": tool_calculate_risk,
    "generate_summary": tool_generate_summary,
}


# ── Agent implementation ─────────────────────────────────────────────────────

def decide_tool(task: str, history: list) -> dict | None:
    """Simulate the LLM's tool selection step.

    Returns: {"tool": "tool_name", "args": {...}} or None (task complete).
    This is a rule-based mock — in production this would be an LLM call.
    """
    task_lower = task.lower()
    if not history:
        if "v-220706" in task_lower or "ssh" in task_lower:
            return {"tool": "stig_lookup", "args": {"control_id": "V-220706"}}
        elif "risk" in task_lower or "score" in task_lower:
            return {"tool": "calculate_risk", "args": {"severity": "CAT1", "open_count": 3}}
        elif "summary" in task_lower:
            return {"tool": "generate_summary", "args": {"findings": [
                {"id": "V-220706", "severity": "CAT1"},
                {"id": "V-220707", "severity": "CAT2"},
            ]}}
    elif len(history) == 1:
        # After first tool call, generate summary
        return {"tool": "generate_summary", "args": {"findings": [
            {"id": "V-220706", "severity": "CAT1"},
            {"id": "V-220707", "severity": "CAT2"},
        ]}}
    return None  # Signal: task complete


def run_agent(task: str, max_steps: int = 5) -> str:
    """TODO: Implement the agent loop.

    1. Call decide_tool(task, history) to get the next tool call
    2. If None → return the last result as the final answer
    3. Execute the tool from TOOLS dict with the provided args
    4. Append the result to history
    5. Loop up to max_steps times

    Print each step: "Step N: calling tool_name(args) → result"
    Return the final answer string.
    """
    # YOUR CODE HERE
    pass


# Test
if __name__ == "__main__":
    tasks = [
        "Look up STIG V-220706 and give me a summary of the findings.",
        "Calculate the risk score for 3 open CAT1 findings.",
    ]
    for task in tasks:
        print(f"\n=== Task: {task} ===")
        result = run_agent(task)
        print(f"Final: {result}")
