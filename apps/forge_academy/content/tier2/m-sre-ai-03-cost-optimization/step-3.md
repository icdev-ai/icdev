---
ontology_id: icdev:mission:m-sre-ai-03-cost-optimization:step:3
step_class: icdev:Assessment
---

<!-- CUI // SP-CTI -->

# Cost Optimization Findings

You have configured cost controls and run `recommend_optimizations()`. Now you need to interpret the output, prioritize actions, avoid the most common anti-patterns, and validate your savings with `project_monthly_spend()`.

## Interpreting `recommend_optimizations()` Output

Each recommendation has a `recommendation_type` from a fixed set. These are the checks `cost_intelligence.py` runs today:

| Type | What triggered it | Typical action |
|---|---|---|
| `switch_to_local` | A cloud model with real spend in the last 30 days | Put `qwen3-local` first in that function's routing chain |
| `reduce_tokens` | An agent whose average prompt is more than 2x the median | Compress the prompt, trim history |
| `cache_responses` | 80% or more of an agent's prompts are repeats | Enable or extend `response_cache` for it |
| `enable_cod` / `enable_cot` | A function that would benefit from a different reasoning architecture | Review the `architectures:` block in `args/llm_config.yaml` |

Batching (combining many short calls into one) is a valid lever, but no detector recommends it. You will have to spot it yourself in `daily_trend` and `by_agent` call counts.

## Prioritization Matrix

Not all optimizations are equal. Prioritize by impact × effort:

```
                      IMPLEMENTATION EFFORT
                   Low          Medium        High
               ┌──────────────┬────────────┬─────────────┐
  SAVINGS High │ DO FIRST ★   │ PLAN NEXT  │ DEFER       │
               ├──────────────┼────────────┼─────────────┤
  SAVINGS Med  │ DO NEXT      │ BACKLOG    │ SKIP        │
               ├──────────────┼────────────┼─────────────┤
  SAVINGS Low  │ QUICK WIN    │ SKIP       │ NEVER       │
               └──────────────┴────────────┴─────────────┘
```

A `switch_to_local` recommendation with a large `estimated_savings_usd` is usually a DO FIRST action. It needs only a routing-chain change in `args/llm_config.yaml`, provided the local model's quality holds.

## The 4 Most Common Cost Anti-Patterns

### Anti-Pattern 1: Always Using the Most Capable Model

Engineers default to the best model "to be safe." In practice, `claude-sonnet` produces the same output as `qwen3-local` for 60–70% of production functions (classification, extraction, formatting). The capability gap matters only for complex reasoning tasks.

**Fix:** Run `compare_edge_vs_cloud()` to see the cost gap. Then compare quality with `model_monitor.get_baseline()` for both models. Downgrade when the quality drop is within your tolerance (for example, under 5%).

### Anti-Pattern 2: Re-Embedding Unchanged Documents

RAG pipelines that re-embed the entire corpus on every run waste compute and (for cloud embedding APIs) money. Embeddings only need to regenerate when the source document changes.

**Fix:** Hash document content; only re-embed if hash changes.

### Anti-Pattern 3: Not Caching User-Identical Queries

A user clicking "Refresh" on a report page should not trigger a new LLM call if the underlying data hasn't changed. Without caching, a report viewed 100 times/day = 100 LLM calls/day.

**Fix:** Keep `response_cache` enabled in `args/llm_config.yaml`, with a TTL (or a `per_function` TTL) that matches your data-freshness SLA.

### Anti-Pattern 4: Sending Full Conversation History Every Turn

Multi-turn chat agents that append every message to the context window see token counts grow linearly with conversation length. A 20-turn conversation can cost 10x more than a 2-turn one for the same information exchange.

**Fix:** Implement a summarization compressor that condenses history older than N turns into a summary block.

## Validating Your Savings

After implementing optimizations, re-project spend:

```python
from tools.llm.cost_intelligence import project_monthly_spend, detect_cost_anomalies

projection = project_monthly_spend()
print(f"Projected month: ${projection['total_projected_usd']:.2f}")
# Compare with the projection you saved before the change.
```

Also re-run `detect_cost_anomalies()`. A successful optimization removes the spike that prompted it. If the anomaly persists, the optimization was not applied correctly.

## Quick CLI Validation Loop

```bash
# 1. Baseline
python tools/llm/cost_intelligence.py --project --json > before_opt.json

# 2. Apply optimizations (edit llm_config.yaml, deploy)

# 3. Wait 24h for new data

# 4. Compare
python tools/llm/cost_intelligence.py --project --json > after_opt.json
# Compare total_projected_usd in the two files
```

**Your task:** Answer the reflection questions.
