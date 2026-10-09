# Call-site prompts read from the registry — seeding the write path

**Classification:** CUI // SP-CTI
**Task:** `exa-refine-02` (EXA — External Adoption)
**Status:** shipped
**Follows:** [`exa-refine-01`](exa-refine-01-supplemental-prompt-layers.md)

---

## The problem

`exa-refine-01` made `LLMRouter` read **supplemental layers** out of the prompt
registry. That is a read path for prompts the *platform* appends. It is not a
read path for the prompts a *tool* actually sends — those were still f-string
literals at the call site.

So `prompt_versions` had no reason to hold a row, and stayed at 0. A read path
over an empty table is indistinguishable from no read path at all: versioning,
`--diff`, `--rollback` and the append-only `prompt_audit_log` were all still
governing nothing. That is the same declared-but-unconsumed shape the EXA card
exists to close, one layer up.

## What shipped

Three f-string call sites now read their prompt from the registry, and the
registry has a seeded write path so there is something to read.

### The `call_site/` namespace

A third namespace, deliberately neither of the existing two:

| Namespace | Who reads it | Written through the registry? |
|-----------|--------------|-------------------------------|
| `base/` | nobody — the base system prompt comes from `LLMRequest.system_prompt` | **no**, `BasePromptImmutableError` |
| `layer/` | `LLMRouter._apply_prompt_layers`, appended to **every** matching call | yes |
| `call_site/` | the one call site that owns each name, by name | yes |

Putting call-site bodies in `layer/` would have appended them to every LLM call
in the platform; putting them in `base/` is refused outright. Hence a third
name.

**Only the user-message body is registrable.** Where a call site also passes an
`LLMRequest.system_prompt` — `gepa_optimizer` does — that literal stays in the
module. Moving it into the registry would let registry content occupy position 0
of a system prompt, which is precisely what `exa-refine-01` made structurally
impossible. The boundary is enforced by scope, not by a new guard: nothing in
`CALL_SITE_PROMPTS` names a system prompt.

### `render_prompt`

```python
render_prompt(name, default_template, /, **variables) -> str
```

Reads the active template for `name`, renders it, and falls back to the call
site's own module-level template. Consequences, in order of importance:

1. **An unseeded installation is byte-identical** to the f-string that was
   replaced. Nothing about this refactor requires a database.
2. **A registered template that fails to render** — a stray brace, a placeholder
   the call site does not supply — is a bad *row*, not a bad caller, so it
   degrades to the default with a warning instead of taking down the tool.
3. **A broken `default_template` raises.** That is a programming error and must
   be loud.
4. `.format()` does not re-scan substituted values, so a skill file or a trace
   summary containing `{` is data, not template.

Like the layer read, it creates no tables: a missing `prompt_versions`, an
unreachable database and "nothing registered" all mean the same thing — use the
default — and none of them costs DDL on the LLM path. Reads are cached for 30s
and the cache is dropped on every registry mutation, so an `--activate` or a
`--rollback` takes effect on the next call.

### Converted call sites

| Call site | Prompt name | `llm_function` |
|-----------|-------------|----------------|
| `tools/skills/gepa_optimizer.py::_build_patch_prompt` | `call_site/gepa_skill_patch` | `gepa_skill_patch` |
| `tools/workflow/reflexion_agent.py::_build_improvement_prompt` | `call_site/reflexion_improvement` | `code_generation` |
| `tools/nova/skill_generator.py::_build_spec_prompt` | `call_site/nova_skill_spec` | `memory_consolidation` |

Each was an f-string inline in the function that called the router; each is now
a module constant plus a pure builder. Extracting the builder is what makes the
prompt observable at all — previously you could not see it without invoking an
LLM.

**No prompt wording changed.** The only structural change the conversion forced
is that Python conversions and format specs (`{x!r}`, `{x:.2f}`, `{x:.1%}`) are
applied *before* substitution, because a stored template cannot carry them. The
templates keep bare `{name}` placeholders.

`nova`'s deterministic *fallback spec* — the output used when the LLM is
unreachable — stays a module literal on purpose. It must not acquire a
dependency on the database being reachable either.

## Seeding

```bash
# hardprompts/ → prompt_versions (registered as v1 and activated)
python tools/llm/prompt_registry.py --import-hardprompts --json

# the three call-site bodies, at their current module text
python tools/llm/prompt_registry.py --seed-call-sites --json

python tools/llm/prompt_registry.py --list --json
python tools/llm/prompt_registry.py --gate
```

`--seed-call-sites` is idempotent: `register_prompt` deduplicates by SHA-256, so
a re-run against an unchanged tree reports every prompt as `skipped` and writes
no row. It imports the declared modules lazily, so declaring a call site in
`CALL_SITE_PROMPTS` costs nothing at import time and creates no cycle.

Iterating on a prompt is then ordinary registry work — and revertable:

```bash
python tools/llm/prompt_registry.py --register --name "call_site/nova_skill_spec" \
    --template-file /path/to/v2.md --function memory_consolidation --json
python tools/llm/prompt_registry.py --activate --name "call_site/nova_skill_spec" --version 2 --json
python tools/llm/prompt_registry.py --diff --name "call_site/nova_skill_spec" --v1 1 --v2 2 --json
python tools/llm/prompt_registry.py --rollback --name "call_site/nova_skill_spec" --to-version 1 --json
```

## Tests

`tests/test_prompt_registry_call_site_prompts.py` — 24 tests.

The load-bearing ones are byte-identity, and they are written to be a real
proof rather than a restatement: each test file holds a **verbatim second copy**
of the pre-refactor f-string, and asserts the builder reproduces it exactly in
both states that matter —

- **empty registry** (every install that never seeds), and
- **seeded registry** (every install that does) — which is what makes running
  `--seed-call-sites` against a live system safe: turning the read path on
  changes not one byte of what is sent.

Byte-identity on its own, though, is also exactly what an *inert* read path
looks like. So the inverse is asserted too:

- registering and activating a different template **changes** the rendered
  prompt at the call site;
- `--rollback` changes it back;
- a draft (unactivated) version is not applied.

Plus the failure modes — an unrenderable registered template falls back, a stray
brace falls back, a broken default raises, an unreachable database still renders
— the namespace invariants (`call_site/` names are neither base nor layer names,
and seeding them registers **no** router layers for any function including `*`),
idempotency, and an end-to-end CLI run asserting `--list` returns rows and
`--gate` passes after seeding.

## Files

| File | Change |
|------|--------|
| `tools/llm/prompt_registry.py` | `CALL_SITE_PREFIX`, `is_call_site_name`, `_read_active_template`, `get_active_template`, `render_prompt`, `CALL_SITE_PROMPTS`, `seed_call_site_prompts`, `--seed-call-sites` |
| `tools/skills/gepa_optimizer.py` | `PATCH_PROMPT_TEMPLATE` + `_build_patch_prompt` |
| `tools/workflow/reflexion_agent.py` | `IMPROVEMENT_PROMPT_TEMPLATE` + `_build_improvement_prompt` |
| `tools/nova/skill_generator.py` | `SPEC_PROMPT_TEMPLATE` + `_build_spec_prompt` |
| `args/capability_consumption.yaml` | `prompt_registry` known-inert note updated |
| `args/ci_test_files/core.txt` | new test file added to the required set |
| `tests/test_prompt_registry_call_site_prompts.py` | new |

`icdev/tools/*` mirrored.
