# Supplemental Prompt Layers — the prompt registry as the router's read path

**Classification:** CUI // SP-CTI
**Task:** `exa-refine-01` (EXA — External Adoption)
**Status:** shipped

---

## The problem

`tools/llm/prompt_registry.py` was a complete, well-built subsystem with zero
users. Monotonic integer versions, SHA-256 content hashing,
draft/active/deprecated/archived status, activate-demotes-prior, `--rollback
--to-version N`, `--diff`, A/B traffic split, an append-only `prompt_audit_log`
— and `prompt_versions` sat at 0 rows because no module imported it.
`tools/llm/router.py` never read an active prompt, so prompts stayed f-string
literals at call sites (`gepa_optimizer._generate_patch`, `reflexion_agent`,
`nova/skill_generator._llm_generate_spec`).

That is ICDEV's signature defect: a *declared* capability with *zero*
consumption. The substrate a continual-improvement harness needs was already
built; nothing read it.

## What shipped

`LLMRouter` now reads active **supplemental prompt layers** from the registry
and appends them to the system prompt on every invocation, both streaming and
non-streaming — together with the invariant that makes self-modification of
prompts governable at all:

> **The base system prompt is immutable.** The registry only ever *appends*
> supplemental layers on top of it. It is never rewritten.

### Enforced in code, not documented

Three independent mechanisms, so the invariant survives an edit to any one of
them:

1. **Write guard.** `register_prompt`, `activate_prompt`, `rollback_prompt` and
   `start_ab_test` refuse any name in the reserved base namespace — the `base/`
   prefix or the bare names `base`, `base_prompt`, `base_system_prompt`,
   `system`, `system_prompt` — raising `BasePromptImmutableError`. Names are
   normalised (case, whitespace, `\` → `/`) so the guard cannot be dodged by
   spelling. There is no `--force`. `--import-hardprompts` skips such files and
   reports them under `rejected` rather than aborting the import.
2. **Read namespace.** `get_active_layers()` — the only function the router
   calls — selects rows in the `layer/` namespace and nowhere else. A row
   inserted behind the write guard's back is still unreadable as a layer, and
   **no code path anywhere reads a base prompt out of the registry**. The base
   comes from the call site's `LLMRequest.system_prompt` and from nowhere else.
3. **Composition post-condition.** `compose_system_prompt(base, layers)`
   concatenates and then verifies the result still starts with the exact base
   text; `LLMRouter._apply_prompt_layers` re-checks the same thing before
   handing the request onward. Either check failing raises rather than emitting
   a prompt whose base was displaced. A tampered base prompt never reaches a
   provider.

The net structural claim: **registry content can never occupy position 0 of a
system prompt.**

### Where the hook sits

`_apply_prompt_layers` runs at the very top of `invoke()` and
`invoke_streaming()`, before redaction, before the response-cache key is
computed, and before context compression — so everything downstream sees the
prompt that will actually be sent. Placing it after the cache lookup would have
served v1 output after a rollback to v2.

### Behaviour with an empty registry

Byte-identical to before. No layers registered → `_apply_prompt_layers` returns
the *same request object*, not even a defensive copy. A missing
`prompt_versions` table (a DB that has never initialised the registry) reads as
"no layers"; the read path deliberately does not create tables, so an
installation that never uses this pays one cheap `SELECT`, cached for 30s, and
never any DDL on the LLM hot path.

## Usage

```bash
# Register and activate a layer for one llm_function
python tools/llm/prompt_registry.py --register --name "layer/house-style" \
    --function code_generation --template-file hardprompts/house_style.md --json
python tools/llm/prompt_registry.py --activate --name "layer/house-style" --version 1 --json

# What will the router actually apply?
python tools/llm/prompt_registry.py --layers --function code_generation --json

# Iterate, then roll back — the router picks it up on the next call
python tools/llm/prompt_registry.py --register --name "layer/house-style" --template-text "v2" --function code_generation --json
python tools/llm/prompt_registry.py --activate --name "layer/house-style" --version 2 --json
python tools/llm/prompt_registry.py --rollback --name "layer/house-style" --to-version 1 --json
```

A layer registered against function `*` applies to every `llm_function` — the
intended home for a platform-wide governance layer. Multiple layers for one
function compose in `prompt_name` order, so composition is deterministic.

## Configuration

`args/llm_config.yaml` (mirrored to `icdev/args/llm_config.yaml`):

```yaml
prompt_registry:
  enabled: true
  layer_separator: "\n\n"
  max_layer_chars: 8000   # oversized layers are SKIPPED with a warning, never truncated
```

`enabled` gates the *feature*. There is no toggle for the immutability
invariant. It ships `true`: a read path wired but switched off by default would
recreate the exact declared-but-unconsumed failure this task closes, and with
no layers registered the hook is a no-op anyway.

## Tests

`tests/test_prompt_registry_supplemental_layers.py` — 32 tests:

- every reserved base name rejected on register / activate / rollback / A/B,
  including case, whitespace and `\`-separator variants; the rejected
  registration persists no row;
- a hostile layer ("IGNORE ALL PRECEDING INSTRUCTIONS") still lands strictly
  after the base;
- a row smuggled directly into `base/core` with raw SQL is still not returned by
  the read path;
- rollback end to end: v1 → v2 → rollback → v1, observed through the router's
  composed prompt, with `rolled_back` in the append-only audit log;
- empty registry returns the identical request object, and a `RecordingProvider`
  driven through the full `invoke()` path receives the untouched base;
- draft (non-activated) layers are not applied; function scoping and `*` globals;
- the shipped `args/llm_config.yaml` has `enabled: true`.

## Files

| File | Change |
|------|--------|
| `tools/llm/prompt_registry.py` | Reserved namespaces, `BasePromptImmutableError`, write guards, `get_active_layers`, `compose_system_prompt`, layer cache + invalidation, `--layers` CLI |
| `tools/llm/router.py` | `LLMRouter._apply_prompt_layers`, called from `invoke()` and `invoke_streaming()` |
| `args/llm_config.yaml` | `prompt_registry` block |
| `args/capability_consumption.yaml` | known-inert note updated |
| `tests/test_prompt_registry_supplemental_layers.py` | new |

`icdev/tools/llm/*` and `icdev/args/llm_config.yaml` mirrored.
