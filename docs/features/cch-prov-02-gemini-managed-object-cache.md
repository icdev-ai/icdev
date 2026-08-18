# Gemini explicit caching as the `managed_object` capability

**Task:** `cch-prov-02` · **Shipped:** 2026-08-16 · **Classification:** CUI // SP-CTI

## Why this needed its own level

`cch-cap-01` gave providers a closed vocabulary for what "cache this prefix"
means to them. Four of the five levels are cheap to satisfy — `explicit` marks
the request, `automatic` reads a number back, `local` and `none` do nothing with
a written reason.

`managed_object` is the one that needed building. Gemini's explicit caching is
not a flag on a call: it is a **stored object**, `cachedContents`, with its own
identity, its own TTL, and its own lifecycle. There is no breakpoint to mark and
no number to simply read. Something has to create the object, hold the handle
while it lives, and let it expire — which is exactly why a boolean
`supports_prefix_caching` could never have expressed it.

## The economics come first, because they invert the usual assumption

`explicit` and `automatic` cost nothing when they miss. A managed object does
not: it is billed **per token per hour of storage**, whether or not anything ever
reads it. So an object created for a prefix used once is *strictly worse* than no
caching at all — it pays the rent and saves nothing, and the failure is silent.
The object is created, the call succeeds, and the only trace is on the invoice.

Measured on this platform (`docs/research/prefix-caching-assessment.md` §3),
**95.5% of calls sit below even the 1024-token floor** at which any vendor's
prefix cache can fire. Enabling this by default would have been a standing cost
against traffic that cannot benefit from it.

### The thresholds chosen, and why

| Knob | Default | Why this value |
|---|---|---|
| `enabled` | **`false`** | A standing per-hour cost is opted into per deployment, never inherited. |
| `ttl_seconds` | `300` | Short TTL is what makes a twice-used prefix profitable (below). It also bounds the two leaks: rent on an object nothing reads, and the orphan a dying process leaves behind. |
| `min_prefix_tokens` | `4096` | 4× the platform's measured cliff, and comfortably above the vendor's own floor (1024 tokens for Gemini 2.5 Flash, 2048 for 2.5 Pro). The vendor floor is where the API *accepts* the object, not where it pays for itself. |
| `min_sightings` | `2` | Never store a prefix seen once — the one case guaranteed to lose money. |
| `max_objects` | `32` | Every live object is renting; the ceiling is a refusal, not an error. |

**Break-even, stated so it can be re-derived when prices move.** A cached read
costs ~0.25× normal input, so each read saves ~0.75×. Storage costs `S` per
token-hour. Over a TTL of `h` hours with `R` reads, storing pays when:

```
(R - 1) > S · h / (0.75 · P)
```

At Gemini 2.5 Flash's published order of magnitude (`P ≈ $0.30/1M` input,
`S ≈ $1.00/1M/hour`) that is `(R - 1) > 4.4·h` — **~6 reads inside an hour, but
only 2 inside five minutes.** That single line explains both defaults: a 300 s
TTL paired with `min_sightings: 2`. A one-hour TTL would need six reads and is
the wrong trade for this platform's traffic.

Vendor prices and thresholds are terms that change (assessment §5). Re-check them
before acting on the arithmetic; the formula is the durable part.

### "Per surface" is the list that already exists

Which surfaces assert the neutral `cache_prefix` intent is the existing
per-canvas / per-function `context_cache:` config. There is deliberately **no
second allowlist**: size and repetition are then *measured per prefix at runtime*,
which is strictly stronger than a declared list — a surface configured for
caching whose prefix turns out to be 300 tokens, or is never repeated, still
stores nothing.

## The lifecycle

`tools/llm/managed_cache.py` — vendor-neutral, imports no SDK, holds an opaque
handle. The adapter supplies the create and delete calls.

```
decide(model_id, system_prompt, tools) -> ManagedCacheDecision
```

Seven named actions, never a bool:

| Action | Meaning |
|---|---|
| `disabled` | The feature is off. The default. |
| `prefix_below_threshold` | Too small to be worth its rent (with the measured size in the reason). |
| `first_sighting` | Seen once in-window. Deliberately not cached. |
| `create` | Large enough, repeated enough, no live object: store one. |
| `reuse` | A live object covers this prefix; here is the handle. |
| `at_capacity` | The per-process ceiling is full — a refusal, not a failure. |
| `create_failed_cooldown` | A recent create failed; suppressed rather than retried on every call. |

They stay distinct for the reason this codebase keeps re-learning: all seven
produce zero cached tokens, and each sends you somewhere different. Collapsing
them into a bool is how a zero metric becomes unreadable.

