# CUI // SP-CTI

# hcx-vv-01 — the reverse-direction proof for `request_context`

**Card:** hcx-vv-01 · **Verifies:** hcx-evt-03 · **Status:** shipped

## 1. What this card is

hcx-evt-03 made every context injection leave a `request_context` row in
`agent_session_events`. This card is its acceptance test, and it is written
**backwards on purpose**: it must FAIL if the event log ships inert.

The distinction matters because of the shape of the failure it is guarding
against. "Some `request_context` rows exist after a real turn" is not evidence
of coverage — it is the assertion that *hides* a coverage gap. A log with three
of four injectors instrumented produces rows, populates the dashboards, passes
every existing test, and is silently wrong about one block of text that reached
the model. A partially-covered log is more dangerous than an absent one, because
it reads as coverage.

So the test does not start from the log. It starts from **the system prompt the
provider was actually handed** and requires the log to account for all of it.

## 2. What it found

**There were four injectors, not three.**

hcx-evt-03 instrumented the three that live in `tools/agent_runtime/` and that
`AgentRuntime._effective_system_prompt` assembles:

| Source | Module | Block |
|---|---|---|
| `project_context` | `tools/agent_runtime/project_context.py` | `CLAUDE.md` / `AGENTS.md` / `memory/MEMORY.md` + project state |
| `goal_context` | `tools/agent_runtime/goal_context.py` | the operator's active standing goals |
| `profile_memory` | `tools/agent_runtime/profile_memory.py` | durable profile facts, preferences, hybrid-memory hits |

The fourth is inside the loop:

| Source | Module | Block |
|---|---|---|
| `agent_loop_memory` | `icdev/tools/llm/agent_loop.py` | the retrieved-memory block, appended **after** the runtime hands the prompt over |

```python
# icdev/tools/llm/agent_loop.py — before this card
if memory_enabled and not resume_session_id and initial_messages is None:
    _mem_ctx = _retrieve_memory_context(user_prompt, memory_top_k, memory_tier)
    if _mem_ctx:
        system_prompt = system_prompt + "\n\n" + _mem_ctx
```

It is the **last text added before the request goes out**, and it is the one
block the runtime cannot see: by the time it is appended, `run_turn` has already
handed its prompt over and returned control to the loop. Downstream, nothing can
tell the retrieved block apart from the text it was appended to. It produced no
`request_context` row, and nothing anywhere went red — three out of four, and the
log looked complete.

That is the exact defect this card was written to catch, and it was found by
asking the question in the other direction.

## 3. The fix

A fifth hook on `run_agent_loop`, in the same family as the four it already has:

```python
ContextInjectionHook = Callable[[str, str, dict[str, Any]], None]
MEMORY_INJECTION_SOURCE = "agent_loop_memory"

run_agent_loop(..., on_context_injection=<hook>)
```

* **A hook, not a direct write.** `icdev.tools.llm.agent_loop` must not acquire a
  dependency on the audit layer in order to stay honest about what it sends —
  the same reasoning that makes `on_pre_tool_use` and `on_stop` hooks.
* **Announced after the append**, so the hook can never be the reason the block
  is or is not in the prompt.
* **Never raises.** `_announce_context_injection` catches everything and logs at
  WARNING. This is the same posture `context_events.record_injection` takes one
  layer up, restated here because this module does not import that one and so
  inherits none of its guarantees. An audit sink that fell over must not become a
  refusal to answer.
* **Not called when nothing was retrieved.** An injector with nothing to say
  injected nothing; a row saying otherwise would fabricate coverage rather than
  measure it, which is why `context_events` counts `skipped_empty` apart from
  `failed`.

`AgentRuntime._record_loop_injection` is the consumer: it records the block
beside the other three, under the same `session_id` (the chat `context_id`) and
the same `correlation_id`. `agent_loop_memory` is registered in
`context_events.SOURCES`, so `coverage()` reports it and an unregistered-source
warning is not triggered.

## 4. The test

`tests/agent_runtime/test_context_events_live_turn.py` — 17 tests, gated in this
PR, no skips, no network, no LLM.

**Arrange.** Every source in `SOURCES` is made genuinely active with real data:
a real `CLAUDE.md` on disk, a real ACTIVE standing goal in `sag_standing_goals`,
a real preference in `sag_user_profiles`, a real hybrid-memory hit. Nothing about
the injection or the recording path is stubbed — only the data each injector
reads. `agent_session_events` is built from the migration's own `up.sql`; the two
injector tables are created by their own modules' `_ensure_schema`, because
letting them do it is part of what "the injector ran for real" means.

**Act.** ONE turn through the REAL `run_agent_loop`, with a router that captures
the system prompt it was asked to send.

