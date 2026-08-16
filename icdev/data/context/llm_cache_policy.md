# LLM Cache Policy

## Overview

ICDEV™ caches LLM responses to reduce token costs and latency. Two mechanisms:

1. **Response cache** — deterministic key-value store for complete responses.
2. **Context cache** — provider-level KV prefix reuse (Anthropic/Bedrock prompt caching).

## Classification
CUI // SP-CTI

## Cache Design

### Storage

- **Backend**: PostgreSQL UNLOGGED table (primary) or SQLite fallback.
- **Schema**: `llm_response_cache` with JSONB columns for tool calls and structured output.
- **Indexes**: BRIN on `expires_at` (compact, append-optimized); partial B-tree on `function`.
- **Why UNLOGGED**: Cache data is ephemeral. WAL bypass gives 2–5× faster writes.

### Key Strategy

SHA-256 over canonical JSON of:
- `function` name
- `model_id`
- Sorted messages (role + content)
- `system_prompt`
- `temperature` (if != 1.0)
- `max_tokens` (if != 4096)
- `tools` schema
- `output_schema`

Messages are sorted to ensure order-independent keys.

### Eviction

- **Lazy TTL**: `get()` skips rows where `expires_at < NOW()`.
- **Eager LRU**: When row count exceeds `max_entries`, delete 5% overage ordered by `hit_count ASC, created_at ASC`.
- **BRIN caveat**: BRIN is lossy; `get()` rechecks `expires_at < NOW()` to filter false positives.

## PII Handling

- Cache stores **post-redaction** responses.
- De-anonymization happens in `router.invoke()` **after** cache lookup.
- Cache hit returns already-de-anonymized response (skipped on first store).
- No redaction session keys are stored in the cache table.

## Security Mitigations

### InputSnatch Timing Side-Channel

Cache hits are faster than misses. An external observer measuring request latency
could infer cache status. Mitigation:

- Random 0–50 ms jitter applied on every cache hit.
- Jitter is uniform and independent of payload size.

### Stale Responses

Time-sensitive functions are excluded by default:

- `pulse_generation` — time-dependent content
- `news_oracle` — real-time data
- `market_scan` — live market data
- `fathomdesk_trap` — time-sensitive signals
- `browser_automation` — stateful UI operations

Per-function and per-canvas TTL overrides allow shorter lifetimes for volatile data.

## Context Caching (Provider-Level)

Set `request.cache_prefix = True` — "this prefix is stable and worth caching".
That is the whole caller contract; the PROVIDER decides what it means, through
the `PrefixCacheCapability` it declares (cch-cap-01). Do **not** set
`cache_control` yourself: it is Anthropic's wire vocabulary, and the router
derives it at the invoke seam only for providers that declare `explicit`.

| Declared support | Providers | What happens |
|---|---|---|
| `explicit` | anthropic, bedrock | System prompt and last user message get `cache_control: {type: "ephemeral"}`, max 4 breakpoints |
| `automatic` | openai, azure_openai, **oci_genai** | Nothing is requested; the provider caches by itself and `cached_tokens` is read back. OpenAI/Azure cache prefixes ≥1024 tokens; OCI returns `Usage.prompt_tokens_details.cached_tokens` (verified 2026-08-16, cch-prov-04) |
| `managed_object` | gemini, vertex_ai | A stored `cachedContents` object with its own TTL is created, reused and expired by the adapter (cch-prov-02). **Default OFF** — see below |
| `local` | ollama (local), vllm, localai | Server-side KV reuse: a **latency** win, never a billing one |
| `none` | ibm_watsonx, cli, hosted ollama | Nothing to set — with a written reason on each. watsonx is a **checked** none (2026-08-16, cch-prov-04): IBM's chat usage object is three counters with no cached-token field, and there is no cache parameter to set. `verified=False` is reserved for endpoints genuinely nobody has checked, such as an unlisted OpenAI-compatible label |

Every branch normalises into the same `LLMResponse.cache_read_input_tokens` /
`cache_creation_input_tokens` fields, so the savings maths stays vendor-neutral.

Context caching is additive to response caching. A request can hit the response
cache entirely (skipping provider), or miss the response cache but still benefit
from provider-level KV prefix reuse.

### Managed objects are the one shape with a standing cost (cch-prov-02)

`explicit` and `automatic` cost nothing when they miss. A `managed_object` does:
Gemini's `cachedContents` is billed per token **per hour of storage**, whether or
not anything reads it. An object created for a prefix that is used once is
therefore *strictly worse* than no caching — it pays rent and saves nothing,
silently, with the only trace on the invoice.

So `managed_object_cache` in `args/llm_config.yaml` is **`enabled: false`**, and
turning it on is a per-deployment decision. Two further gates are then measured
per prefix at runtime rather than declared per surface:

- `min_prefix_tokens` (default 4096) — 95.5% of this platform's calls sit below
  even the 1024-token floor at which any vendor's cache can fire.
- `min_sightings` (default 2) — nothing is stored on a prefix's **first**
  sighting inside the TTL window.

Break-even, so it can be re-derived when vendor prices move: a cached read costs
~0.25× normal input (saving ~0.75×) and storage costs `S` per token-hour, so over
a TTL of `h` hours with `R` reads, storing pays when `(R - 1) > S·h / (0.75·P)`.
At Gemini 2.5 Flash's published order of magnitude that is `(R - 1) > 4.4·h`:
~6 reads inside an hour, but only 2 inside five minutes — which is why the
default TTL is 300 s rather than an hour. Vendor prices are terms that change;
re-check them before acting on the arithmetic.

Which surfaces participate is the existing per-canvas / per-function
`context_cache` list — the surfaces that assert `cache_prefix`. There is no
second allowlist.

Engine: `tools/llm/managed_cache.py`. Caching never fails a call: a refused
create, an expired handle or a rejected object all degrade to a normal uncached
invocation.

## Operational Runbook

### Clear Cache

```bash
python tools/llm/response_cache.py --clear --json
```

### Warm Cache

```bash
python tools/llm/response_cache.py --warm --function code_generation --limit 50 --json
```

### Toggle

```bash
python tools/llm/response_cache.py --enable
python tools/llm/response_cache.py --disable
```

### Stats

```bash
python tools/llm/response_cache.py --stats --json
```

### Verify After Schema Change

```bash
python tools/llm/response_cache.py --stats --json
python tools/testing/health_check.py --json
```

## Per-Canvas Configuration

All 12 canvases are pre-configured in `args/llm_config.yaml`:

| Canvas | TTL | Context Cache |
|--------|-----|---------------|
| NDC | 7200 s | true |
| SDC | 3600 s | true |
| BDC | 3600 s | true |
| PDC | 3600 s | true |
| ODC | 3600 s | true |
| IDC | 3600 s | true |
| QDC | 3600 s | true |
| MDC | 7200 s | true |
| AADC | 3600 s | true |
| AIMC | 3600 s | true |
| OHC | 3600 s | true |
| DDC | 7200 s | true |

Child apps inherit the same configuration via `DIRECTORY_TREE` snapshot.

## References

- `tools/llm/response_cache.py` — Cache engine
- `tools/llm/router.py` — Integration hooks
- `args/llm_config.yaml` — Configuration
- `hardprompts/cache_routing.md` — Prompt engineering guide
