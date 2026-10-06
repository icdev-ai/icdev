---
ontology_id: icdev:mission:m-sre-ai-01-llm-observability:step:2
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Instrument Your Agent with token_tracker.py

In the previous step you learned the four observability pillars. Now you wire them into a real agent: budget enforcement, usage logging, latency capture, and quality-score recording.

## Setting a Monthly Budget

Budgets are configuration, not code. They live under `token_budgets` in `args/llm_config.yaml`:

```yaml
token_budgets:
  enabled: true
  default_monthly_usd: 50.00
  warning_threshold: 0.8          # warn at 80% spend
  hard_stop: true                  # block at 100% (false = warn-only)
  per_agent:
    my-agent:
      monthly_usd: 50.00
```

`check_budget(agent_id)` reads this block. It sums the agent's `cost_estimate_usd` in `agent_token_usage` for the current month and returns the decision. A `default_monthly_usd` of 0 means unlimited.

## The Full Instrumented Call Pattern

```python
import time
import requests
from tools.agent.token_tracker import log_usage, check_budget, estimate_cost
from tools.llm.model_monitor import record_quality_score

def instrumented_llm_call(agent_id: str, project_id: str, model: str, prompt: str,
                          task_id: str, function_name: str = "default") -> dict:
    # 1. Budget gate: enforce before every invoke
    budget = check_budget(agent_id)
    if budget["action"] == "block":
        return {"error": "budget_exceeded", "output": None, "detail": budget["message"]}
    if budget["action"] == "warn":
        print(f"[BUDGET WARN] {budget['message']}")

    # 2. Call the model with latency measurement
    t0 = time.perf_counter()
    resp = requests.post(
        "http://localhost:11434/api/chat",
        json={"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    latency_ms = int((time.perf_counter() - t0) * 1000)

    output_text = data["message"]["content"]
    input_tokens = data.get("prompt_eval_count", 0)
    output_tokens = data.get("eval_count", 0)

    # 3. Log token usage
    log_usage(agent_id=agent_id, project_id=project_id, model_id=model,
              input_tokens=input_tokens, output_tokens=output_tokens,
              duration_ms=latency_ms, task_id=task_id,
              cost_estimate_usd=estimate_cost(model, input_tokens, output_tokens))

    # 4. Record a quality score (replace the heuristic with a real evaluator)
    quality_score = min(1.0, len(output_text.split()) / 50.0)
    record_quality_score(
        model_id=model,
        function_name=function_name,
        score=quality_score,
        response_time_ms=latency_ms,
        token_count=output_tokens,
    )

    return {"output": output_text, "latency_ms": latency_ms,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "quality_score": quality_score, "budget_action": budget["action"]}
```

## Retrieving Usage Summaries

```python
from tools.agent.token_tracker import get_usage_summary, check_budget

summary = get_usage_summary(agent_id="my-agent")          # all-time totals for this agent
# {"total_input": 142800, "total_output": 38400, "total_thinking": 0,
#  "total_cost": 12.47, "count": 234}

check_budget("my-agent")
# {"action": "allow", "agent_id": "my-agent", "month": "2026-10",
#  "budget_usd": 50.0, "spent_usd": 12.47, "remaining_usd": 37.53,
#  "utilization_pct": 24.94, "message": "Agent 'my-agent' within budget: ..."}
```

`tools/llm/cost_intelligence.py` builds its spend views (by agent, by model, daily trend, budget status) from the same table: `python tools/llm/cost_intelligence.py --dashboard --json`.

## CLI Quick-Check

```bash
# Usage for one agent
python tools/agent/token_tracker.py --action summary --agent-id my-agent --json

# Budget decision without making a call
python tools/agent/token_tracker.py --action check-budget --agent-id my-agent --json

# Every agent with spend this month; exits 1 if any is blocked
python tools/agent/token_tracker.py --action budgets --gate
```

## Connecting Latency to the Dashboard

Latency is stored in `model_quality_scores.response_time_ms` alongside quality scores. `get_baseline(model_id, function_name)` reports the baseline window's `latency_ms` mean, p95 and p99. For an ad-hoc window, read the rows and compute the percentiles in Python. This works the same on PostgreSQL and SQLite:

```python
import statistics
from tools.llm.model_monitor import get_baseline

baseline = get_baseline("qwen3-local", "summarize")
print(baseline.get("latency_ms"))      # {"mean": ..., "p95": ..., "p99": ...}

def p99(latencies: list[int]) -> float:
    return statistics.quantiles(latencies, n=100)[98]
```

**Your task:** Answer the configuration questions above.
