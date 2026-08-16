# CUI // SP-CTI

# Ollama declares `local`, and is measured in LATENCY — not dollars (cch-prov-03)

**Shipped 2026-08-16.** Epic `PROV` of the `cch` card. Builds directly on
[cch-cap-01](cch-cap-01-provider-declared-prefix-cache.md), which introduced
`PrefixCacheCapability` and the `local` support level.

---

## The problem

60% of measured LLM traffic on this platform runs on a local model through Ollama
(834 of 1,391 calls), and `tools/llm/ollama_provider.py` had no cache handling at
all. The obvious reading — "a gap; wire up the Anthropic mechanism" — is wrong, and
acting on it would have made the platform less honest, not more.

A locally hosted model has **no per-token price**. There is no invoice for a cached
prefix to discount. So `cache_read_input_tokens` is not merely zero for Ollama, it
is the **wrong instrument**: it will read 0 whether caching is working perfectly or
not running at all. Any dollar figure derived from it is meaningless.

What Ollama *does* have is server-side KV-cache reuse for a repeated prompt prefix,
and the payoff is real. It is just denominated in **time**.

### What was actually on the card

Worse than a misleading zero. `tools/cache_savings/savings.py` priced **every** row
with Anthropic's rate card regardless of which provider produced it:

```python
_IN  = 3.00 / 1_000_000      # $3.00/MTok
_OUT = 15.00 / 1_000_000     # $15.00/MTok
resp_saved = avoided * (inp * _IN + out * _OUT)
```

Measured on this deployment, 2026-08-16 — `SELECT provider, ... FROM llm_response_cache GROUP BY provider`:

| provider | entries | hits | in_tok | out_tok | credited |
|---|---|---|---|---|---|
| `ollama` | 1 | 2 | 193 | 226 | **$0.0040** |

One local call, served twice from ICDEV's own response cache, credited with $0.0040
of savings against a rate card belonging to a different vendor, for inference nobody
was ever billed for. Small in absolute terms and entirely fabricated — which is the
defect class this whole card exists to close. The task brief said it directly: *do
not invent a dollar figure for local inference to make the card look uniform.*

---

## What shipped

### 1. The declaration (verified, inherited from cch-cap-01)

`OllamaProvider.prefix_cache_capability` returns `PREFIX_CACHE_LOCAL` with a written
reason, and — importantly — only for a **local endpoint**. `_is_local_endpoint`
inspects the configured `base_url`, so the `ollama` provider (loopback) and the
`ollama_cloud` provider (ollama.com, billed) give opposite answers from the same
class. "Nothing to bill" would be a false claim about a hosted endpoint, so that one
declares `none` with `verified=False`.

This task pins the declaration under test so the reason cannot be quietly deleted.

### 2. `prompt_eval_ms` — reading the metric that actually moves

`LLMResponse` gained:

```python
prompt_eval_ms: Optional[float] = None
```

`None` is load-bearing. It means *this provider does not report prefill time*, which
is a different fact from a measured `0.0`. Only the Ollama adapter populates it
(non-streaming and streaming); every other adapter leaves it `None` rather than
claiming a zero nobody measured.

**`prompt_eval_count` is deliberately not used as the hit signal.** Measured across
one cold call and four warm ones, it stayed pinned at **1,914 tokens**: Ollama
reports the full prompt length whether or not it re-evaluated it. Only the *duration*
moves. A "cached tokens" figure derived from the count here would be fiction — the
same mistake in a new unit.

### 3. The card says "not applicable"

`get_savings_stats` gained a `by_provider` section. Each row resolves the provider's
**declared** capability — via `router.prefix_cache_capability_for_provider(name)`,
which goes through the router's own construction rather than a second hand-maintained
name→capability table that would drift from the adapters — and then:

| declared | dollars shown | why |
|---|---|---|
| `local` | **`None` → "not applicable"** | no per-token price; wrong unit |
| `none` | `$0.00` | a billed provider that cached nothing really saved nothing |
| `automatic` / `explicit` / `managed_object` | computed | dollars are the right unit |

`none` and `local` are kept apart on purpose. Collapsing them would relabel every
unverified provider "not applicable" and hide genuine cache misses — the opposite of
the point.

Because it keys off the declaration rather than the string `"ollama"`, `vllm`,
`mistral_vllm` and `localai` — which cch-cap-01 also declared `local` — are covered
with no further work.

**The withheld amount is reported, not silently dropped.** A headline that quietly
shrinks is its own kind of dishonesty, so the summary carries `gross_usd_saved`,
`usd_withheld_local` and `local_providers`, and the card renders *"Excludes $0.0040
from ollama — locally hosted, never billed."*

Fail-safe: a provider the config does not know stays **monetary**. "Nobody declared
this" must not become a free pass to "not applicable".

### 4. The measurement

`tools/llm/ollama_prefix_latency.py` — re-runnable, so this is a measurement the
platform can repeat rather than a claim in a PR body.

```bash
python tools/llm/ollama_prefix_latency.py --json
python tools/llm/ollama_prefix_latency.py --model qwen3:4b --repeats 7
```

