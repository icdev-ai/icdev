# Prefix / prompt / context caching across ICDEV's providers

**Measured 2026-08-16.**

Requirement: caching must work with **all** LLM providers — ICDEV is LLM-agnostic.

The finding is that ICDEV does not have a caching *coverage* problem so much as a
**shape** problem. Caching is currently modelled as an Anthropic feature that other
providers happen not to have, when it is really a **provider capability** that four
different vendors express four different ways. Fixing the shape is what makes the
coverage follow.

---

## 0. Three things that get conflated

| | Response cache | Prefix / prompt cache | Context cache |
|---|---|---|---|
| Where | ICDEV's `llm_response_cache` | Provider, implicit on the prefix | Provider, an explicitly stored object |
| Key | Exact match, whole request | A shared prefix | A handle you create and reuse |
| Saves | 100% of a repeated call | ~50–90% of cached input tokens | Same, with a managed TTL |
| Vendor | ICDEV's own | OpenAI/Azure automatic; Anthropic/Bedrock marked | Gemini `cachedContents`, Anthropic 1h |

They are additive. ICDEV's dashboard card mixes the first two —
`resp_cache_usd_saved` vs `context_cache_usd_saved`.

---

## 1. The agnosticism defect

`LLMRequest.cache_control = "ephemeral"` is **Anthropic's wire format**, sitting in the
provider-neutral request object. Every other provider must either recognise a foreign
vendor's vocabulary or ignore it.

That single choice produces the whole current picture:

| provider | sets markers | reports cached tokens | how caching actually works there |
|---|---|---|---|
| `anthropic` | yes | yes | explicit `cache_control` breakpoints, max 4 |
| `bedrock` | yes | yes | explicit, same model |
| `openai` | n/a | **yes** | **automatic** ≥1024-token prefixes; nothing to request |
| `azure_openai` | n/a | yes *(fixed here)* | identical to OpenAI, same SDK object |
| `gemini` | no | no | `cachedContents` API + implicit caching |
| `ollama` | no | no | server-side KV reuse; **latency only, no billing** |
| `ibm_watsonx` | n/a | no | **none — verified 2026-08-16**, no cache field and no counter |
| `oci_genai` | n/a | **yes** *(cch-prov-04)* | **automatic** — `Usage.prompt_tokens_details.cached_tokens` |

Note what the "no" column is really saying. For OpenAI and Azure there is **nothing to
set** — caching is automatic and the only job is reading the number back. For Ollama
there is nothing to bill — a local model has no per-token price, so the payoff is
latency and `cache_read_input_tokens` is the wrong unit entirely.

So "make cache_control work everywhere" is the wrong goal. Three of these providers
would have to ignore it by design.

### The shape that is provider-agnostic

A capability declared by the provider, not a flag set by the caller:

```
PrefixCacheSupport = none | automatic | explicit | managed_object
```

- **`explicit`** (Anthropic, Bedrock): the router asks for caching; the provider decides
  where the breakpoints go.
- **`automatic`** (OpenAI, Azure): the router asks for nothing; the provider reports what
  it cached.
- **`managed_object`** (Gemini): the provider needs a stored handle with its own TTL.
- **`none`** (watsonx — verified 2026-08-16) and **`local`** (Ollama): declared, with the
  reason, so a zero is never mistaken for a defect.

*Built in cch-cap-01 — `tools/llm/provider.py::PrefixCacheCapability`. The
declaration also carries `verified`, because "checked, and the answer is none"
and "never checked" are different facts, and `reports_cache_tokens`, because
caching that fires and is never recorded is indistinguishable from caching that
never fired (§2).*

Every branch normalises into the *same* response fields — `cache_read_input_tokens`,
`cache_creation_input_tokens` — which already exist on `LLMResponse`. The caller says
"this prefix is stable and worth caching"; the provider decides what that means.

---

## 2. Nothing can measure any of it

The strongest finding, and it is independent of provider.

`cache_creation_input_tokens` / `cache_read_input_tokens` are populated on the response
object and stored in `llm_response_cache` — but **no durable telemetry table records
them**. `usage_events` holds 2.2M rows of route-level data with no token columns;
`llm_gateway_audit` and `agent_token_usage` are empty. The only durable token ledger is
`module_budget_usage`, which has no cache columns.

Consequence: `context_cache_usd_saved` on the dashboard can only reflect cache tokens
belonging to responses that were *themselves* response-cached — a subset of a subset.
**If prefix caching started working tomorrow, or silently stopped, the card would look
identical.** That is why the Azure gap below survived: Azure has been serving cached
tokens and discarding the count.

---

