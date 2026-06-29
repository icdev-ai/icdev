---
ontology_id: icdev:mission:m-sre-xai-01:step:2
step_class: icdev:coding
---

# Run AgentSHAP Attribution

Write a script that runs AgentSHAP attribution on a recent agent trace and produces a tool impact report.

## AgentSHAP API

```python
from tools.xai.agent_shap import AgentSHAP

shap = AgentSHAP()
attribution = shap.explain(
    trace_id="trace-abc123",
    target="response_quality",
)
# Returns:
# {
#   "trace_id": "trace-abc123",
#   "tool_attributions": [
#     {"tool": "knowledge.search", "shap_value": 0.62, "contribution_pct": 62},
#     ...
#   ],
#   "top_contributor": "knowledge.search",
#   "explanation": "..."
# }
```

## Your task

Write a Python script that:
1. Lists the 5 most recent traces from the traces DB
2. Runs AgentSHAP attribution on each trace
3. Prints a ranked tool attribution table
4. Identifies the tool with the highest average SHAP value across all 5 traces
