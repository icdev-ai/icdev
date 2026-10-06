---
ontology_id: icdev:mission:m-sre-ai-03-cost-optimization:step:2
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Configure Cost Controls

In the previous step you learned the five cost levers. Now you wire them into your system with `tools/llm/cost_intelligence.py` and `args/llm_config.yaml`. Every function below reads the `agent_token_usage` table that `token_tracker.log_usage()` fills (Mission SRE-AI-01).

## `cost_intelligence.py` API Reference

### `get_cost_dashboard()`

```python
from tools.llm.cost_intelligence import get_cost_dashboard

dashboard = get_cost_dashboard()
# {
#   "status": "ok",
#   "total_spend_usd": 87.42, "total_calls": 5120,
#   "month": "2026-10", "month_spend_usd": 31.10, "month_calls": 1840,
#   "by_agent":  [{"agent_id": "builder-agent", "cost_usd": 18.2, "calls": 400, ...}, ...],
#   "by_model":  [{"model_id": "claude-sonnet-4-6", "cost_usd": 22.9, "calls": 310, ...}, ...],
#   "daily_trend": [{"date": "2026-10-01", "cost_usd": 3.1, "calls": 190}, ...],
#   "budget_status": [{"agent_id": ..., "budget_usd": 50.0, "spent_usd": ..., "utilization_pct": ...}],
#   "unacknowledged_alerts": 2
# }
```

Spend is broken down **by agent and by model**. To find your most expensive *function*, group `agent_token_usage` by `agent_id` and map each agent to the functions it calls. Alternatively, read `ai_telemetry`, which records the router `function` for every call.

### `recommend_optimizations()`

```python
from tools.llm.cost_intelligence import recommend_optimizations

recs = recommend_optimizations()
# [
#   {"id": "crec-...", "recommendation_type": "switch_to_local",
#    "function_name": None, "current_model": "claude-sonnet-4-6",
#    "recommended_model": "qwen3-local",
#    "estimated_savings_usd": 18.32, "confidence": 0.6, "status": "pending"},
#   ...
# ]
```

Every recommendation is also stored in `llm_cost_recommendations`.

### `project_monthly_spend()`

```python
from tools.llm.cost_intelligence import project_monthly_spend

projection = project_monthly_spend()            # or project_monthly_spend(agent_id="builder-agent")
# {"status": "ok", "month": "2026-10",
#  "total_spent_usd": 31.10, "total_projected_usd": 107.1,
#  "projections": [{agent_id, spent_usd, daily_rate_usd, projected_eom_usd, over_budget, ...}],
#  "alerts_created": [...]}
```

It is a linear extrapolation of this month's spend, and it raises an alert for any agent projected over its `token_budgets` cap.

### `detect_cost_anomalies()`

```python
from tools.llm.cost_intelligence import detect_cost_anomalies

result = detect_cost_anomalies(lookback_hours=24)
# {"status": "ok", "lookback_hours": 24, "anomalies_found": 1,
#  "anomalies": [{"agent_id": ..., "recent_hourly_usd": ..., "baseline_hourly_usd": ...,
#                 "ratio": 6.2, ...}],
#  "alerts_created": ["..."]}
```

It compares each agent's recent hourly spend with its 7-day baseline. Each anomaly is a `spike` alert in `llm_cost_alerts`; the severity is `critical` when the ratio exceeds 5x and `warning` otherwise.

## Configuring Cost-Aware Routing in `args/llm_config.yaml`

The levers map to real config blocks:

```yaml
routing:                       # Lever 1: per-function model chain
  extract_entities:            # your function name
    chain: [qwen3-local, claude-haiku]
    effort: low

response_cache:                # Lever 3: exact-match cache
  enabled: true
  ttl_seconds: 3600

token_budgets:                 # Lever 4: per-agent monthly caps
  enabled: true
  default_monthly_usd: 50.00
  warning_threshold: 0.8
  hard_stop: true
```

`two_tier:` is a separate block (`tier1_model` worker vs `tier2_model` planner/reviewer, plus a `planner_functions` list). It splits drafting from review; it is not a budget control.

## CLI Quick Commands

```bash
# Current cost dashboard
python tools/llm/cost_intelligence.py --dashboard --json

# Optimization recommendations
python tools/llm/cost_intelligence.py --recommend --json

# Project end-of-month spend (optionally for one agent)
python tools/llm/cost_intelligence.py --project --agent builder-agent --json

# Detect cost anomalies over the last 24h
python tools/llm/cost_intelligence.py --anomalies --lookback 24 --json

# Compare cost across a function's routing chain
python tools/llm/cost_intelligence.py --edge-vs-cloud --function nlq_sql --json
```

## Verifying Routing in Production

After changing a routing chain, confirm that requests land on the model you intended:

```python
from tools.llm.cost_intelligence import get_cost_dashboard

by_model = {m["model_id"]: m["cost_usd"] for m in get_cost_dashboard()["by_model"]}
print(by_model)
# A simple function you routed to qwen3-local should stop appearing under a cloud model_id.
```

**Your task:** Answer the configuration questions.