The **fingerprint** covers `(model, system_instruction, tools)` — the parts that
repeat verbatim across a surface's calls. Message content is excluded on purpose:
it is what varies, and a fingerprint including it would never be seen twice. It
is model-scoped because a cache object belongs to one model at the vendor, and
NUL-joined so `("ab", "c")` and `("a", "bc")` cannot collide.

**Expiry** is the vendor's, authoritatively — the object dies on its own TTL
regardless. The registry mirrors it so a handle is never offered past its
lifetime, and `expired_handles()` returns them so an adapter can release them
early.

## Caching never fails a call

Four ways it can go wrong, four degradations, all to a normal uncached
invocation:

- **The create is refused** (our chars/4 estimate can sit above the local floor
  and below the vendor's real one) → the call proceeds uncached and the prefix is
  suppressed for a cooldown, so a persistently failing prefix cannot storm the
  API on every request.
- **`from_cached_content` fails** → the handle is dropped and the model is built
  the ordinary way.
- **The vendor already expired the handle** and `generate_content` raises → the
  handle is forgotten and the call is retried **once** without it. An
  optimisation that can fail a user's request is not an optimisation. The retry
  is scoped to the cached path only; a real error (quota, auth) still raises.
- **Two threads both reach `create`** → the loser is told its object was not
  kept and deletes it, rather than leaving it renting with nothing able to
  reference it.

## The tokens land in the shared field

`usageMetadata.cachedContentTokenCount` → `LLMResponse.cache_read_input_tokens`,
the same field Anthropic, Bedrock, OpenAI and Azure report into. Read
**unconditionally**, not only when we created an object: Gemini also caches
implicitly, and a count we did not ask for is still a count worth recording.

`cache_creation_input_tokens` carries the storage event, on the creating call
only, so a read is never counted as a write.

Two honest caveats for anyone summing these:

- Gemini's `prompt_token_count` **includes** the cached tokens (OpenAI's
  convention, the opposite of Anthropic's), so `input_tokens +
  cache_read_input_tokens` double-counts them.
- `tools/cache_savings/savings.py` prices a "write" as Anthropic's 1.25×
  premium. Gemini has no write premium — it charges hourly storage instead. The
  token count is the same fact; the price attached to it is Anthropic-shaped and
  is an approximation for Gemini.

## What this does NOT do

- **No durable, cross-process registry.** The registry is per-process: a second
  worker builds its own object for the same prefix, and a process that exits
  leaves one renting until its TTL. Both costs are bounded by the short TTL —
  the other reason the default is 300 s. A durable registry needs a table and is
  a separate question; it is not needed to make the lifecycle correct.
- **No enablement change.** Which canvases and functions assert `cache_prefix` is
  the same config as before.
- **No `vertex_ai` implementation.** It declares `managed_object` (it serves
  Gemini) but runs through a different adapter; wiring it is the same shape and
  a separate task.
- **Streaming reuses the object but reports no tokens** — the stream yields no
  `LLMResponse`. The object is shared with `invoke()`, which does report.

## Tests

`tests/test_gemini_managed_object_cache.py` (26 tests), gated in
`args/ci_test_files/core.txt`. A fake SDK stands in for `google-generativeai`, so
there is no vendor package, key, network or DB in the loop.

The acceptance, directly: three calls sharing a prefix produce **exactly one**
stored object — sighting, create, reuse — the object carries the configured TTL,
it stops serving once that TTL passes, and `cache_read_input_tokens` comes back
5000 on the reusing call.

Also pinned: the shipped `args/llm_config.yaml` has `enabled: false` (a default
of `False` in the dataclass means nothing if the YAML switches it on); a
malformed config falls back to *off*, never to caching everything; a real API
error still raises; and the vendor handle never lands on `LLMRequest` — checked
behaviourally **and** by parsing the adapter's source for any assignment to a
request attribute, because cch-cap-01's defect in new clothes is exactly what
this feature could have reintroduced.

## Also in this change

`icdev/args/llm_config.yaml` was missing `routing.rag_complexity_classify`, which
the canonical `args/llm_config.yaml` has had since trust-self-03. A pip-installed
wheel therefore fell back to `routing.default` for that function — the precise
defect that entry's own comment describes. The two copies now match.

## References

- `tools/llm/managed_cache.py` — lifecycle and the economics gate
- `tools/llm/gemini_provider.py` — `_resolve_model`, `_create_cache_object`
- `args/llm_config.yaml` — `managed_object_cache:`
- `context/llm_cache_policy.md` — the caller-facing contract
- `docs/research/prefix-caching-assessment.md` §3 (traffic), §4.4 (this item), §5 (limits)
- `docs/features/cch-cap-01-provider-declared-prefix-cache.md` — the capability this implements
