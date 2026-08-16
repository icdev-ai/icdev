# Providers declare prefix-cache support; the router stops setting an Anthropic field

**Task:** `cch-cap-01` · **Shipped:** 2026-08-16 · **Classification:** CUI // SP-CTI

## The defect

`LLMRequest.cache_control = "ephemeral"` is Anthropic's wire format, and it sat
in the provider-neutral request object. `LLMRouter._apply_context_cache` stamped
it there for every canvas and function configured with `context_cache: true` —
before the chain is routed, so before anyone knows which vendor will serve the
call. Every other provider then had to recognise a foreign vendor's vocabulary
or ignore it.

"Make `cache_control` work everywhere" was never the goal. Three of the eight
providers would have to ignore it *by design*: OpenAI and Azure cache prefixes
automatically with nothing to request, and a local Ollama has no per-token bill
for a cache to reduce.

## The shape

Caching is a **provider capability**, declared by the provider — not a flag the
caller stamps on the wire.

```python
PrefixCacheCapability(
    support="none" | "automatic" | "explicit" | "managed_object" | "local",
    reason=...,                  # required — an undocumented "none" is not evidence
    verified=True | False,       # "checked, and the answer is none" != "never checked"
    reports_cache_tokens=...,    # does this adapter read the vendor's counter back?
)
```

The caller's job shrinks to one neutral assertion — `request.cache_prefix = True`,
*"this prefix is stable and worth caching"* — and the provider decides what that
means.

| Provider | Support | Verified | Reports cache tokens |
|---|---|---|---|
| `anthropic` | `explicit` | yes | yes |
| `bedrock` | `explicit` | yes | yes |
| `openai` | `automatic` | yes | yes |
| `azure_openai` | `automatic` | yes | yes |
| `gemini` | `managed_object` | yes | no |
| `ollama` (local endpoint) | `local` | yes | no |
| `ibm_watsonx` | `none` | yes *(cch-prov-04)* | no |
| `oci_genai` | ~~`none`~~ → `automatic` | yes *(cch-prov-04)* | yes *(cch-prov-04)* |

> Both rows shipped here as `verified=False` placeholders and were checked on
> 2026-08-16 by **cch-prov-04**. watsonx held; **OCI did not** — its `Usage`
> object carries `prompt_tokens_details.cached_tokens` and it has no
> request-side cache field, which is the `automatic` shape. See
> [docs/research/cch-prov-04-watsonx-oci-cache-verification.md](../research/cch-prov-04-watsonx-oci-cache-verification.md).

Also declared, outside the assessment's eight: `vertex_ai` → `managed_object`
(unverified — it serves Gemini, so it is Gemini's shape); `vllm` / `mistral_vllm`
/ `localai` → `local`; `mistral` / `gateway` / an unlisted OpenAI-compatible
label → `none`, unverified; the CLI bridge → `none` (the vendor CLI subprocess
owns whatever caching happens, and returns no token accounting); a **hosted**
Ollama endpoint → `none`, unverified, because "latency only, nothing to bill" is
false for a billed endpoint.

`none` and `local` are first-class answers with a written reason, not gaps to be
filled in later.

## Where it is wired

A declaration nobody consults is inert surface, so the consumer ships in the
same change:

- `LLMRouter._apply_context_cache` sets **only** `request.cache_prefix`.
  Per-canvas / per-function enablement semantics are untouched.
- `LLMRouter._provider_invoke` and `_provider_invoke_streaming` — the two seams
  where the provider instance is finally known — call
  `provider.apply_prefix_cache(...)`, which asks the declared capability what to
  do. `explicit` gets `cache_control="ephemeral"`; every other level gets
  nothing, and a foreign marker is **stripped** rather than forwarded, so a
  fallback from Anthropic to Gemini cannot carry Anthropic's vocabulary onto
  Gemini's wire.
- `_rag_augment` inserts the `<!-- cache_breakpoint -->` marker on the neutral
  intent (it runs before the provider is known, so it cannot read a vendor
  field).

`apply_prefix_cache` copies rather than mutates — the same request object is
retried down the fallback chain.

## Behaviour for Anthropic and Bedrock is unchanged

Proven, not asserted: `test_anthropic_payload_is_byte_for_byte_what_it_was_before`
builds the request the old way (caller stamps `cache_control`) and the new way
(neutral intent, translated by the capability), runs both through the adapter
with a captured client, and compares the kwargs that would go on the wire.

## What this does NOT do

- No enablement semantics changed — which canvases and functions cache is the
  same config, and a separate argument.
- Gemini's `cachedContents` handle is declared, not implemented (assessment
  §4.4). Ollama's latency measurement is declared, not implemented (§4.5).
- **Nothing durable still records cache tokens** (assessment §2). Until that
  exists, whether prefix caching fires cannot be verified either way — which is
  why the assessment ranks telemetry first.

## Tests

`tests/test_prefix_cache_capability.py` (37 assertions), including:

- every one of the eight providers declares a level from the closed vocabulary,
  with a reason substantial enough to be evidence;
- `verified=False` is distinct from a verified `none` (cch-prov-04 then checked
  both named vendors, so the assertion now names the endpoints that are
  genuinely unchecked rather than watsonx/OCI);
- the router's source is parsed and **any** assignment to `.cache_control`
  fails the test — a behavioural check only covers the paths it walks;
- `explicit` providers still receive the marker; the other six receive no vendor
  field at all.

`tests/test_llm_cache_integration.py` was updated to assert the neutral intent
and that `cache_control` stays empty.

## References

- `docs/research/prefix-caching-assessment.md` §1 (the agnosticism defect), §4.2
- `tools/llm/provider.py` — `PrefixCacheCapability`, `apply_prefix_cache`
- `context/llm_cache_policy.md` — the caller-facing contract
