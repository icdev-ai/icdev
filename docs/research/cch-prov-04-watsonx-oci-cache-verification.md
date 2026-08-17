# watsonx.ai and OCI Generative AI: prefix-cache support, verified

**Checked 2026-08-16** (cch-prov-04). Re-check date and citations below are the
point of this document — a `none` with no date is a stale assumption wearing a
finding's clothes.

The [2026-08-16 prefix-caching assessment](prefix-caching-assessment.md) closed
with six recommendations, of which the sixth was "watsonx / OCI: verify vendor
support before declaring anything". cch-cap-01 shipped the declaration shape and
parked both providers at `support=none, verified=False` — deliberately, because
"checked, and the answer is none" and "never checked" are different facts and
the placeholder said which one it was. This is the check.

## Result

| provider | before (cch-cap-01) | after (cch-prov-04) | reports cached tokens |
|---|---|---|---|
| `ibm_watsonx` | `none`, **unverified** | `none`, **verified** | no — there is no counter to read |
| `oci_genai` | `none`, **unverified** | **`automatic`**, verified | **yes — implemented here** |

One of the two placeholders was wrong. That is why the task said not to guess a
level to complete the matrix: an unverified `none` for OCI would have made
cch-obs-01's observability report a confident zero for a provider that has a
cached-token counter in its API.

---

## 1. IBM watsonx.ai — `none`

Three independent reads, all pointing the same way.

**The response has no cached-token counter.** IBM's own Java SDK models the chat
usage object as a three-field record and nothing else:

```java
public record ChatUsage(Integer completionTokens, Integer promptTokens, Integer totalTokens) {}
```

