"""
Step 3: Multi-Model Routing
Goal: implement resolve_chain() and pick_model() — an offline fallback-chain resolver
over a config shaped like args/llm_config.yaml. No model is called.
"""

# Same shape as args/llm_config.yaml. blended_per_1k is an illustrative internal
# GPU-time chargeback figure, not a real price.
LLM_CONFIG = {
    "providers": {
        "vllm": {"type": "openai_compatible", "base_url": "http://localhost:8000/v1"},
        "mistral_vllm": {"type": "openai_compatible", "base_url": "http://localhost:8100/v1"},
        "ollama": {"type": "ollama", "base_url": "http://localhost:11434"},
    },
    "models": {
        "qwen-vllm": {"provider": "vllm", "model_id": "Qwen/Qwen2.5-32B-Instruct-AWQ",
                      "blended_per_1k": 0.004},
        "mistral-vllm": {"provider": "mistral_vllm", "model_id": "mistralai/Mistral-Small-Instruct",
                         "blended_per_1k": 0.002},
        "qwen-local": {"provider": "ollama", "model_id": "qwen3:4b", "blended_per_1k": 0.0},
    },
    "routing": {
        "code_generation": {"chain": ["qwen-vllm", "mistral-vllm", "qwen-local"]},
        "default": {"chain": ["mistral-vllm", "qwen-local"]},
    },
    "cost_budget": {"downgrade": {"max_blended_per_1k": 0.0}},
}


def resolve_chain(config: dict, function: str, health: dict, budget_exhausted: bool) -> list:
    """Return the ordered list of model names to try for `function`.

    TODO:
      1. Take routing[function]["chain"], or routing["default"]["chain"] if the
         function is not declared.
      2. Skip chain entries that have no entry in config["models"].
      3. If budget_exhausted: move models whose blended_per_1k is above
         cost_budget.downgrade.max_blended_per_1k (or missing) to the tail.
         Keep order within each group. Never drop a model.
      4. Reorder by provider health: healthy, then "recovering", then "degraded".
         A provider missing from `health` is healthy. Keep order within each group.
    """
    # YOUR CODE HERE
    pass


def pick_model(config: dict, function: str, health: dict, budget_exhausted: bool):
    """Return the first model in resolve_chain(...) whose provider is not degraded,
    or None if every candidate is degraded.

    TODO
    """
    # YOUR CODE HERE
    pass


if __name__ == "__main__":
    print(resolve_chain(LLM_CONFIG, "code_generation", {"vllm": "degraded"}, False))
    print(pick_model(LLM_CONFIG, "code_generation", {}, True))
