"""AgentSHAP attribution runner.

The Academy sandbox cannot import `tools.*` or reach the platform database, so this
starter ships a canned set of recent traces and a stub `explain()` that returns the
same shape as the platform's AgentSHAP attribution. Use them as-is.
"""

# ── Provided: simulated otel_traces rows (newest last) ────────────────────────

SAMPLE_TRACES = [
    {"trace_id": "trace-000", "operation": "agent.loop", "started_at": "2026-10-05T00:00:00Z", "duration_ms": 1200},
    {"trace_id": "trace-001", "operation": "agent.loop", "started_at": "2026-10-05T01:00:00Z", "duration_ms": 1300},
    {"trace_id": "trace-002", "operation": "agent.loop", "started_at": "2026-10-05T02:00:00Z", "duration_ms": 1400},
    {"trace_id": "trace-003", "operation": "agent.loop", "started_at": "2026-10-05T03:00:00Z", "duration_ms": 1500},
    {"trace_id": "trace-004", "operation": "agent.loop", "started_at": "2026-10-05T04:00:00Z", "duration_ms": 1600},
]

# Per-trace SHAP values the stub explainer returns.
SAMPLE_ATTRIBUTIONS = {
    "trace-000": {"knowledge.search": 0.62, "supply_chain.check": 0.29, "llm.summarize": 0.09},
    "trace-001": {"knowledge.search": 0.48, "supply_chain.check": 0.40, "llm.summarize": 0.12},
    "trace-002": {"knowledge.search": 0.55, "stig.scan": 0.30, "llm.summarize": 0.15},
    "trace-003": {"supply_chain.check": 0.51, "knowledge.search": 0.37, "llm.summarize": 0.12},
    "trace-004": {"knowledge.search": 0.70, "llm.summarize": 0.30},
}


def get_recent_traces(n: int = 5) -> list[dict]:
    """Return the N most recent traces (newest first)."""
    return sorted(SAMPLE_TRACES, key=lambda t: t["started_at"], reverse=True)[:n]


def explain(trace_id: str) -> dict:
    """Stub for AgentSHAP attribution of one trace.

    Returns:
        {
            "trace_id": str,
            "tool_attributions": [
                {"tool": "knowledge.search", "shap_value": 0.62, "contribution_pct": 62},
                ...                                   # highest shap_value first
            ],
            "top_contributor": "knowledge.search",
        }
    """
    vals = SAMPLE_ATTRIBUTIONS.get(trace_id, {})
    attrs = [
        {"tool": tool, "shap_value": value, "contribution_pct": round(value * 100)}
        for tool, value in sorted(vals.items(), key=lambda kv: -kv[1])
    ]
    return {
        "trace_id": trace_id,
        "tool_attributions": attrs,
        "top_contributor": attrs[0]["tool"] if attrs else None,
    }


# ── Your code ─────────────────────────────────────────────────────────────────

def highest_average_tool(results: list[dict]) -> str:
    """TODO: Return the tool with the highest AVERAGE shap_value across all results.

    `results` is the list of explain() dicts. Average over the number of traces —
    a tool missing from a trace counts as 0 for that trace.
    """
    # YOUR CODE HERE
    pass


def run_attribution_report() -> list[dict]:
    """Run AgentSHAP on recent traces and print attribution table.

    Return the list of explain() results, one per trace.
    """
    traces = get_recent_traces(5)
    if not traces:
        print("No traces found. Run some agent operations first.")
        return []

    results = []
    for trace in traces:
        # TODO: attribution = explain(trace["trace_id"])
        # TODO: print a ranked attribution table for this trace (tool, shap_value)
        # TODO: append the attribution dict (not the raw trace) to results
        results.append(trace)

    # TODO: print summary: highest_average_tool(results) and its average SHAP value
    return results


if __name__ == "__main__":
    run_attribution_report()
