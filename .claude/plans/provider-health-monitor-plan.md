# Provider Health Monitor + Adaptive Circuit Breaker

## Goal
Automatically detect token exhaustion / rate limits across LLM providers (Ollama Cloud, Featherless, Anthropic), degrade unhealthy providers, and share load across healthy ones. No manual switching required.

## State of the Art (Existing Patterns)

### Already Working
1. **Two-tier tier2 auto-degrade** (`router.py`): Claude gets rate-limited → `_degrade_tier2_model()` → persisted to `data/llm_degraded_tier2.json` → probed every 5 min → auto-recovers
2. **Per-invocation chain fallback** (`router.py:invoke`): when a model fails, marks `_availability_cache[model_name]=False` and tries next model. But this is *per-call* and doesn't persist or deprioritize the *provider*.
3. **Kanban executor degrade** (`kanban.py`): `_degraded_executors` set skips `claude_cli` when token-exhausted. But Ollama and Featherless are not tracked.

### Gaps
- Ollama Cloud token exhaustion is NOT auto-detected at router level
- No provider-level circuit breaker (only model-level and executor-level)
- No load sharing / round-robin across healthy providers
- `availability_cache` is TTL-based (30 min), not error-driven

## Proposed Architecture

### 1. Provider Health Tracker (router.py)

New module: `tools/llm/provider_health.py` (or inline in router.py)

```python
class ProviderHealthTracker:
    """Circuit breaker for LLM providers.

    Tracks per-provider success/failure over a rolling window.
    When failure rate exceeds threshold, mark provider DEGRADED.
    Auto-probe degraded providers every N seconds.
    Auto-recover when probes succeed.
    """
```

**States:**
- `HEALTHY` — normal operation, all models from this provider are tried in chain
- `DEGRADED` — provider is failing; ALL its models are deprioritized to end of chain
- `RECOVERING` — periodic probes running; if probe succeeds → HEALTHY

**Metrics (per provider, rolling window):**
- success_count, failure_count
- last_failure_at, last_success_at
- consecutive_failures
- failure_rate (failures / total in last N minutes)

**Auto-degrade triggers:**
- 3 consecutive failures
- OR failure rate > 50% over last 5 minutes
- OR rate-limit error (429, quota exceeded, token limit)

**Auto-recovery:**
- Probe every `probe_interval` (default 5 min, override from error `reset` header)
- 3 consecutive successful probes → HEALTHY

### 2. Adaptive Chain Reordering (router.py:invoke)

When building the fallback chain for a function:

1. Start with config chain order
2. For each model, check its provider's health
3. If provider is DEGRADED, move ALL its models to end of chain
4. If provider is RECOVERING, keep in chain but after HEALTHY providers
5. Within same provider, preserve original order
6. Persist degraded state to `data/llm_provider_health.json` (cross-process)

**Example:**
```
Config chain: [qwen3-local(ollama), claude-sonnet(anthropic), gpt-4o(openai), qwen(featherless)]
If ollama is degraded: [claude-sonnet, gpt-4o, qwen, qwen3-local]
If anthropic is also degraded: [gpt-4o, qwen, qwen3-local, claude-sonnet]
```

### 3. Kanban Scheduler Integration (kanban.py)

**Option A: Router-driven** (recommended)
- Kanban already calls `LLMRouter.invoke()` for LLM-backed dispatches
- The router's adaptive chain reordering handles provider switching automatically
- No kanban changes needed for LLM dispatch path

**Option B: Executor-aware provider health**
- Add `_degraded_providers` alongside `_degraded_executors`
- When `ollama_cloud` provider is degraded, skip `ollama_local` executor tier
- When `anthropic` provider is degraded, skip `claude_cli` executor tier
- Use router's provider health as source of truth

We implement **Option A + a thin Option B wrapper**:
- Kanban reads `LLMRouter.get_provider_health()` before dispatch
- If the target provider is degraded, kanban routes to the next executor tier

### 4. Load Sharing (Round-Robin within Healthy Providers)

