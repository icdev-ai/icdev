# Provider Token Budget + CLI-Bridge Final Fallback

## Problem

`OLLAMA_API_KEY` (and other provider API keys) are set in `.env`, but ICDEV™ routes requests through the Claude Code CLI bridge (`claude-cli`) instead. The current router treats the CLI bridge as a front-of-chain fallback whenever no "cloud" key is detected, and it has no concept of provider-level token exhaustion.

Desired behavior:

1. Any provider with a configured API key and remaining token budget is eligible for routing.
2. When a provider's monthly token/cost budget is exhausted, the router stops using it until the budget resets.
3. Only after **all** providers are exhausted does the router fall back to the CLI bridge.
4. Users can reset a provider's budget (monthly rollover or manual).

## Root Causes (Current Code)

- `tools/llm/cli_bridge/activate.py` only recognizes `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `AZURE_OPENAI_API_KEY`, and `IBM_CLOUD_API_KEY` as cloud keys. `OLLAMA_API_KEY`, `MISTRAL_API_KEY`, `MISTRAL_VLLM_API_KEY`, and `VLLM_API_KEY` are missing, so the CLI bridge auto-enables when only those keys are present.
- The CLI bridge is **prepended** to every routing chain when `should_enable()` returns True, so `claude-cli` always wins even if Ollama Cloud is configured.
- `tools/llm/router.py` availability checks (`_check_model_available`) only probe network/model reachability; they never consult spend vs. budget.
- `tools/llm/cost_intelligence.py` tracks `agent_token_usage` / `agent_token_budgets` per **agent**, not per **provider**.
- There is no `llm_provider_budgets` table or provider-level circuit breaker for token exhaustion.

## Immediate Workaround (Applied Now)

Until the full implementation lands, the WebUI can use `OLLAMA_API_KEY` by:

1. Adding `OLLAMA_API_KEY` (and the other key env vars) to `CLOUD_KEY_ENV_VARS` in `tools/llm/cli_bridge/activate.py` so the CLI bridge does **not** auto-enable when those keys are present.
2. Adding `ollama_cloud` to the dashboard BYOK provider dropdown and `PROVIDER_ENV_MAP`.
3. Adding `ollama_cloud` to `_BYOK_PROVIDERS` so a stored BYOK key also counts as a cloud key.

This is a tactical fix; the full plan below makes the behavior robust and configurable.

## Full Implementation Plan

### Phase 1 — Provider-Level Budget Schema

Add a new table `llm_provider_budgets`:

```sql
CREATE TABLE IF NOT EXISTS llm_provider_budgets (
    provider TEXT PRIMARY KEY,
    month TEXT NOT NULL,
    budget_usd REAL NOT NULL DEFAULT 0.0,
    spent_usd REAL NOT NULL DEFAULT 0.0,
    warning_threshold REAL NOT NULL DEFAULT 0.8,
    exhausted INTEGER NOT NULL DEFAULT 0,
    exhausted_at TEXT,
    reset_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

- `provider` matches the provider logical name in `args/llm_config.yaml` (`anthropic`, `openai`, `ollama_cloud`, `ollama`, `mistral`, etc.).
- `budget_usd` comes from a new `provider_budgets` section in `args/llm_config.yaml` (default can be infinite/`0` = unlimited).
- `spent_usd` is incremented by the router after every successful invocation, using model pricing from config (same logic as `agent_token_usage`).
- `exhausted` is set to `1` when `spent_usd >= budget_usd`.
- `reset_at` is the ISO timestamp when the budget is expected to reset (monthly rollover or manual reset).

Add to `tests/conftest.py` `MINIMAL_ICDEV_SCHEMA` so tests do not break.

### Phase 2 — Budget-Aware Availability Check

In `tools/llm/router.py`:

1. Extend `_check_model_available(model_name)` to also check the provider's budget state.
   - If `exhausted = 1` and `reset_at` is in the future → model unavailable.
   - If `budget_usd <= 0` → unlimited, available.
2. Add a helper `_is_provider_budget_available(provider_name: str) -> bool` that reads `llm_provider_budgets`.
3. Cache the budget check with a short TTL (e.g., 60 s) so the DB is not hit on every call.

### Phase 3 — CLI Bridge as Final Fallback

In `tools/llm/cli_bridge/activate.py`:

1. Remove the unconditional prepend behavior.
2. Introduce `CLI_FALLBACK_ONLY: bool = True` in `args/llm_config.yaml` under a new `cli_bridge:` section.
3. When `cli_fallback_only` is true, `prepend_cli_to_chains` is replaced by `append_cli_to_chains` — `claude-cli` is added to the **end** of every chain if it is not already present.
4. Keep the context override (`cli_bridge_override(True/False)`) for per-request bypass/force.
5. `_has_cloud_key()` continues to treat any provider with a key as "cloud" so the bridge does not auto-enable prematurely.

### Phase 4 — Token Exhaustion Rotation in Router

In `tools/llm/router.py:invoke()`:

1. Build the function chain as today.
2. Filter out models whose provider is exhausted or degraded.
3. Try each remaining model in priority order.
4. On a rate-limit / token-exhaustion error, mark the provider exhausted and retry the next model.
5. If all models fail, append `claude-cli` (when enabled) and try it once.
6. If the CLI bridge also fails (or is disabled), raise `LLMUnavailableError`.

Add provider-level degradation state persistence (JSON file or DB table) so the router remembers exhaustion across restarts.

### Phase 5 — Spend Recording

In `tools/llm/router.py:_log_telemetry()` or a new `_record_provider_spend()`:

1. After each successful invocation, compute cost from `input_tokens`, `output_tokens`, and `cache_*_tokens` using model pricing.
2. Insert into `llm_provider_budgets` (upsert per provider+month):
   ```sql
   INSERT INTO llm_provider_budgets (...) VALUES (...)
   ON CONFLICT(provider) DO UPDATE SET
     spent_usd = spent_usd + excluded.spent_usd,
     updated_at = excluded.updated_at;
   ```
3. If the new `spent_usd >= budget_usd`, set `exhausted = 1` and `exhausted_at`.

### Phase 6 — Budget Reset API/UI

Add a small admin/status API:

- `GET /api/llm/provider-budgets` — list provider budgets and exhaustion status.
- `POST /api/llm/provider-budgets/<provider>/reset` — reset `spent_usd = 0`, `exhausted = 0`, `reset_at = now`.

Wire into the dashboard on a new or existing admin/settings page so ops can manually un-exhaust a provider.

Monthly auto-reset: in `tools/llm/router.py` or a Genesis reflex, at month boundary update `month` and reset `spent_usd` / `exhausted`.

### Phase 7 — Config Defaults

Add to `args/llm_config.yaml`:

```yaml
provider_budgets:
  enabled: true
  # default monthly USD cap per provider (0 = unlimited)
  default_budget_usd: 0
  providers:
    anthropic:
      budget_usd: 100.0
    openai:
      budget_usd: 50.0
    ollama_cloud:
      budget_usd: 20.0
    mistral:
      budget_usd: 20.0
    # local providers are unlimited by default
    ollama:
      budget_usd: 0
    vllm:
      budget_usd: 0

cli_bridge:
  # When true, claude-cli is only tried after all other providers fail.
  fallback_only: true
  # Still auto-enable when no cloud keys are present and no Ollama is reachable.
  auto_enable: true
```

### Phase 8 — Tests

Add tests in `tests/test_llm_router.py`:

1. `OLLAMA_API_KEY` only → CLI bridge does not auto-enable.
2. Provider budget exhausted → model skipped, next provider used.
3. All providers exhausted → CLI bridge used.
4. CLI bridge disabled + all exhausted → `LLMUnavailableError`.
5. Budget reset → provider becomes available again.
6. Spend recording increments `llm_provider_budgets.spent_usd`.

## Files to Modify

| File | Change |
|------|--------|
| `tools/llm/router.py` | Budget-aware availability, provider exhaustion rotation, spend recording, CLI fallback ordering |
| `tools/llm/cli_bridge/activate.py` | Treat `OLLAMA_API_KEY`/others as cloud keys; add `fallback_only` mode (append vs prepend) |
| `tools/llm/cost_intelligence.py` | Add `llm_provider_budgets` table helpers, reset logic, monthly rollover |
| `tools/llm/provider.py` | Potentially add `LLMRateLimitError` exhaustion signal |
| `tools/db/schema/pg_consolidated.sql` | Add `llm_provider_budgets` schema |
| `tools/db/init_icdev_db.py` | Ensure table is created on fresh install |
| `tests/conftest.py` | Add `llm_provider_budgets` to minimal schema |
| `args/llm_config.yaml` | Add `provider_budgets` and `cli_bridge.fallback_only` sections |
| `tools/dashboard/byok.py` | Add `ollama_cloud` / `ollama_cloud` to env map and BYOK provider list |
| `tools/dashboard/templates/profile.html` | Add `ollama_cloud` option to BYOK dropdown |
| `icdev/tools/dashboard/byok.py` | Mirror root BYOK changes |
| `icdev/tools/dashboard/templates/profile.html` | Mirror template changes |
| `tools/dashboard/api/admin.py` or new API | Provider budget reset endpoint |

## Acceptance Criteria

- [ ] With only `OLLAMA_API_KEY` set in `.env`, the router selects an `ollama_cloud` model before `claude-cli`.
- [ ] After `ollama_cloud` spend reaches its monthly budget, the router skips all `ollama_cloud` models.
- [ ] After all providers are exhausted, the router tries `claude-cli` exactly once.
- [ ] If `claude-cli` is also unavailable, invocation raises `LLMUnavailableError`.
- [ ] Dashboard BYOK dropdown includes **Ollama Cloud** and stores/uses `OLLAMA_API_KEY`.
- [ ] A reset API/UI clears `exhausted` for a provider and makes it routable again.
- [ ] Monthly rollover auto-resets provider budgets.
- [ ] All changes have pytest coverage; no regression in existing routing tests.

## Rollback

- Set `provider_budgets.enabled: false` to restore current chain-based routing.
- Set `cli_bridge.fallback_only: false` to restore current prepend behavior.
- Delete/reset `llm_provider_budgets` rows to clear exhaustion state.

## Notes

- Local providers (`ollama`, `vllm`, `mistral_vllm`) default to unlimited budget because they consume local resources, not tokens.
- The existing per-agent budgets in `agent_token_budgets` remain; `llm_provider_budgets` is an additional cross-cutting guard.
- This plan intentionally does **not** change provider error handling (rate limits, 429s) beyond marking a provider exhausted on token-budget events. The existing `ProviderHealthTracker` / tier2 degradation logic stays and can be reused.
