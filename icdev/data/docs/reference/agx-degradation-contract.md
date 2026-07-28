CUI // SP-CTI

# AGX Architecture — LLM-Agnosticism & Degradation Contract (agx-core-02)

> Companion to `docs/spikes/agx-00-agentic-architectures-adaptation.md` and the
> `tools/llm/architectures/` registry (agx-core-01). Enforced by
> `tools/workflow/coherence_checker.py::check_architecture_agnosticism` and
> `tests/llm/test_architecture_agnosticism.py`.

## Why categorical outputs make portability achievable

Upstream (FareedKhan-dev/all-agentic-architectures) defaults to Nebius and wires
providers through LangChain. ICDEV's provider-agnosticism — 9 providers, air-gap
Ollama routing, CUI egress rules — is a hard-won property that this work must
**preserve, not dilute**.

The causal link, stated plainly: a free-form `rate this 0.0–1.0` prompt yields
**incomparable numbers** across model families — a 70B and a 7B disagree on the
scale, not just the answer. A 3-value enum (`{supported, contradicted,
unsupported}`) yields the **same token** from both. Categorical outputs
(agx-pick-*) are therefore not a nicety; they are the portability layer that lets
one architecture run identically on a frontier model and a local 7B. This is why
the deterministic-picker discipline and LLM-agnosticism are the same project.

## The enforced contract

Every module under `tools/llm/architectures/` (and its `icdev/` mirror):

1. **No vendor-SDK / framework imports.** Zero `import anthropic|openai|langchain*|
   groq|cohere|mistralai|boto3|...`. All inference flows through
   `LLMRouter.invoke(function, request)` / `invoke_for_role(...)`.
2. **No hardcoded model IDs.** No `claude-*`, `gpt-4o`, `gemini-*`, `llama-3`,
   etc. string literals. Models resolve from `args/llm_config.yaml` via
   `resolve_llm_config_path()`.
3. **No direct provider instantiation.** No `SomethingLLMProvider(...)` call that
   bypasses the router.
4. **Multi-model diversity from config.** Fan-out steps use
   `router.get_diverse_models(role_key, count)`, never a hardcoded vendor list.
5. **Embeddings via `get_embedding_provider()` only.**
6. **CUI stays LOCAL-ONLY** per the `api_key_env` local-vs-cloud distinction.

Violations fail the coherence gate (`check_architecture_agnosticism`) and the
unit gate (`test_architecture_agnosticism.py`).

## Degradation contract

An architecture that only works on a frontier model is **not shippable**. Every
architecture MUST complete air-gapped (`ICDEV_LLM_PROVIDER=ollama`,
`two_tier.enabled=false`, no cloud fallback). "Complete" may mean returning an
honest **degraded** envelope — it must never mean raising, hanging, or fabricating
a verdict. `ArchitectureResult.degraded=True` with empty `output` is the truthful
signal; `stop_reason` names the cause.

| Failure mode | Contract |
|---|---|
| **No provider available** (air-gap, no cloud fallback) | Return `degraded=True`, `output=""`, `stop_reason="unavailable"`. Never raise. |
| **Budget exceeded** mid-run | Return `degraded=True`, `stop_reason="budget_exceeded"` via the existing `BudgetExceededError` path. Never present a truncated result as complete. |
| **Malformed structured output** from a small local model | The categorical vocabulary is kept to 3–5 values so a 7B hits it reliably. When parsing still fails, the deterministic fallback applies the **conservative** enum value (the one that does not silently pass a gate — e.g. `unsupported` for a citation verdict, `complex` for a retrieval-complexity route) and marks `degraded=True`. A malformed judgment must never be read as a passing judgment. |
| **Programming error** (`TypeError`/`ValueError`/`AttributeError`) | Re-raised, not degraded — these are bugs, not runtime conditions, and must surface in tests/CI. |

The distinction in the last two rows is deliberate: runtime unavailability and
budget pressure are **operational** conditions that degrade gracefully; malformed
structured output degrades to the **safe** enum; genuine programming errors
propagate so they are caught before merge.

## Where new architectures plug in

agx-verify-*, agx-rag-*, agx-search-*, and agx-bench-* register into the
agx-core-01 registry and inherit this contract automatically — the gate scans the
whole package, so a new architecture that hardcodes a model or imports a vendor
SDK fails CI the moment it lands.
