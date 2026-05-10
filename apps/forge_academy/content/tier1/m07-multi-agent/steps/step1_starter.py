"""
Step 1: Multi-Agent Coordination — Orchestrator-Worker Pattern
Goal: Implement run_orchestrator() that dispatches to 3 workers and synthesizes results.
"""

import json


# ── Worker Agents ─────────────────────────────────────────────────────────────

def worker_stig_scanner(system_name: str) -> dict:
    """Worker: scans a system and returns STIG findings summary."""
    findings = {
        "ICDEV-Prod": {"cat1": 2, "cat2": 5, "cat3": 8, "system": system_name},
        "ICDEV-Dev":  {"cat1": 0, "cat2": 3, "cat3": 12, "system": system_name},
    }
    return findings.get(system_name, {"cat1": 1, "cat2": 4, "cat3": 6, "system": system_name})


def worker_rag_context(query: str) -> dict:
    """Worker: retrieves relevant compliance context for the query."""
    contexts = {
        "remediation": {
            "docs_found": 3,
            "top_result": "CAT I findings must be remediated within 30 days per DoD policy.",
            "source": "DoD POA&M Guidance",
        },
        "authorization": {
            "docs_found": 2,
            "top_result": "ATO requires all CAT I findings to be closed prior to authorization.",
            "source": "RMF Step 5 Guidance",
        },
    }
    for key, val in contexts.items():
        if key in query.lower():
            return val
    return {
        "docs_found": 1,
        "top_result": "Continuous monitoring requires monthly STIG scans for IL4 systems.",
        "source": "ICDEV Security Policy",
    }


def worker_report_writer(findings: dict, context: dict, system_name: str) -> str:
    """Worker: generates a plain-language security report from findings + context."""
    cat1 = findings.get("cat1", 0)
    cat2 = findings.get("cat2", 0)
    status = "CRITICAL — immediate action required" if cat1 > 0 else "ACCEPTABLE with remediation plan"
    return (
        f"Security Assessment: {system_name}\n"
        f"Status: {status}\n"
        f"Findings: {cat1} CAT I | {cat2} CAT II\n"
        f"Guidance: {context.get('top_result', 'N/A')}\n"
        f"Source: {context.get('source', 'N/A')}"
    )


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_orchestrator(task: str) -> dict:
    """TODO: Orchestrate 3 workers to complete the security assessment task.

    Steps:
    1. Extract system_name from the task string (look for known system names or use "ICDEV-Prod")
    2. Dispatch worker_stig_scanner(system_name) → findings
    3. Dispatch worker_rag_context("remediation") → context
    4. Dispatch worker_report_writer(findings, context, system_name) → report
    5. Synthesize: return a dict with keys: system, findings, context_docs, report, workers_called

    Print each dispatch step: "Dispatching <worker_name>..."
    """
    # YOUR CODE HERE
    pass


# Test
if __name__ == "__main__":
    tasks = [
        "Run a full security assessment for ICDEV-Prod and generate a remediation report.",
        "Assess ICDEV-Dev system compliance status.",
    ]
    for task in tasks:
        print(f"\nTask: {task}")
        result = run_orchestrator(task)
        if result:
            print(f"Workers called: {result.get('workers_called')}")
            print(f"Report preview: {result.get('report', '')[:100]}")