**Assert.**

1. `{event.source} == {injector that produced a block}` — set equality, not
   "rows exist".
2. Per source, `event.body_sha256 == compute_payload_hash(block)`. Naming the
   source is necessary and not sufficient: an event naming `goal_context` while
   carrying the project block satisfies every set comparison and describes the
   wrong injection.
3. Every recorded body is IN the prompt the provider received — the log
   describes the model request, not an intention to build one.
4. The loop's block is *exactly* the tail of the prompt (`prompt.endswith(body)`),
   so a truncated body and a body that swallowed the three runtime blocks both
   fail.

**Ground truth, and why it is independent.** For the three runtime injectors it
is `AgentRuntime`'s own per-injector cache; for the loop's it is recovered by
**subtraction** from the prompt that was sent (`sent[len(composed):]`), never by
re-formatting a search result — a re-implementation of
`_retrieve_memory_context` would agree with a broken one. `record_injection`
swallows everything and returns `None`, so it cannot influence what an injector
produces: "this text was injected" and "an event names it" are two separate
facts, and this file is the assertion that the second follows the first.

**It is not vacuous.** Disabling one injector must remove it from the prompt AND
from the log, together; with nothing to inject there are no rows at all and the
turn still completes. A test asserting a constant four sources would pass both,
and would also pass on a build that wrote four rows regardless of what was
injected.

## 5. Test hygiene

Both of these have produced false greens in this repo and are addressed
explicitly:

* **Every module alias is patched.** `tools.X` and `icdev.tools.X` are DISTINCT
  module objects for `agent_runtime` — and the SAME object for `db.storage`,
  which is what makes the difference easy to miss. Patching one leaves the other
  pointing at the live board. `_patch_every_alias` patches all of them, and
  `test_the_alias_hygiene_this_file_depends_on_is_real` asserts the two really
  are distinct rather than trusting a comment.
* **Nothing skips.** A gated test that skips is unmeasured, not passing. Schema
  comes from the migration and from the modules' own `_ensure_schema`, so a
  missing table fails the test instead of being caught by an
  `except OperationalError` and reported green. `args/ci_skip_census.txt` names
  no site in this file, and none exists.
* **The connection translates `%s`.** Every module in this chain authors
  PostgreSQL SQL; a bare `sqlite3` handle raises `near "%": syntax error` inside
  the best-effort `except` each injector wraps its work in, and the file would
  then assert against a no-op it caused itself — reading as "the log is inert"
  when the log is fine and the fixture is broken.

## 6. Recorded RED

`python tools/ci/red_first_gate.py --gate` against merge base `e2c2e2e50c`:

```
tests/agent_runtime/test_context_events_live_turn.py
    merge-base: failed (exit 1)  8 failed, 9 passed
    this tree:  passed (exit 0)  17 passed
tests/agent_runtime/test_context_events.py
    merge-base: failed (exit 1)  1 failed, 36 passed
    this tree:  passed (exit 0)  37 passed
```

The RED is deliberately an **assertion** failure and not a collection error.
`agent_loop.MEMORY_INJECTION_SOURCE` is spelled once more in the test file rather
than imported at module scope, because a hard import would make the file fail to
COLLECT against a tree without the fix — and "the module did not import" is a far
weaker recorded RED than "the log did not account for a block that reached the
model". The two spellings are asserted equal in
`test_the_loop_declares_the_source_the_recorder_registers`.

`tests/agent_runtime/test_context_events.py` changed too: it asserted a literal
three sources, which made it a tripwire for a fourth injector rather than a check
on the ordering it exists to protect. Generalising it to `len(SOURCES)` is only
honest if something still asserts the new source IS there, so
`test_the_loop_is_a_registered_injector` was added in the same file — otherwise
the relaxation is a coverage cut wearing a refactor's clothes.

## 7. Known limitation

`on_context_injection` is wired by `AgentRuntime.run_turn` only. Other callers of
`run_agent_loop` (Cortex, Studio, the rubric loop, ACE) pass no hook, so the
loop's retrieved-memory block is announced and recorded for SAG turns and not for
theirs. That is a smaller gap than the one this card closed — those callers have
no `agent_session_events` session to file an event under at all — but it is a
gap, and it is stated here rather than left for the next reverse-direction test
to find.

## Related

- [phase-hcx-agent-event-log.md](phase-hcx-agent-event-log.md) — hcx-evt-01/02,
  the log and its writer.
- `tools/agent_runtime/context_events.py` — the recorder, the `SOURCES`
  vocabulary and `coverage()`.
- [docs/ci/test-gating-policy.md](../ci/test-gating-policy.md) — why the test
  file is added to `args/ci_test_files/core.txt` in the PR that makes it pass.