## 3. What today's traffic would actually save

Measured across `module_budget_usage` — 1,391 calls, 418,801 tokens, 2026-08-01..12:

```
average       301 tokens/call
maximum     4,096 tokens
>= 1024 tok    63 calls   (4.5%)
```

1024 tokens is the minimum cacheable prefix for both Anthropic (Sonnet/Opus) and OpenAI.
**95.5% of calls are below the threshold at which any vendor's prefix cache can fire**,
and the figure is generous because that column counts input *plus* output while caching
applies only to input.

Models actually used: `qwen3.5:latest` (834 calls, local Ollama) and `kimi-k2.6:cloud`
(557). **Zero Anthropic or OpenAI traffic** in the sample — so the two providers with
working implementations saw none of it, and the 60% on Ollama has no billing to save.

Break-even matters too: a cache write costs ~1.25x normal input and a read ~0.1x, so a
prefix needs roughly **two reads inside its TTL** (5 min default) merely to pay for
itself. The best candidate in the data, `conformance_review` (27 calls, ~3,040 tokens
avg), is spread over twelve days and would rarely put two calls in one five-minute
window.

For scale: the entire measured corpus is ~$1.26 of input at Sonnet pricing.

---

## 4. Recommendation

**Build the agnostic shape and the measurement. Do not tune caching for savings yet.**

The savings case is weak *on today's traffic* — but today's traffic is 60% local models
and 301-token prompts, and that is exactly what changes when agent sessions with
CLAUDE.md-sized system prompts or RAG-heavy canvases route through a cloud provider. The
work below is worth doing because it makes the platform answer the question by itself,
in the shape a multi-provider platform needs, rather than because $1.26 is at stake.

Ordered by value per unit of work:

1. **Durable cache-token telemetry.** Record `cache_creation_input_tokens` /
   `cache_read_input_tokens` per call next to `module_budget_usage`'s accounting.
   Without it every item below is unverifiable, including whether it worked.
2. **Declare the capability per provider** (`none | automatic | explicit |
   managed_object | local`), and have the router consult it instead of setting an
   Anthropic field. This is the agnosticism fix; the rest are its consequences.
   **Done — cch-cap-01.** `PrefixCacheCapability` on every adapter (plus
   `vertex_ai`, the OpenAI-compatible labels and the CLI bridge); the caller now
   sets the neutral `LLMRequest.cache_prefix`, and `apply_prefix_cache` does the
   vendor translation at the invoke seam. See
   [docs/features/cch-cap-01-provider-declared-prefix-cache.md](../features/cch-cap-01-provider-declared-prefix-cache.md).
3. **Read cached tokens wherever the provider already reports them** — the cheapest real
   wins, because no caching has to be *requested*. Azure is **done in this PR**; Gemini
   reports `cachedContentTokenCount` in `usageMetadata`.
4. **Gemini explicit caching** via `cachedContents`, mapped onto `managed_object`.
5. **Ollama**: declare `local` and measure **latency**, not dollars. Prompt-eval time
   with and without a shared prefix is the honest metric for a local model.
6. **watsonx / OCI**: verify vendor support before declaring anything.
   **Done — cch-prov-04, checked 2026-08-16.** One of the two placeholders was
   wrong: watsonx really is `none` (IBM's own SDK models chat usage as three
   counters and carries no cache parameter — zero files with `cach` in the path
   across the whole tree), but **OCI is `automatic`** — its `Usage` object has
   `prompt_tokens_details.cached_tokens` on both response shapes and no
   request-side cache field, which is the OpenAI shape. The reporting half is
   implemented; there is no request half for `automatic`. Fixing the read also
   surfaced that the adapter had been pulling usage off `ChatResult.model_usage`,
   which the SDK has never had — so every OCI call reported zero tokens. See
   [cch-prov-04-watsonx-oci-cache-verification.md](cch-prov-04-watsonx-oci-cache-verification.md).

**Re-run section 3 when either input changes** — cloud-provider traffic appears, or
average prompt size crosses ~1024 tokens. Both flip the conclusion.

---

## 5. Honest limits

- `module_budget_usage` covers **one module** (`generative_intelligence`) over **twelve
  days**. It is the only durable token ledger, not a complete census — which is itself
  finding §2.
- Vendor thresholds and discounts (1024-token minimum, 1.25x write, 0.1x read, OpenAI's
  50% vs Anthropic's 90%) are terms that change and should be re-checked before anyone
  acts on the arithmetic.
- No claim is made that prefix caching *works* today on the 12 canvases and 9 functions
  where `context_cache` is switched on. It cannot be verified either way until §4.1
  exists. That is the point.
