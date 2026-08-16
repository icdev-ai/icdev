# CUI // SP-CTI

# Per-Provider Cache Effectiveness (cch-obs-01)

**Status:** shipped
**Card:** CCH — provider-agnostic caching, telemetry and observability
**Builds on:** cch-tel-01 (prompt-cache tokens recorded per LLM call)

---

## The defect

The LLM Prompt Cache card showed a single hit rate, and derived
`context_cache_usd_saved` from `llm_response_cache` rows. Neither number could
answer the question an operator actually has: **is prefix caching working, on
which provider, and is it better or worse than last week.**

Two things were wrong with the single number.

**It averaged over providers that do genuinely different things.** Anthropic
and Bedrock cache what a request explicitly marks with `cache_control`. OpenAI
and Azure cache automatically above a token floor with no caller action. Gemini
wants a managed cache object. The Ollama family reuses a KV prefix server-side,
reports no counters for it, and is not billed at all. One average over that mix
is not a summary; it is a blur, and the blur is precisely where the answer was.

**It made four different situations render identically as `0%` / `$0.00`.**

| Situation | What the old card showed | What it means |
|---|---|---|
| Nobody called the provider | `0%`, `$0.00` | Not a hit rate at all |
| Transport reports no cache counters | `0%`, `$0.00` | Unknown, not zero — caching may be working upstream |
| Called, counters reported, all zero | `0%`, `$0.00` | A real measured 0% — **the only defect of the four** |
| Provider is not billed (self-hosted) | `0%`, `$0.00` | No dollars exist to save; `$0.00` reads as failure |

Measured on the live board on 2026-08-16, three of the four were live cases and
none of them was the defect: `ollama` had **10,863 calls** reporting no cache
counters, `claude-cli` **626** (Claude Code caches aggressively on the far side
of that transport and returns no usage counters), and `anthropic` and `bedrock`
had **no traffic at all**. Only `openai`, with 6 calls, was a genuine measured
zero. The old card reported all of them the same way.

---

## What shipped

`tools/cache_savings/by_provider.py` reads **`ai_telemetry`** — the per-call
ledger cch-tel-01 taught to record `cache_creation_input_tokens` and
`cache_read_input_tokens` — and reports each provider separately.

It deliberately does **not** read `llm_response_cache`. That table answers a
different question ("was an LLM call avoided outright") and holds a row only
for results that were themselves response-cached, so it can never describe
cached *input* tokens on a call that still happened. Prefix caching lives in
the per-call ledger or nowhere.

### Four states, never merged

```
no_data        zero calls in the window            cached_share_pct = None
unreported     calls, but no counters reported     cached_share_pct = None
no_cache_hits  calls, counters reported, all zero  cached_share_pct = 0.0
caching        cache tokens observed               cached_share_pct = <rate>
```

`None` versus `0.0` is the load-bearing distinction and it is in the payload,
not merely in a tooltip: a caller formatting `cached_share_pct` cannot
accidentally print `0%` for a provider nobody called.

**Measurement outranks declaration.** A provider declared silent that
nonetheless reports non-zero cache tokens is `caching`. The config only decides
how to read a *zero*, which is the sole ambiguous case.

### Dollars are withheld when there are none to claim

`usd_basis` records *why* a provider shows no money:

- `priced` — a list rate is declared; savings are computed net of the cache-write premium.
- `local` — self-hosted inference is not billed per token. **`usd_saved` is `None`, and the observed average latency is reported instead.** Printing `$0.00` would render a working, unbilled cache identically to a failed one.
- `unpriced` — genuinely billed, but no verified per-token rate is declared (a subscription-billed CLI bridge, or a vendor nobody has priced). Tokens are reported; dollars are withheld.

### The correctness bug the aggregate was hiding

Providers disagree about what `input_tokens` includes:

- **disjoint** (Anthropic, Bedrock) — `input_tokens` *excludes* cache reads and writes. Total prompt = `input + read + write`.
- **inclusive** (OpenAI, Azure) — `prompt_tokens_details.cached_tokens` is a *subset* of `prompt_tokens`. Total prompt = `input`; uncached = `input − read`.

Given identical raw numbers (1,000 input tokens, 400 cache reads) the true
cached share is **40.00%** under inclusive accounting and **28.57%** under
disjoint. Summing both shapes into one rate — which one aggregate number
necessarily does — double-counts every OpenAI cached token. That is not a
rounding error; the aggregate reports cache reads it has already counted.

`totals` therefore carries counts per state and a dollar sum, and **no blended
hit rate**. A test asserts that the key never reappears.

### Trend

Each provider is compared against the previous window of equal length:
`improved` / `worsened` / `flat` / `no_baseline`, with the delta in percentage
points. A window with no comparable measurement reports `no_baseline`, never a
0% baseline.

### A database with no history is unmeasurable

A fresh worktree or an ephemeral CI database would otherwise report every
declared provider as `no_data` — a wall of findings about a database that has
simply never been used. When neither the current nor the previous window holds
a row, the result is `measurable: false` with a reason, and
`usd_saved_total: None` rather than `0.0`.

---

## Surfaces

Existing surfaces were extended; no second surface was added.

| Surface | Change |
|---|---|
| `/cache-savings` | New **Prefix Cache by Provider** table. Only `caching` and `no_cache_hits` get the red/amber/green scale; `no_data` and `not reported` are neutral and say why on hover. |
| `GET /api/cache-savings/by-provider` | Full payload; optional `?window_days=N`. |
| `GET /api/cache-savings/tile` | New `by_provider` block: counts per state plus the busiest three by name. Never a blended rate. |
| Home page **LLM Prompt Cache** tile | Renders those counts as chips and names the top providers; a provider without a rate shows `no data` or `not reported`, not `0%`. |
| IQE | New `cache.by_provider` collection, alongside `cache.stats` / `cache.entries`. |

---

## Configuration

`args/cache_effectiveness.yaml` holds the claims, **keyed by provider and never
by model** — a model id in a YAML value pins one vendor into the platform the
same way a hardcoded `model=` does in code, and this file is not covered by the
AST gate that catches the code half. A test asserts it.

Per provider: `capability` (`explicit` / `automatic` / `managed` / `server_kv` /
`opaque` / `unknown`), `token_accounting`, `reports_cache_tokens`
(`true` / `false` / `null`), `usd_basis`, and for priced providers
`input_usd_per_mtok` with `read_multiplier` / `write_multiplier` and a
`pricing_verified_on` date. The multipliers are the stable half; the base rate
is the one that drifts and the one to re-verify.

An unreadable config degrades every provider to `unknown`, which reads a zero
as `unreported` rather than fabricating a measured 0%.

---

## Commands

```bash
python tools/cache_savings/by_provider.py --json
python tools/cache_savings/by_provider.py --window-days 30
python tools/cache_savings/by_provider.py --provider anthropic --json
```

## Tests

`tests/test_cache_effectiveness_by_provider.py` — 21 tests, in-memory SQLite
driven through the real `translate_sql`, no board / network / LLM. The first
three are the acceptance criteria: a provider with zero traffic and one with
zero cache hits must differ in `status`, in `status_label`, and in
`cached_share_pct` (`None` vs `0.0`).

## NIST 800-53

AU-12 (Audit Record Generation), SA-11 (Developer Testing), SC-28 (Protection at Rest).
