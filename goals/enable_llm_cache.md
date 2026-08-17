# Goal: Enable LLM Response Cache

## Problem
ICDEV™ invokes LLMs thousands of times per day. Many prompts are repeated:
- RAG-augmented queries with identical context blocks
- Code generation with similar instructions
- Narrative generation with stable templates
- Per-canvas analysis that reuses system prompts

Repeated prompts waste tokens and increase latency. Cost intelligence already
detects repeat patterns (`cache_responses` recommendation in
`llm_cost_recommendations`), but nothing acts on them.

## Solution
Deterministic response cache + provider-level context caching.

- **Response cache**: SHA-256 keyed PostgreSQL UNLOGGED table. Stores complete
  LLMResponse objects. Lazy TTL eviction + eager LRU sweep. 0–50 ms jitter on
  hit for InputSnatch side-channel mitigation.
- **Context cache**: provider-declared prefix caching (cch-cap-01). The caller
  sets `LLMRequest.cache_prefix = True`; each provider declares what it supports
  (`none | automatic | explicit | managed_object | local`) and the router
  translates at the invoke seam. On `explicit` providers (Anthropic/Bedrock)
  that marks the system prompt and last user message with
  `cache_control: {type: "ephemeral"}`; on `automatic` ones nothing is requested
  at all. KV prefix reuse reduces token cost for long contexts where the
  provider bills for them.

## Workflow

1. **Detect** — `cost_intelligence.py` identifies functions with ≥80% repeat ratio.
2. **Enable** — Auto-flip `response_cache.enabled: true` when confidence ≥ 0.9.
3. **Verify** — Dashboard shows cache hit rate per function/canvas.
4. **Report** — Weekly savings report from `llm_cost_recommendations` deltas.

## Tools Used

- `tools/llm/response_cache.py` — Core cache engine
- `tools/llm/router.py` — Cache lookup/store hooks
- `tools/llm/anthropic_provider.py` — Anthropic prompt caching
- `tools/llm/bedrock_provider.py` — Bedrock prompt caching
- `tools/llm/cost_intelligence.py` — Auto-enable logic

## Expected Outputs

- Cache hit rate dashboard widget
- Reduced token spend (target: 20%+ savings on cached functions)
- Faster response times for repeated prompts (sub-100 ms vs multi-second)

## Success Criteria

- Hit rate > 20% for `code_generation` within 7 days of enablement.
- Zero PII leakage via cache (verified by redaction audit).
- Timing jitter prevents cache status inference (InputSnatch defense).
- All 12 canvases configured with per-canvas TTL and context_cache flags.

## Child App Inheritance

Child apps receive `response_cache.py` automatically via `DIRECTORY_TREE`
(`tools/llm/` is snapshotted at scaffold time). No generator changes needed.

## Classification
CUI // SP-CTI
