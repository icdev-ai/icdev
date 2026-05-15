---
ontology_id: icdev:mission:m-sre-ai-03-cost-optimization:step:2
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Configure Cost Controls

In the previous step you learned the five cost levers. Now you wire them into your system using `cost_intelligence.py` and `args/llm_config.yaml`.

## `cost_intelligence.py` API Reference

### `get_cost_dashboard()`

Returns a complete picture of current spend:

```python
from tools.llm.cost_intelligence import get_cost_dashboard

dashboard = get_cost_dashboard()
# Returns:
# {
#   "total_spend_usd": 87.42,
#   "spend_by_model": {
#     "claude-sonnet-4-5": 64.10,
#     "claude-haiku-3-5": 18.90,
#     "qwen3-local": 0.00
#   },
#   "spend_by_function": {
#     "legal_contract_analysis": 48.30,
#     "document_summarize": 22.14,
#     "classify_sentiment": 0.00
#   },
#   "top_10_expensive_calls": [...],  # list of individual high-cost invocations
#   "period": "current_month",
#   "days_remaining": 22
# }
```

### `recommend_optimizations()`

Analyzes usage patterns and returns actionable recommendations:

```python
from tools.llm.cost_intelligence import recommend_optimizations

recs = recommend_optimizations()
# Returns list of dicts, e.g.:
# [
#   {
#     "optimization_type": "model_downgrade",
#     "function": "document_summarize",
#     "current_model": "claude-sonnet-4-5",
#     "recommended_model": "qwen3-local",
#     "estimated_savings_pct": 71.0,
#     "quality_risk": "low",
#     "implementation_effort": "low"
#   },
#   {
#     "optimization_type": "prompt_compression",
#     "function": "legal_contract_analysis",
#     "estimated_savings_pct": 18.0,
#     "quality_risk": "none",
#     "implementation_effort": "medium"
#   }
# ]
```

### `project_monthly_spend()`

Projects end-of-month spend based on current daily average:

```python
from tools.llm.cost_intelligence import project_monthly_spend

projection = project_monthly_spend(current_daily_avg=8.20)
# Returns:
# {
#   "current_daily_avg_usd": 8.20,
#   "projected_month_total_usd": 246.0,
#   "days_elapsed": 9,
#   "days_remaining": 22,
#   "current_spend_usd": 87.42
# }
```

### `detect_cost_anomalies()`

Flags unusual spending patterns:

```python
from tools.llm.cost_intelligence import detect_cost_anomalies

anomalies = detect_cost_anomalies()
# Returns list of anomaly dicts with severity: 'info', 'warning', 'critical'
# Example anomaly types:
#   - "spike": single-hour spend 5x above baseline
#   - "new_model_high_cost": unexpected cloud model appeared in spend
#   - "agent_loop_runaway": one agent_id consuming >50% of daily budget
```

## Configuring Cost-Aware Routing in `args/llm_config.yaml`

```yaml
two_tier:
  enabled: true
  edge:
    primary_model: qwen3-local
    base_url: http://localhost:11434/api/chat
    quality_threshold: 0.75  # if edge quality drops below this, escalate to cloud
  cloud:
    primary_model: claude-haiku-3-5
    fallback_model: claude-sonnet-4-5
    max_monthly_spend_usd: 200.0

routing_strategy:
  # Route by function complexity
  simple_functions:
    - classify_sentiment
    - extract_entities
    - format_output
    - tag_document
  # These always go to edge (Ollama)
  # All other functions route to cloud tier

cost_controls:
  per_agent_monthly_budget_usd: 50.0
  alert_threshold_pct: 80
  block_threshold_pct: 100
  anomaly_detection: true
  anomaly_spike_multiplier: 5.0
```

## CLI Quick Commands

```bash
# View current month cost dashboard
python tools/llm/cost_intelligence.py --dashboard

# Get optimization recommendations
python tools/llm/cost_intelligence.py --recommend

# Project end-of-month spend
python tools/llm/cost_intelligence.py --project

# Detect cost anomalies
python tools/llm/cost_intelligence.py --anomalies

# Compare edge vs cloud for a specific function
python tools/llm/cost_intelligence.py --compare --function document_summarize
```

## Verifying Routing in Production

After configuring cost-aware routing, confirm requests are landing on the correct model:

```python
from tools.agent.token_tracker import get_usage_summary

# Verify that classify_sentiment is using qwen3-local (cost $0)
summary = get_usage_summary(agent_id="classifier-agent", period="today")
assert summary["spend_by_model"].get("claude-sonnet-4-5", 0) == 0, \
    "Routing misconfigured: cloud model is being used for simple classification"
```

**Your task:** Answer the configuration questions.