— [`ChatUsage.java`](https://github.com/IBM/watsonx-ai-java-sdk/blob/main/modules/watsonx-ai/src/main/java/com/ibm/watsonx/ai/chat/model/ChatUsage.java),
`IBM/watsonx-ai-java-sdk`, branch `main`, repository last pushed
`2026-08-16T10:43:58Z`. There is no `promptTokensDetails`, no `cachedTokens`, no
`cacheReadInputTokens`.

**The request has no cache parameter.** `ChatParameters`, `BaseChatParameters`
and `TextChatRequest` contain no cache field. Across the *entire* SDK tree —
every module, every package, tests and samples included — **zero files have
`cach` anywhere in their path**. Nothing to ask for.

**The Python SDK agrees.** `ModelInference.chat()` documents `messages`,
`params`, `tools`, `tool_choice`, `tool_choice_option`, `context` and `crypto`.
No caching parameter, and no cached-token field in the documented response —
[watsonx.ai Python SDK reference, v1.6.0a](https://ibm.github.io/watsonx-ai-python-sdk/fm_model_inference.html).

### The trap this walked past

Searching "IBM watsonx prompt caching" returns IBM material in quantity: a
`think/topics` explainer and a tutorial titled *Implement prompt caching by
using LangChain*. Neither is a watsonx.ai API feature. The tutorial's mechanism
is LangChain's `SQLiteCache` — an **application-side response cache**, keyed on
the exact request, which is [row one of the assessment's §0
table](prefix-caching-assessment.md) and is a thing ICDEV already has, as
`llm_response_cache`. Prefix caching is row two: provider-side, implicit on a
shared prefix, priced per cached input token.

Reading the first as evidence for the second is precisely how an unverified
`automatic` gets declared. The SDK's type definitions were used instead because
they are the wire contract, not marketing.

### What would change this

A `cachedTokens`-shaped field appearing on `ChatUsage`, or a cache parameter on
`ChatParameters`. Both are one `git log` away in a public repo. Note also that
watsonx.ai *deployment* on dedicated hardware may run engines with their own KV
prefix reuse — but that is a latency property of the serving stack, not
something the inference API exposes or bills, and it is not what this
declaration is about.

---

## 2. OCI Generative AI — `automatic`

The placeholder was wrong, and the SDK says so plainly.

**The response carries a cached-token counter.** OCI's inference API (version
`20231130`) returns a `Usage` object with five properties — `prompt_tokens`,
`completion_tokens`, `total_tokens`, `completion_tokens_details` and
`prompt_tokens_details`. `PromptTokensDetails` has exactly one property:

```python
self.swagger_types = {'cached_tokens': 'int'}
self.attribute_map = {'cached_tokens': 'cachedTokens'}
```

> Gets the cached_tokens of this PromptTokensDetails.
> **Cached tokens present in the prompt.**

— [`prompt_tokens_details.py`](https://github.com/oracle/oci-python-sdk/blob/master/src/oci/generative_ai_inference/models/prompt_tokens_details.py)
(auto-generated from API version 20231130, © 2016, 2026 Oracle) and the
[SDK reference for `PromptTokensDetails`](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/generative_ai_inference/models/oci.generative_ai_inference.models.PromptTokensDetails.html)
(oci 2.184.1).

**Both response shapes this adapter handles carry it.**
[`CohereChatResponse.usage`](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/generative_ai_inference/models/oci.generative_ai_inference.models.CohereChatResponse.html)
and
[`GenericChatResponse.usage`](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/generative_ai_inference/models/oci.generative_ai_inference.models.GenericChatResponse.html)
are both `Usage`, so Cohere Command R/R+ and the Llama-family generic path are
covered by one read.

**There is nothing to request.** No model in
`src/oci/generative_ai_inference/models/` has `cach` in its name, and the chat
request classes carry no cache field. Caching, if it happens, happens without
being asked for.

Counter reported + nothing to request = **`automatic`**, the same shape as
OpenAI and Azure OpenAI, which the platform already implements.

### Honest limit — stated because it is load-bearing

The SDK proves the counter **exists** and that there is **nothing to request**.
It does not prove the service populates it non-zero for any particular model.
Oracle's service documentation is silent: the
[Generative AI concepts page](https://docs.oracle.com/en-us/iaas/Content/generative-ai/concepts.htm)
does not mention caching at all, and neither does the
[gpt-oss-120b model page](https://docs.oracle.com/en-us/iaas/Content/generative-ai/openai-gpt-oss-120b.htm).
Oracle's developer blog discusses prompt caching as an architecture pattern to
*build*, not as a service guarantee.

That gap is now **measurable rather than assumed**, which is the whole reason to
implement the reporting half: a persistent `cached_tokens == 0` on real OCI
traffic is an answer, and it is a different fact from "we never looked". Before
this change the two were indistinguishable, and would have stayed so forever.

Nothing exercises it yet — the assessment measured **zero OCI traffic** in the
sample (§3), so this reads zero until an OCI model is actually routed.

---

## 3. What was implemented

The reporting half only. There is no request half for `automatic`, by
definition — `apply_prefix_cache` is already a no-op for that level, so no
follow-up card is owed for one. The follow-up that *is* owed is observation, and
it is cch-obs-01's durable cache-token telemetry (assessment §4.1), which this
change feeds rather than duplicates.

`tools/llm/oci_genai_provider.py` (and its `icdev/` mirror):

- `prefix_cache_capability` → `automatic`, `verified=True`,
  `reports_cache_tokens=True`, with the date and citation in the reason.
- New `_read_usage_into()` copies `Usage.prompt_tokens_details.cached_tokens`
  into `LLMResponse.cache_read_input_tokens`.

### A pre-existing bug this had to fix on the way

The adapter read usage from `response.data.model_usage`. `ChatResult` has three
properties — `model_id`, `model_version`, `chat_response` — and `model_usage`
is not one of them
([`ChatResult` reference](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/generative_ai_inference/models/oci.generative_ai_inference.models.ChatResult.html)).
Usage lives on the chat response. So **every OCI call has been reporting zero
input and output tokens**, silently, since the adapter was written — `getattr`
with a default cannot fail loudly.

This is in scope rather than a drive-by: `cache_read_input_tokens` is read off
the same object, and adding a correct sibling read next to a broken one would
have been incoherent. The old attribute is kept as a fallback, not deleted — the
`oci` SDK is not installed in this environment, so the tests mock the shape, and
a fallback costs one `getattr` against being wrong about a library nobody here
can import.

### Two conventions worth not tripping over

- OCI's `Usage` follows the **OpenAI** convention: `prompt_tokens` **includes**
  the cached tokens. Anthropic's excludes them. Do not add
  `cache_read_input_tokens` to `input_tokens` when costing an OCI call.
- OCI reports cache **reads** only. There is no cache-creation counter, so
  `cache_creation_input_tokens` stays 0 for this provider — a real zero, not a
  missing one.

### Not covered

`invoke_streaming` reports no token counts at all, cached or otherwise — it
yields text deltas and a `message_stop`. That is a pre-existing gap across every
counter on that path, not a cache-specific one, and widening it here was outside
what this task asked for.

---

## 4. Where this is pinned

`tests/test_prefix_cache_capability.py`:

- `EXPECTED_SUPPORT` now names `oci_genai` as `automatic`.
- `test_unverified_is_distinct_from_verified_none` asserts **no shipping adapter
  declares `verified=False`** — the unchecked states still exist and are still
  tested (an unlisted OpenAI-compatible label, a hosted Ollama endpoint), but no
  named vendor is left unasked.
- `test_the_two_verified_vendors_cite_a_date` requires `2026-08-16` and
  `cch-prov-04` in both declarations, so the next reader can tell a finding from
  a fossil.
- Five tests over `_read_usage_into` covering the cached read, the
  `ChatResult.model_usage` regression, an absent details block, the legacy
  fallback, and no usage anywhere.
