---
ontology_id: icdev:mission:m-sre-ai-01-llm-observability:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# LLM Observability — What to Measure

Standard APM tools (Datadog, New Relic, Prometheus) capture CPU, memory, request latency, and error rates. They are blind to what makes AI systems fail: token inflation, quality regression, model drift, and cost runaway. Instrumenting LLMs requires a second observability layer built around four AI-specific pillars.

## The 4 Pillars of LLM Observability

### 1. Token Usage and Cost

Every LLM call consumes input tokens (your prompt) and output tokens (the model's response). Cost is computed as:

```
cost = (input_tokens / 1000) * price_per_1k_input
     + (output_tokens / 1000) * price_per_1k_output
```

Prices change often, so ICDEV does not hard-code them. Each model's `pricing: {input_per_1k, output_per_1k}` lives in `args/llm_config.yaml`, and `token_tracker.estimate_cost()` reads it from there. For orientation, as of September 2026 (source: Anthropic API first-party pricing, per million tokens — input / cached-input read / output): Claude Haiku 4.5 $1.00 / ~$0.10 / $5.00, Claude Sonnet 5.5 $2.00 / $0.20 / $10.00, Claude Opus 5.5 $4.00 / $0.20 / $20.00. A local model served by Ollama (such as `qwen3-local`) is priced at $0.00 per token (you pay in hardware instead). Reasoning ("thinking") tokens are billed as output tokens even when the reasoning text is hidden, so track them as their own series — they are the usual cause of an output bill that outgrows the visible answers. Token counts grow silently: a prompt that works at 500 tokens can balloon to 3,000 tokens as conversation history accumulates. Track per-agent, per-function, and per-day.

### 2. Latency (P50 / P95 / P99)

Mean latency is a vanity metric for LLMs. P99 matters. A model responding in 800ms median but 14s at P99 will break synchronous user-facing flows. Measure latency end-to-end: from `requests.post()` to last token received. Separate by model and function — summarization has a different latency profile than code generation.

### 3. Quality Scores

LLM quality is not binary. Quality scores range from 0.0 to 1.0, produced by an evaluator (LLM-as-judge, embedding cosine similarity, or task-specific heuristics). A score below 0.7 indicates degraded output. Track rolling averages per `(model_id, function_name)` pair to detect gradual drift.

### 4. Error Rates

API failures, rate limits (HTTP 429), and timeouts are distinct failure modes requiring different responses. Rate limit errors mean you need to back off and retry. Timeout errors may indicate token inflation. Model errors (500s) require fallback routing. Track each type separately — a single "error rate" metric hides the root cause.

## Why Standard APM Misses These Signals

APM tracks request success/failure. A 200 OK response from an LLM API is counted as success even if the model hallucinated, exceeded your token budget, or produced output with a quality score of 0.2. You need application-layer instrumentation.

## The ICDEV Tool: `tools/agent/token_tracker.py`

Key functions:

| Function | Description |
|---|---|
| `log_usage(agent_id, project_id, model_id, input_tokens, output_tokens, duration_ms=0, task_id=None, cost_estimate_usd=0.0)` | Records one LLM call in `agent_token_usage` |
| `get_usage_summary(project_id=None, agent_id=None)` | Aggregates token counts, cost and call count |
| `check_budget(agent_id)` | Returns a decision dict whose `action` is `'allow'`, `'warn'` or `'block'` |

You rarely call these by hand. `LLMRouter.invoke()` runs `check_budget()` before every call whose `LLMRequest` carries an `agent_id`, and raises `BudgetExceededError` on `block`. The Bedrock client calls `log_usage()` for you. The pattern below shows what that wiring does, so you can reproduce it around a provider the router does not cover.

## Wrapping an LLM Call with Token Tracking

```python
import time
import requests
from tools.agent.token_tracker import log_usage, check_budget, estimate_cost

def invoke_llm(agent_id: str, project_id: str, model: str, prompt: str, task_id: str):
    # Gate: enforce budget before the call
    budget = check_budget(agent_id)
    if budget["action"] == "block":
        raise RuntimeError(budget["message"])
    if budget["action"] == "warn":
        print(f"[WARN] {budget['message']}")

    t0 = time.perf_counter()
    resp = requests.post(                       # Ollama example
        "http://localhost:11434/api/chat",
        json={"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    latency_ms = int((time.perf_counter() - t0) * 1000)
    input_tokens = data.get("prompt_eval_count", 0)
    output_tokens = data.get("eval_count", 0)

    # Record usage after the call
    log_usage(
        agent_id=agent_id,
        project_id=project_id,
        model_id=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=latency_ms,
        task_id=task_id,
        cost_estimate_usd=estimate_cost(model, input_tokens, output_tokens),
    )
    return data["message"]["content"], latency_ms
```

This pattern gates every LLM call on budget before it runs and records it in full afterwards. `log_usage()` writes to the `agent_token_usage` table in the ICDEV database (PostgreSQL by default, SQLite as the fallback). Separately, `LLMRouter` writes a hashed record of every call to the append-only `ai_telemetry` table, which is what makes usage auditable under NIST AU-2.

**Your task:** In the next step, instrument your own agent.
