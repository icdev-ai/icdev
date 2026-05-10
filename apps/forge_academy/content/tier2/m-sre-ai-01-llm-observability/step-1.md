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

Reference prices (2025): `claude-haiku` ~$0.25/M input, `claude-sonnet` ~$3/M input, `qwen3-local` via Ollama = $0.00. Token counts grow silently — a prompt that works at 500 tokens can balloon to 3,000 tokens when conversation history accumulates. Track per-agent, per-function, and per-day.

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
| `log_usage(agent_id, model, input_tokens, output_tokens, task_id)` | Records a single LLM call |
| `get_usage_summary(agent_id, period='month')` | Aggregates spend and token counts |
| `check_budget(agent_id)` | Returns `'allow'`, `'warn'`, or `'block'` |

## Wrapping an LLM Call with Token Tracking

```python
from tools.agent.token_tracker import log_usage, check_budget
import time

def invoke_llm(agent_id: str, model: str, prompt: str, task_id: str) -> str:
    # Gate: enforce budget before the call
    status = check_budget(agent_id)
    if status == 'block':
        raise RuntimeError(f"Agent {agent_id} has exceeded its token budget.")
    if status == 'warn':
        print(f"[WARN] Agent {agent_id} approaching token budget limit.")

    t0 = time.perf_counter()

    # LLM call (Ollama example)
    import requests
    resp = requests.post(
        "http://localhost:11434/api/chat",
        json={"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()

    latency_ms = (time.perf_counter() - t0) * 1000
    input_tokens = data.get("prompt_eval_count", 0)
    output_tokens = data.get("eval_count", 0)

    # Record usage after the call
    log_usage(
        agent_id=agent_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        task_id=task_id,
    )

    return data["message"]["content"], latency_ms
```

This pattern ensures every LLM call is budget-gated before execution and fully recorded after. The `log_usage()` call writes to the `agent_token_usage` table in `data/icdev.db`, making all usage auditable under NIST AU-2.

**Your task:** In the next step, instrument your own agent.
