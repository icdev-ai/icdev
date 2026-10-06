
# Auto-grader for M12 Step 3: Multi-Model Routing
#
# Runs offline. Uses its OWN config (different model names, prices and chains from
# the starter's LLM_CONFIG) so an answer hard-coded to the lesson's example cannot pass.

import copy as _copy

_GRADER_CONFIG = {
    "providers": {
        "vllm": {"type": "openai_compatible"},
        "mistral_vllm": {"type": "openai_compatible"},
        "ollama": {"type": "ollama"},
    },
    "models": {
        "big-vllm": {"provider": "vllm", "blended_per_1k": 0.010},
        "lora-vllm": {"provider": "vllm", "blended_per_1k": 0.003},
        "mistral-vllm": {"provider": "mistral_vllm", "blended_per_1k": 0.001},
        "unpriced-mistral": {"provider": "mistral_vllm"},
        "tiny-local": {"provider": "ollama", "blended_per_1k": 0.0},
    },
    "routing": {
        "summarize": {"chain": ["big-vllm", "ghost-model", "mistral-vllm", "tiny-local"]},
        "agent_builder": {"chain": ["unpriced-mistral", "big-vllm", "tiny-local", "lora-vllm"]},
        "default": {"chain": ["mistral-vllm", "tiny-local"]},
    },
    "cost_budget": {"downgrade": {"max_blended_per_1k": 0.002}},
}

_pristine = _copy.deepcopy(_GRADER_CONFIG)


def _check(label, got, want):
    assert got == want, f"{label}: expected {want!r}, got {got!r}"


# 1. Undeclared function falls back to routing.default.
_check("undeclared function -> default chain",
       resolve_chain(_GRADER_CONFIG, "no_such_function", {}, False),
       ["mistral-vllm", "tiny-local"])

# 2. A chain entry with no models: entry is skipped.
_check("unknown model is skipped",
       resolve_chain(_GRADER_CONFIG, "summarize", {}, False),
       ["big-vllm", "mistral-vllm", "tiny-local"])

# 3. Budget exhausted: over-ceiling and unpriced models go to the tail, order kept,
#    nothing dropped.
_check("budget downgrade is a stable partition",
       resolve_chain(_GRADER_CONFIG, "agent_builder", {}, True),
       ["tiny-local", "unpriced-mistral", "big-vllm", "lora-vllm"])
_check("budget downgrade keeps the cheap model at the ceiling",
       resolve_chain(_GRADER_CONFIG, "summarize", {}, True),
       ["mistral-vllm", "tiny-local", "big-vllm"])

# 4. Health: healthy, then recovering, then degraded; missing provider = healthy.
_check("health ordering",
       resolve_chain(_GRADER_CONFIG, "agent_builder",
                     {"mistral_vllm": "degraded", "vllm": "recovering"}, False),
       ["tiny-local", "big-vllm", "lora-vllm", "unpriced-mistral"])

# 5. Health is applied AFTER budget: availability beats cost.
_check("health after budget",
       resolve_chain(_GRADER_CONFIG, "summarize", {"mistral_vllm": "degraded",
                                                    "ollama": "degraded"}, True),
       ["big-vllm", "mistral-vllm", "tiny-local"])

# 6. pick_model: first non-degraded; recovering is acceptable; None when all degraded.
_check("pick under budget",
       pick_model(_GRADER_CONFIG, "summarize", {}, True), "mistral-vllm")
_check("pick skips degraded, accepts recovering",
       pick_model(_GRADER_CONFIG, "summarize",
                  {"vllm": "recovering", "mistral_vllm": "degraded",
                   "ollama": "degraded"}, False),
       "big-vllm")
_check("pick when every provider is degraded",
       pick_model(_GRADER_CONFIG, "default",
                  {"mistral_vllm": "degraded", "ollama": "degraded"}, False),
       None)

# 7. Resolving must not edit the operator's config.
assert _GRADER_CONFIG == _pristine, "resolve_chain/pick_model must not mutate the config"

print("PASS: fallback chain resolves by function, budget and health -- the router's decision is yours.")
