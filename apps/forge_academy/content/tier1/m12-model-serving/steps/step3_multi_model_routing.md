---
ontology_id: icdev:mission:m12-model-serving:step:3
step_class: icdev:Lab
---

# Lab: Multi-Model Routing

You will write the resolver that decides which model serves a request: given a function name, the health of each inference server and whether the token budget is exhausted, return the ordered fallback chain and the model to call first.

This runs fully offline — no model is called. You are writing the *decision*, which is the part that has to be right before any server sees a request.

## The config

The starter gives you `LLM_CONFIG`, a Python dict with the same shape as `args/llm_config.yaml` (the sandbox has no YAML parser, so it is a dict rather than a file):

```yaml
providers:
  vllm:         {type: openai_compatible, base_url: http://localhost:8000/v1}
  mistral_vllm: {type: openai_compatible, base_url: http://localhost:8100/v1}
  ollama:       {type: ollama,            base_url: http://localhost:11434}
models:
  qwen-vllm:     {provider: vllm,         model_id: Qwen/Qwen2.5-32B-Instruct-AWQ, blended_per_1k: 0.004}
  mistral-vllm:  {provider: mistral_vllm, model_id: mistralai/Mistral-Small-Instruct, blended_per_1k: 0.002}
  qwen-local:    {provider: ollama,       model_id: "qwen3:4b",                     blended_per_1k: 0.0}
routing:
  code_generation: {chain: [qwen-vllm, mistral-vllm, qwen-local]}
  default:         {chain: [mistral-vllm, qwen-local]}
cost_budget:
  downgrade: {max_blended_per_1k: 0.0}
```

`blended_per_1k` here is an illustrative internal chargeback figure for GPU time, not a real price.

## The rules

Implement `resolve_chain(config, function, health, budget_exhausted)`:

1. **Route.** Use `config["routing"][function]["chain"]`; if the function is not declared, fall back to `config["routing"]["default"]["chain"]`.
2. **Drop unknown models.** A chain entry with no entry under `config["models"]` cannot be called — skip it.
3. **Budget.** If `budget_exhausted` is true, move every model whose `blended_per_1k` is *above* `config["cost_budget"]["downgrade"]["max_blended_per_1k"]` to the tail. A model with no `blended_per_1k` counts as above the ceiling (an unknown price is not free). Keep the original order within each group. **Never drop** a model for budget — if the cheap tier is down, the expensive one must still be reachable.
4. **Health.** `health` maps a provider name to `"healthy"`, `"recovering"` or `"degraded"`; a provider not in the dict is healthy. Reorder the chain into healthy models first, then recovering, then degraded, keeping the order from step 3 within each group. Apply this **after** the budget step, so availability beats cost.

Then implement `pick_model(config, function, health, budget_exhausted)`: return the first model in the resolved chain whose provider is **not** degraded, or `None` if every candidate is degraded.

## Example

```python
resolve_chain(LLM_CONFIG, "code_generation", {"vllm": "degraded"}, False)
# -> ["mistral-vllm", "qwen-local", "qwen-vllm"]
pick_model(LLM_CONFIG, "code_generation", {}, True)
# -> "qwen-local"   (budget exhausted: the two priced models move to the tail)
```

## The real seams

Your resolver is a teaching-sized version of code that runs on every ICDEV LLM call:

- `tools/llm/router.py` — `LLMRouter._get_chain_for_function` reads `routing:` and falls back to `default`.
- `tools/llm/provider_health.py` — `ProviderHealthTracker.reorder_chain` moves degraded providers to the end (healthy, then recovering, then degraded).
- `tools/llm/cost_budget.py` — `downgrade_chain` demotes over-ceiling models to the tail and never drops one; the real version also sorts cheaper-first and prefers local models among equals.
- `tools/llm/routing_policy.py` — decides whether data of a given classification may leave for a cloud model at all. It is out of scope for this lab, but in production it runs before any of the above.

## Your task

Open the starter, implement `resolve_chain` and `pick_model`, and run it. The grader checks your functions against its own config and scenarios, so hard-coding the example answers will not pass.
