# CUI // SP-CTI

# cch-tel-01 — Durably record prompt-cache tokens per LLM call

## The defect

`LLMResponse` has carried `cache_creation_input_tokens` and
`cache_read_input_tokens` since D-CACHE-10, and four provider adapters populate
them — `anthropic_provider.py:223`, `bedrock_provider.py:180`,
`azure_openai_provider.py:228`, `openai_provider.py:133`. **No durable table
recorded them per call.** Every claim about prompt caching on this platform was
therefore unfalsifiable: Azure served cached tokens and discarded the count for
its entire life, and nothing went red (#1725).

Measured on the live board, 2026-08-16, before this change:

| table | rows | cache columns? |
|---|---:|---|
| `ai_telemetry` | 5,838 (SQLite) / 13,073 (PG) | no |
| `module_budget_usage` | 19 | no — one summed `tokens` figure |
| `agent_token_usage` | 14 | no |
| `llm_gateway_audit` | 0 | no |
| `usage_events` | — | no token columns at all |
| `llm_response_cache` | 113 | **yes** — but only for responses that were themselves response-cached, a subset of a subset |

`llm_response_cache` was explicitly ruled out as the home: it is already the
response cache *and* the savings ledger, and #1725 had to work around that
double duty. A third job would deepen exactly the coupling that caused it.

## What was built

Two columns on **`ai_telemetry`** — the per-call ledger the router already
writes — plus the wiring that makes them true.

```
cache_creation_input_tokens  INTEGER NOT NULL DEFAULT 0
cache_read_input_tokens      INTEGER NOT NULL DEFAULT 0
```

* Migration `20260816135136_ai_telemetry_cache_tokens` (`up.py`/`down.py`) —
  probes the **live** schema (`information_schema.columns` / `PRAGMA
  table_info`) and adds what is missing. Not a raw `up.sql`: PostgreSQL takes
  `ADD COLUMN IF NOT EXISTS` and SQLite does not.
* `tools/db/init_icdev_db.py` — the DDL, for databases that do not have the
  table yet.
* `tools/security/ai_telemetry_logger.py` — two new keyword arguments, both
  named in the INSERT.
* `tools/llm/router.py::_log_telemetry` — reads the two fields off the response.

### One writer, not two

The task's constraint was to write where the router already accounts tokens so
that **one** place knows a call happened. `LLMRouter._log_telemetry` is that
place, and every routing path funnels into it. No second ledger was added.

### The two-tier path recorded nothing at all

Wiring `_log_telemetry` was not sufficient, because the default path on this
deployment never reached it. `two_tier.enabled` is `true` and
`code_generation` is a `worker_function`, so `invoke()` returned from
`_maybe_invoke_two_tier` **before** the telemetry block. A plain
`router.invoke("code_generation", …)` made one to two real provider calls and
wrote **zero** rows — not tokens, not cost, nothing.

Telemetry now fires in `_invoke_model_direct`, which is where two-tier makes
its real provider calls (the qwen3 draft and the tier-2 review, one each).
Per-call is the only honest granularity there: the response two-tier returns is
the *review* alone, so a single row at the return would silently drop the
draft's tokens.

`_invoke_model_direct` gained a `telemetry_function` parameter that is a **label
only**, deliberately separate from `function`. Passing a name in `function` also
arms the routing policy's `force_local` rung, and relabelling a ledger row must
not change which provider a request may reach.

### Absent must not equal zero

Both columns are `NOT NULL DEFAULT 0` and are always written. A provider that
stops serving cached tokens has to look different from one that was never asked.
A `None` from a provider is coerced to `0`; a response lacking the attributes
entirely (a chain-orchestrator aggregate) records `0`. Rows predating the
migration back-fill to `0` — a known floor, distinguishable by `logged_at`.

### Failures are no longer silent

`log_ai_interaction` logged nothing when its INSERT failed; a `return None` was
indistinguishable from "no DB". That is precisely how `module_budget_usage` sat
at 0 rows while reporting success. It now logs a warning — and it earned its
keep immediately, surfacing a stale fixture schema in `tests/test_ai_telemetry.py`
that would otherwise have failed silently.

## Acceptance evidence

A real LLM call through the router, against a database whose `ai_telemetry` was
cloned from the **live** production shape (which lacked the columns) and then
migrated:

```
migration result: {'status': 'applied',
                   'added': ['cache_creation_input_tokens', 'cache_read_input_tokens']}

REAL CALL OK -> ACCEPTANCE-e16c92

ai_telemetry rows: 1
  provider                   : ollama_cloud
  model_id                   : kimi-k2.6:cloud
  function                   : code_generation
  input_tokens               : 1044
  output_tokens              : 7
  cache_creation_input_tokens: 0
  cache_read_input_tokens    : 0
  latency_ms                 : 1390
  logged_at                  : 2026-08-16T14:02:40.082963+00:00
```

That same call wrote **zero rows** before this change. The counts are `0`
because this deployment holds no Anthropic/OpenAI/Azure credentials — the only
reachable providers are Ollama and the CLI bridge, and Ollama reports no prompt
cache. A recorded `0` from a provider that was genuinely asked is the acceptance
criterion's second half, not a gap. Non-zero propagation from response to row is
held by `test_logger_records_reported_cache_tokens`,
`test_router_telemetry_seam_forwards_cache_tokens` and
`test_invoke_model_direct_records_the_call`.

Applied to the live PostgreSQL board the same day: both columns present,
`NOT NULL`, default `0`, all 13,073 existing rows intact.

## Tests

`tests/test_ai_telemetry_cache_tokens.py` — 16 tests, gated in
`args/ci_test_files/core.txt` in this PR. Red-first proof recorded: 16 failed
against the merge base, 16 pass here.

The load-bearing ones are negative:

* `test_zero_cached_tokens_is_recorded_as_zero_not_skipped` — a zero-cache call
  writes a row and the columns hold integer `0`, never NULL, never absent.
* `test_telemetry_function_label_does_not_arm_the_routing_policy` — only
  `function` reaches `_provider_invoke`.
* `test_telemetry_failure_never_fails_the_call` — a broken ledger never takes
  down a served response.
* `test_shipped_ddl_declares_the_cache_columns` reads the DDL out of
  `init_icdev_db.py` rather than copying it, so deleting the columns there
  fails the test instead of leaving a private copy agreeing with itself.

## Found, not fixed

Two adjacent defects were measured during this work and deliberately left out
of scope rather than folded in silently:

1. **The CLI bridge discards cache tokens it is handed.**
   `tools/llm/cli_bridge/subprocess_backend.py:213` reads only `input_tokens`
   and `output_tokens` from the Claude CLI's `usage` payload. That payload does
   carry `cache_read_input_tokens` / `cache_creation_input_tokens` — proven by
   `tools/agents/adapters/claude_cli.py:152-153`, which reads exactly those keys
   from the same object. This is the same discard defect one layer out, and it
   needs its own columns on `cli_llm_jobs`.

2. **Two-tier does not arm `force_local`.** The five `_invoke_model_direct`
   call sites in `_maybe_invoke_two_tier` pass no `function`, so the routing
   policy's `force_local` rung never evaluates on the two-tier path — which
   routes to a cloud tier-2 by design. `args/llm_config.yaml` already warns that
   "two_tier routing is applied BEFORE the chain is resolved". Changing it alters
   routing behaviour and belongs in a security-reviewed change, not a telemetry one.

Also observed: `tools/llm/response_cache_test.py::test_lru_eviction` and
`::test_excluded_functions_not_cached` fail in-suite (not alone) on `main`
today, unrelated to this change.
