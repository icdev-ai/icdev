---
ontology_id: icdev:mission:m-sre-xai-01:step:2
step_class: icdev:coding
---

# Run AgentSHAP Attribution

Write a script that runs AgentSHAP attribution on recent agent traces and produces a tool impact report.

## AgentSHAP on the platform

On a live ICDEV install, AgentSHAP lives in `tools/observability/shap/agent_shap.py` (`AgentSHAP.analyze_trace(...)`) and reads spans from the `otel_traces` table. An attribution looks like:

```python
# {
#   "trace_id": "trace-abc123",
#   "tool_attributions": [
#     {"tool": "knowledge.search", "shap_value": 0.62, "contribution_pct": 62},
#     ...
#   ],
#   "top_contributor": "knowledge.search",
# }
```

## In the exercise

The Academy sandbox cannot import `tools.*` and has no database or network access. The starter therefore provides stubs with the same shape:

- `SAMPLE_TRACES` and `get_recent_traces(n)` — the 5 most recent traces, newest first
- `explain(trace_id)` — returns the attribution dict shown above for one trace

Use the stubs as-is; do not try to import the platform module.

## Your task

1. In `run_attribution_report()`, call `explain()` for each of the 5 recent traces and append the **attribution dict** (not the raw trace) to `results`
2. Print a ranked tool attribution table for each trace (tool, `shap_value`)
3. Implement `highest_average_tool(results)` — the tool with the highest average `shap_value` across all 5 traces (a tool missing from a trace counts as 0)
4. Print that tool and its average as a summary line, and return `results`