Within the HEALTHY provider group, add optional round-robin load sharing:

- `load_sharing.enabled: true` in `llm_config.yaml`
- `load_sharing.mode: round_robin | weighted | latency` (default: round_robin)
- When enabled, models from HEALTHY providers are shuffled round-robin per function
- Prevents hammering a single provider
- Weighted mode uses pricing/capacity weights from config

### 5. Error Detection Enhancements

Expand `_is_rate_limit_error()` to detect provider-specific patterns:

- **Ollama Cloud**: `"insufficient_quota"`, `"rate_limit"`, `"429"`, `"token limit"`
- **Featherless**: `"usage limit"`, `"billing"`, `"rate_limit_exceeded"`
- **Anthropic**: `"rate_limit_error"`, `"429"`, `"overloaded"`

Parse `X-RateLimit-Reset` / `retry-after` headers from provider responses to set intelligent probe intervals.

## Files to Modify

| File | Changes |
|------|---------|
| `icdev/tools/llm/router.py` | Add `ProviderHealthTracker`, integrate into `invoke()` chain building, persist/load health state |
| `icdev/tools/llm/provider.py` | Add `LLMRateLimitError` with `reset_at` timestamp; propagate HTTP headers |
| `icdev/tools/llm/openai_provider.py` | Raise `LLMRateLimitError` with `retry-after` / `X-RateLimit-Reset` on 429 |
| `icdev/tools/llm/ollama_provider.py` | Raise `LLMRateLimitError` with `retry-after` on 429 / quota errors |
| `icdev/tools/llm/anthropic_provider.py` | Raise `LLMRateLimitError` with `retry-after` on 429 |
| `icdev/tools/genesis/reflexes/kanban.py` | Read router provider health; degrade `ollama_local` executor when `ollama_cloud` provider degraded |
| `args/llm_config.yaml` | Add `provider_health:` section with thresholds, probe intervals, load sharing settings |
| `icdev/tools/llm/provider_health.py` | **NEW** — `ProviderHealthTracker` class |
| `tests/test_llm_router.py` | Add tests for provider health, auto-degrade, auto-recovery, load sharing |

## Config Changes (args/llm_config.yaml)

```yaml
provider_health:
  enabled: true
  # Auto-degrade triggers
  degrade_after_consecutive_failures: 3
  degrade_after_failure_rate: 0.5          # 50% failure rate in window
  degrade_window_seconds: 300              # 5-minute rolling window
  # Auto-recovery
  probe_interval_seconds: 300              # 5 minutes default
  recovery_after_consecutive_successes: 3
  # Load sharing
  load_sharing:
    enabled: true
    mode: round_robin                      # round_robin | weighted | latency
    # Optional weights (default: equal)
    provider_weights:
      featherless: 1.0
      ollama_cloud: 1.0
      anthropic: 1.0
      ollama: 1.0
```

## Implementation Order

1. **Phase 1**: `provider_health.py` core + `router.py` integration (chain reordering)
2. **Phase 2**: Provider error propagation (add `reset_at` to `LLMRateLimitError`)
3. **Phase 3**: Kanban scheduler reads provider health for executor tier decisions
4. **Phase 4**: Load sharing (round-robin)
5. **Phase 5**: Tests + config defaults

## Rollback Plan

- `provider_health.enabled: false` in config → restores original chain-based routing
- Degraded state is persisted to JSON but ignored when disabled
- All changes are additive; no existing interfaces removed

## Acceptance Criteria

1. When Ollama Cloud returns 429, all `ollama_cloud` models are deprioritized within 1 call
2. Featherless models take priority when Ollama Cloud is degraded
3. Degraded provider is auto-probed every 5 min; recovers after 3 successful probes
4. Kanban scheduler skips `ollama_local` executor when `ollama_cloud` provider is degraded
5. Router logs provider health transitions (degraded/recovered) at INFO level
6. Config toggle `provider_health.enabled` disables the entire feature cleanly
