---
ontology_id: icdev:mission:m-sre-ai-01-llm-observability:step:2
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Instrument Your Agent with token_tracker.py

In the previous step you learned the four observability pillars. Now you wire them into a real agent. This step covers the full instrumentation pattern: budget enforcement, usage logging, latency capture, and quality score recording.

## Setting a Monthly Budget

Before deploying an agent to production, establish its monthly token budget. The budget is stored per `agent_id` in `agent_budgets` and checked on every call.

```bash
python tools/agent/token_tracker.py --set-budget my-agent 50.0
# Output: Budget set: my-agent = $50.00/month
```

You can also set budgets programmatically:

```python
from tools.agent.token_tracker import set_budget
set_budget(agent_id="my-agent", monthly_limit_usd=50.0)
```

## The Full Instrumented Call Pattern

```python
import time
from tools.agent.token_tracker import log_usage, check_budget, get_usage_summary
from tools.llm.model_monitor import record_quality_score

def instrumented_llm_call(
    agent_id: str,
    model: str,
    prompt: str,
    task_id: str,
    function_name: str = "default",
) -> dict:
    # 1. Budget gate — enforce before every invoke
    budget_status = check_budget(agent_id)
    if budget_status == "block":
        return {"error": "budget_exceeded", "output": None}
    if budget_status == "warn":
        print(f"[BUDGET WARN] {agent_id} is above 80% of monthly limit.")

    # 2. Call the model with latency measurement
    t0 = time.perf_counter()
    import requests
    resp = requests.post(
        "http://localhost:11434/api/chat",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    latency_ms = (time.perf_counter() - t0) * 1000

    output_text = data["message"]["content"]
    input_tokens = data.get("prompt_eval_count", 0)
    output_tokens = data.get("eval_count", 0)

    # 3. Log token usage
    log_usage(
        agent_id=agent_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        task_id=task_id,
    )

    # 4. Record quality score (use your evaluator; default: heuristic length check)
    quality_score = min(1.0, len(output_text.split()) / 50.0)  # replace with real evaluator
    record_quality_score(
        model_id=model,
        function_name=function_name,
        score=quality_score,
        response_time_ms=latency_ms,
        token_count=output_tokens,
    )

    return {
        "output": output_text,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "quality_score": quality_score,
        "budget_status": budget_status,
    }
```

## Retrieving Usage Summaries

```python
from tools.agent.token_tracker import get_usage_summary

summary = get_usage_summary(agent_id="my-agent", period="month")
# Returns:
# {
#   "agent_id": "my-agent",
#   "period": "month",
#   "total_input_tokens": 142800,
#   "total_output_tokens": 38400,
#   "total_cost_usd": 12.47,
#   "call_count": 234,
#   "budget_limit_usd": 50.0,
#   "budget_used_pct": 24.9
# }
```

This summary feeds the ICDEV dashboard at `/monitor` and produces the cost trend chart on the home canvas.

## CLI Quick-Check

```bash
# Check current month spend for an agent
python tools/agent/token_tracker.py --summary my-agent --period month

# Check budget status without making a call
python tools/agent/token_tracker.py --check-budget my-agent
# Output: allow | Budget: $12.47 / $50.00 (24.9%)
```

## Connecting Latency to the Dashboard

Latency is stored in `model_quality_scores.response_time_ms` alongside quality scores. The P50/P95/P99 percentiles are computed at query time:

```sql
SELECT
    percentile_cont(0.50) WITHIN GROUP (ORDER BY response_time_ms) AS p50,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY response_time_ms) AS p95,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY response_time_ms) AS p99
FROM model_quality_scores
WHERE model_id = 'qwen3-local'
  AND recorded_at >= datetime('now', '-24 hours');
```

**Your task:** Answer the configuration questions above.
