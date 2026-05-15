---
ontology_id: icdev:mission:m-sre-ai-03-cost-optimization:step:3
step_class: icdev:Assessment
---

<!-- CUI // SP-CTI -->

# Cost Optimization Findings

You have configured cost controls and run `recommend_optimizations()`. Now you need to interpret the output, prioritize actions, avoid the most common anti-patterns, and validate your savings with `project_monthly_spend()`.

## Interpreting `recommend_optimizations()` Output

The function returns a list of recommendations ordered by estimated impact. Each recommendation has an `optimization_type` drawn from a fixed set:

| Type | What It Means | Typical Savings |
|---|---|---|
| `prompt_compression` | Your prompts contain redundant tokens | 10–30% |
| `model_downgrade` | A cheaper model can handle this function | 30–90% |
| `caching` | Same prompts are being called repeatedly | 5–40% |
| `batching` | Sequential calls should be combined | 10–25% |
| `context_trimming` | Conversation history is growing unbounded | 15–50% |

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

A `model_downgrade` recommendation with `estimated_savings_pct: 71` and `implementation_effort: low` is a DO FIRST action — it requires only a config change in `args/llm_config.yaml`.

## The 4 Most Common Cost Anti-Patterns

### Anti-Pattern 1: Always Using the Most Capable Model

Engineers default to the best model "to be safe." In practice, `claude-sonnet` produces the same output as `qwen3-local` for 60–70% of production functions (classification, extraction, formatting). The capability gap matters only for complex reasoning tasks.

**Fix:** Run `compare_edge_vs_cloud()` for every function. If `quality_delta < 0.05`, downgrade.

### Anti-Pattern 2: Re-Embedding Unchanged Documents

RAG pipelines that re-embed the entire corpus on every run waste compute and (for cloud embedding APIs) money. Embeddings only need to regenerate when the source document changes.

**Fix:** Hash document content; only re-embed if hash changes.

### Anti-Pattern 3: Not Caching User-Identical Queries

A user clicking "Refresh" on a report page should not trigger a new LLM call if the underlying data hasn't changed. Without caching, a report viewed 100 times/day = 100 LLM calls/day.

**Fix:** Enable `response_cache` in `args/llm_config.yaml` with a TTL matching your data freshness SLA.

### Anti-Pattern 4: Sending Full Conversation History Every Turn

Multi-turn chat agents that append every message to the context window see token counts grow linearly with conversation length. A 20-turn conversation can cost 10x more than a 2-turn one for the same information exchange.

**Fix:** Implement a summarization compressor that condenses history older than N turns into a summary block.

## Validating Your Savings

After implementing optimizations, re-project spend:

```python
from tools.llm.cost_intelligence import project_monthly_spend, get_cost_dashboard

# Before optimization (run last week): projected $246/month
# After optimization:
dashboard = get_cost_dashboard()
current_daily = dashboard["total_spend_usd"] / 9  # 9 days elapsed
projection = project_monthly_spend(current_daily_avg=current_daily)

print(f"Projected month: ${projection['projected_month_total_usd']:.2f}")
# Expected: ~$68/month after routing + caching optimizations
```

Also re-run `detect_cost_anomalies()` — a successful optimization removes the anomaly that triggered the recommendation. If the anomaly persists, the optimization was not applied correctly.

## Quick CLI Validation Loop

```bash
# 1. Baseline
python tools/llm/cost_intelligence.py --project > before_opt.json

# 2. Apply optimizations (edit llm_config.yaml, deploy)

# 3. Wait 24h for new data

# 4. Compare
python tools/llm/cost_intelligence.py --project > after_opt.json
# Manually compare projected_month_total_usd values
```

**Your task:** Answer the reflection questions.
