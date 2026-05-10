"""
SWE/Arch Mission 1: Multi-Agent DAG — Directed Acyclic Graph Orchestration
Goal: Build a DAGRunner that executes agent tasks in dependency order.
"""

from collections import deque

# ── Compliance Pipeline Task Functions ───────────────────────────────────────

def task_stig_scan(context: dict) -> dict:
    """Simulate scanning 3 systems for STIG findings."""
    return {
        "systems_scanned": 3,
        "findings": [
            {"system": "ICDEV-Prod", "cat1": 2, "cat2": 5, "cat3": 8},
            {"system": "ICDEV-Dev",  "cat1": 0, "cat2": 3, "cat3": 12},
            {"system": "ICDEV-Test", "cat1": 1, "cat2": 2, "cat3": 4},
        ],
    }


def task_risk_score(context: dict) -> dict:
    """Calculate risk score from STIG scan results."""
    scan = context.get("stig_scan", {})
    findings = scan.get("findings", [])
    total_cat1 = sum(f["cat1"] for f in findings)
    total_cat2 = sum(f["cat2"] for f in findings)
    score = total_cat1 * 10 + total_cat2 * 3
    level = "CRITICAL" if score >= 30 else "HIGH" if score >= 15 else "MEDIUM"
    return {"risk_score": score, "risk_level": level, "total_cat1": total_cat1}


def task_poam_draft(context: dict) -> dict:
    """Draft POA&M entries from scan findings."""
    scan = context.get("stig_scan", {})
    findings = scan.get("findings", [])
    entries = []
    for f in findings:
        if f["cat1"] > 0:
            entries.append({
                "system": f["system"],
                "severity": "CAT I",
                "count": f["cat1"],
                "deadline_days": 30,
            })
    return {"poam_entries": entries, "total_entries": len(entries)}


def task_executive_summary(context: dict) -> dict:
    """Combine risk score and POA&M into an executive summary."""
    risk = context.get("risk_score", {})
    poam = context.get("poam_draft", {})
    level = risk.get("risk_level", "UNKNOWN")
    cat1_count = risk.get("total_cat1", 0)
    poam_count = poam.get("total_entries", 0)
    return {
        "summary": (
            f"Overall risk: {level}. "
            f"{cat1_count} CAT I findings require immediate remediation. "
            f"{poam_count} POA&M entries drafted for ISSM review."
        ),
        "risk_level": level,
        "action_required": cat1_count > 0,
    }


# ── DAG Task Registry ─────────────────────────────────────────────────────────

COMPLIANCE_PIPELINE = {
    "stig_scan":          {"fn": task_stig_scan,          "deps": []},
    "risk_score":         {"fn": task_risk_score,          "deps": ["stig_scan"]},
    "poam_draft":         {"fn": task_poam_draft,          "deps": ["stig_scan"]},
    "executive_summary":  {"fn": task_executive_summary,   "deps": ["risk_score", "poam_draft"]},
}


# ── DAGRunner ─────────────────────────────────────────────────────────────────

class DAGRunner:
    """Runs a dict-of-tasks in topological order, passing results as context."""

    def __init__(self, task_graph: dict):
        """
        task_graph: dict[str, {"fn": callable, "deps": list[str]}]
        """
        self.graph = task_graph

    def _build_order(self) -> list[list[str]]:
        """TODO: Use Kahn's algorithm to produce batches of tasks.

        A "batch" is all tasks whose dependencies have been satisfied by
        previous batches. Tasks within a batch can run in any order (they
        don't depend on each other).

        Algorithm:
        1. Compute in_degree[task] = len(deps) for each task
        2. Initialize queue with all tasks where in_degree == 0
        3. While queue is non-empty:
           a. Dequeue all current tasks → this is one batch
           b. For each task in the batch, find all tasks that depend on it
           c. Reduce their in_degree by 1; if in_degree reaches 0, enqueue them
        4. If total tasks processed < len(graph): raise ValueError("DAG has a cycle")

        Return: list of batches, e.g. [["stig_scan"], ["risk_score", "poam_draft"], ["executive_summary"]]
        """
        # YOUR CODE HERE
        pass

    def run(self) -> dict:
        """TODO: Execute the DAG, returning all task results.

        1. Call _build_order() to get execution batches
        2. For each batch:
           a. Execute each task's fn(context) where context = all results so far
           b. Store result as results[task_name]
        3. Return the complete results dict: {task_name: result_dict, ...}

        The context passed to each task must contain the results of ALL
        previously completed tasks (from prior batches).
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    runner = DAGRunner(COMPLIANCE_PIPELINE)
    results = runner.run()

    print("=== Compliance Pipeline DAG Results ===")
    print(f"Tasks completed: {list(results.keys())}")
    print(f"\nSTIG Scan: {results['stig_scan']['systems_scanned']} systems")

    risk = results["risk_score"]
    print(f"Risk Score: {risk['risk_score']} ({risk['risk_level']})")

    poam = results["poam_draft"]
    print(f"POA&M Entries: {poam['total_entries']}")

    summary = results["executive_summary"]
    print(f"\nExecutive Summary: {summary['summary']}")
