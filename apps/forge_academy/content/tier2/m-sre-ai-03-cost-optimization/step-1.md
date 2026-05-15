---
ontology_id: icdev:mission:m-sre-ai-03-cost-optimization:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# AI Cost Optimization — 5 Levers

LLM costs scale with usage in ways that traditional compute costs don't. A single misconfigured agent that sends full conversation history on every turn can generate $400/day in API calls. Cost optimization is not premature optimization — it is production hygiene.

## Lever 1: Model Routing

Not every task needs the most capable model. Use `tools/llm/router.py` to route requests to the appropriate model based on function complexity.

```python
from tools.llm.router import LLMRouter

router = LLMRouter()

# Router selects the cheapest model that meets the quality bar for this function
model = router.get_provider_for_function("classify_sentiment")
# Returns: "qwen3-local" (Ollama, $0.00/token)

model = router.get_provider_for_function("legal_contract_analysis")
# Returns: "claude-sonnet" ($3.00/M tokens) — high-stakes, needs best model
```

**Reference prices (2025):**

| Model | Input $/M tokens | Output $/M tokens | Best for |
|---|---|---|---|
| `qwen3-local` (Ollama) | $0.00 | $0.00 | Classification, extraction, summarization |
| `claude-haiku-3-5` | $0.25 | $1.25 | Light reasoning, formatting |
| `claude-sonnet-4-5` | $3.00 | $15.00 | Complex reasoning, code gen |
| `claude-opus-4` | $15.00 | $75.00 | Research, strategic analysis |

Routing simple queries to `qwen3-local` reduces cost to zero for those calls.

## Lever 2: Prompt Compression

Redundant context inflates token counts without improving output quality. ICDEV measures an average 23% token reduction from prompt compression alone.

Compression techniques:
- Remove boilerplate instructions already captured in the system prompt.
- Summarize conversation history rather than appending full turns.
- Strip whitespace, markdown formatting, and code comments from retrieved document chunks.
- Use a sliding window (keep last N turns) rather than unbounded history.

## Lever 3: Response Caching

Semantically identical prompts within a TTL window should return cached results. A user who asks "What is the status of contract #1042?" three times in a session should trigger one LLM call, not three.

Configure cache TTL in `args/llm_config.yaml`:

```yaml
response_cache:
  enabled: true
  ttl_seconds: 300
  similarity_threshold: 0.95  # cosine similarity for cache hit
  max_cache_entries: 10000
```

## Lever 4: Token Budgets

Hard stops enforced by `token_tracker.py` prevent runaway costs. A bug in an agent loop that generates 50,000 API calls overnight is a budget issue, not just a reliability issue. Set conservative budgets initially and widen them based on observed usage.

## Lever 5: Batch Processing

If your workflow makes 50 short LLM calls sequentially, combine them into a single batched call where the model processes all 50 inputs in one prompt. Reduces per-call overhead and often improves throughput by 3–5x.

## Concrete Example: Document Summarizer

Before optimization — a document summarizer running on 200 contracts/day:

- Sends full 8,000-token contract to `claude-sonnet` each time.
- Cost: 200 × 8,000 tokens × $3.00/M = **$4.80/day = $144/month**.

After optimization:
1. Route simple contracts (<2,000 tokens) to `qwen3-local` (68% of volume).
2. Compress repetitive boilerplate headers: −23% tokens on remaining.
3. Cache identical contract re-reads: −12% additional calls.

New cost: $144 × (1 − 0.68) × (1 − 0.23) × (1 − 0.12) ≈ **$31/month**.
**Savings: 78%.**

## `compare_edge_vs_cloud()`

```python
from tools.llm.cost_intelligence import compare_edge_vs_cloud

result = compare_edge_vs_cloud(function_name="document_summarize")
# Returns:
# {
#   "edge_model": "qwen3-local",
#   "edge_cost_per_call": 0.0,
#   "cloud_model": "claude-sonnet-4-5",
#   "cloud_cost_per_call": 0.024,
#   "quality_delta": 0.03,  # edge is 3% lower quality for this function
#   "recommendation": "Use edge model — quality delta is within tolerance"
# }
```

**Your task:** In the next step, configure your cost controls.