It drives `OllamaProvider.invoke()` rather than raw HTTP, so it measures the path
ICDEV actually runs — and gives `prompt_eval_ms` a consumer. A field nothing reads is
indistinguishable from a field never set, which is this platform's signature bug.

---

## Measured result

**2026-08-16, this deployment.** Prefix ≈1,900 tokens (60 paragraphs), `num_predict`
minimal, `temperature` 0, model already resident. Median of 5 alternating pairs,
**three consecutive runs per model**:

| model | cold prefill | warm prefill | speedup |
|---|---|---|---|
| `qwen3:4b` | 440–471 ms | 20.2–21.0 ms | **21.8x – 22.7x** |
| `qwen3:0.6b` | 103–278 ms | 16.5–23.0 ms | 4.6x – 16.8x |

The `qwen3:4b` figure is the reliable one: three runs landed within 7% of each
other on both legs. `qwen3:0.6b` is reported as a range because its prefill is
short enough that background GPU contention dominates — one run's cold leg read
`[235.1, 495.9, 102.7, 377.9, 277.6]`. **The warm leg is stable in both models**
(16–23 ms regardless of load), which is itself the useful result: KV reuse removes
the part of the cost that varies.

USD saved: **not applicable**, in every run, by design.

### A measurement bug found by re-running it

The first version of this tool used fixed cold seeds (`COLD0`…`COLD4`) and reported
`qwen3:0.6b` 64.5 → 9.1 ms (7.1x). That single reading was real, and it was also the
only correct one the tool could ever produce: **Ollama's KV cache outlives the
process**, so on every subsequent run those "cold" prefixes had already been
evaluated. An immediate re-run reported:

```
cold_raw: [10.8, 9.4, 10.1, 70.6, 69.7]   warm_raw: [13.5, 16.2, 16.0, 17.5, 18.0]
speedup: 0.7x
```

Warm-versus-warm, presented with exactly the same authority as the real
measurement. A tool that works once and then quietly reports noise is worse than no
tool, because nothing about the second reading looks wrong.

Fixed by `cold_nonce()` — the cold seed is a per-run UUID, so no run can inherit
another's prefixes. The warm prefix is deliberately left stable across runs, since a
prefix the server already holds is precisely what the warm leg is for. Pinned by
`test_the_cold_seed_is_unique_per_run` and
`test_cold_prefixes_differ_between_two_runs`.

### Method, and why each step is load-bearing

1. **Three discarded warm-up calls first.** The first call after a cold start pays to
   load weights onto the accelerator. Left in, it made an otherwise-warm call read
   **5,148 ms** against a true ~8 ms, dominating whichever leg happened to run first
   and producing a spectacular, meaningless number.
2. **Legs alternate** rather than running in blocks, so thermal drift and background
   GPU contention hit cold and warm equally.
3. **Cold means a prefix never sent before — by any run.** A per-run nonce, not a
   fixed seed (see above). The seed also varies in the *first* characters, because
   KV reuse matches a leading prefix: a seed buried at the end would leave the body
   cached and the "cold" leg would be a slower warm one.
4. **Median, not mean, and print the raw samples.** The first warm sample is a cold
   call by construction, and the cold leg carries real contention outliers. A mean
   lets either dominate at n=5; printing the samples is what let the bug above be
   spotted at all.
5. **`status: unmeasurable` with a reason** when Ollama is unreachable or the endpoint
   is not declared `local`. An unreachable server must never read as "caching does not
   help", and the tool exits 0 either way — it is a measurement, not a merge gate.

---

## Scope note

`savings.py` prices every provider with Anthropic's rate card. This task removes the
case where that produces a **fabricated** number (local providers, now withheld and
declared), but a *cloud* provider that is not Anthropic and does report cached tokens
is still mispriced. That is a real, separate defect in the same module; it needs
per-model pricing from `args/llm_config.yaml` and belongs to the `OBS` epic
(`cch-obs-01..02`), not here. Flagged rather than silently half-fixed.

---

## Files

| File | Change |
|---|---|
| `tools/llm/provider.py` | `LLMResponse.prompt_eval_ms`; `prefix_cache_savings_are_monetary()` |
| `tools/llm/ollama_provider.py` | `_prompt_eval_ms()`; populated on invoke + streaming; `_LOCAL_HOSTNAMES` (bandit B104 false positive annotated at the flagged line) |
| `tools/llm/router.py` | `prefix_cache_capability_for_provider(name)` — name → declared capability, fail-safe |
| `tools/llm/ollama_prefix_latency.py` | **new** — the measurement |
| `tools/cache_savings/savings.py` | `by_provider`; `usd_saved=None` for `local`; `usd_withheld_local` |
| `tools/dashboard/templates/cache_savings/page.html` | By-Provider table; "not applicable" + reason; exclusion note |
| `tests/test_ollama_prefix_latency.py` | **new** — 22 tests, no Ollama/network/DB |
| `args/ci_test_files/core.txt` | gated in this PR |
| `icdev/tools/...` | mirrored byte-identically |

NIST 800-53: AU-12 (audit generation), SA-11 (developer testing), SI-4 (monitoring).
