---
ontology_id: icdev:mission:m-sre-ai-03-cost-optimization:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# AI Cost Optimization — 5 Levers

LLM costs scale with usage in ways that traditional compute costs don't. A single misconfigured agent that sends full conversation history on every turn can generate $400/day in API calls. Cost optimization is not premature optimization — it is production hygiene.

## Lever 1: Model Routing

Not every task needs the most capable model. ICDEV's `tools/llm/router.py` routes by **function**. Each function has a declared `chain` of models in the `routing:` section of `args/llm_config.yaml`. The router walks the chain and uses the first model whose provider is available. A function with no entry falls back to `routing.default`.

```yaml
routing:
  nlq_sql:
    chain: [qwen3-local, gpt-4o-mini, gemini-2.0-flash, claude-haiku, llama-local]
    effort: low
  code_generation:
    chain: [qwen3-local, kimi-cloud, claude-sonnet, gemini-2.5-pro, gpt-4o, codestral-local]
    effort: high
```

```python
from tools.llm.router import LLMRouter

router = LLMRouter()
provider, model_id, model_cfg = router.get_provider_for_function("nlq_sql")
# -> the first AVAILABLE model in nlq_sql's chain, e.g. qwen3-local on a box running Ollama
```

So cost routing is a **configuration decision you review**. Put a cheap or local model first in the chain for simple functions, and keep the expensive model first only where it earns its price.

**Relative cost tiers** (check your vendor's current price sheet; the price ICDEV uses is the `pricing:` entry for each model in `args/llm_config.yaml`):

| Logical model (llm_config) | Relative cost | Best for |
|---|---|---|
| `qwen3-local` (Ollama) | $0 per token: you pay for the hardware | Classification, extraction, summarization |
| `claude-haiku`, `gpt-4o-mini` | Low | Light reasoning, formatting |
| `claude-sonnet`, `gpt-4o` | Medium | Complex reasoning, code generation |
| `claude-opus` | High | Research, strategic analysis |

Routing simple functions to `qwen3-local` takes the per-token cost of those calls to zero.

## Lever 2: Prompt Compression

Redundant context inflates token counts without improving output quality. Typical techniques:
- Remove boilerplate instructions that the system prompt already covers.
- Summarize conversation history rather than appending full turns.
- Strip whitespace, markdown formatting and code comments from retrieved document chunks.
- Use a sliding window (keep the last N turns) rather than unbounded history.

`recommend_optimizations()` (next step) flags agents whose average prompt is more than 2x the median as `reduce_tokens` candidates.

## Lever 3: Response Caching

Identical prompts within a TTL window should return cached results. A user who asks "What is the status of contract #1042?" three times in a session should trigger one LLM call, not three. ICDEV's router cache is configured in `args/llm_config.yaml`:

```yaml
response_cache:
  enabled: true
  backend: postgresql
  ttl_seconds: 3600
  max_entries: 100000
  match_strategy: exact          # exact prompt match, not semantic similarity
  excluded_functions: [pulse_generation, news_oracle, ...]
  per_function:
    code_generation:
      ttl_seconds: 7200
```

Matching is **exact**: a reworded question is a cache miss. Exclude functions whose answers must always be fresh.

## Lever 4: Token Budgets

Hard stops enforced by `token_tracker.py` prevent runaway costs. A bug in an agent loop that generates 50,000 API calls overnight is a budget issue, not just a reliability issue. Set conservative budgets initially and widen them based on observed usage.

## Lever 5: Batch Processing

If your workflow makes 50 short LLM calls sequentially, combine them into a single batched call where the model processes all 50 inputs in one prompt. Reduces per-call overhead and often improves throughput by 3–5x.

## Concrete Example: Document Summarizer

Before optimization, a document summarizer handles 200 contracts/day. Assume a cloud model at **$3.00 per million input tokens** for this illustration.

- Sends the full 8,000-token contract to the cloud model every time.
- Cost: 200 × 8,000 tokens × $3.00/M = **$4.80/day ≈ $144/month**.

After optimization:
1. Route simple contracts (<2,000 tokens, 68% of volume) to `qwen3-local`.
2. Compress repetitive boilerplate headers: −23% tokens on the rest.
3. Cache identical contract re-reads: −12% more calls.

New cost: $144 × (1 − 0.68) × (1 − 0.23) × (1 − 0.12) ≈ **$31/month**.
**Savings: 78%.**

## `compare_edge_vs_cloud()`

```python
from tools.llm.cost_intelligence import compare_edge_vs_cloud

result = compare_edge_vs_cloud(function_name="nlq_sql")
# {
#   "status": "ok",
#   "function_name": "nlq_sql",
#   "avg_input_tokens": 512.0, "avg_output_tokens": 180.0,
#   "chain": ["qwen3-local", "gpt-4o-mini", ...],
#   "comparisons": [
#     {"model": "qwen3-local", "is_local": True, "estimated_cost_per_call": 0.0, ...},
#     {"model": "gpt-4o-mini", "is_local": False, "estimated_cost_per_call": 0.000185, ...}
#   ],
#   "cheapest_cloud": {...}, "cheapest_local": {...}
# }
```

It compares **cost** across the function's routing chain, using the average token counts from the last 30 days. It does not measure quality. Pair it with `model_monitor.get_baseline()` quality numbers (Mission SRE-AI-02) before you move a function to a cheaper model.

**Your task:** In the next step, configure your cost controls.
