# CUI // SP-CTI

# AGENT_START / AGENT_END dispatched, and the four dead points named (hcx-live-03)

## What this task closes

`ExtensionPoint` declares ten hook points. hcx-live-01 gave `TOOL_EXECUTE_BEFORE`
its first production dispatcher. This task does two things:

1. **Wires `AGENT_START` and `AGENT_END`** into the standalone agent runtime's
   turn executor, `tools/agent_runtime/runtime.py::AgentRuntime.run_turn`.
2. **Measures and names** the points that still cannot fire, without deleting
   anything.

## Correction to the task premise

The task was seeded on the belief that `AGENT_START` and `AGENT_END` had no
dispatcher anywhere. They had one: `tools/dashboard/chat_manager.py` fires both
via `_dispatch_hook("agent_start", …)` at context creation and
`_dispatch_hook("agent_end", …)` at context close. That call path is invisible
to a grep for `ExtensionPoint.AGENT_START` because `_dispatch_hook` takes the
point as a **string** and resolves `ExtensionPoint(hook_name)` on a variable.

What was true is the other half of the sentence: **no handler is registered on
either point**, from anywhere in the tree. Every auto-loaded builtin in
`tools/extensions/builtins/` declares `chat_message_after` and nothing else. So
the points fired into an empty list.

Two consequences, both kept:

- The wiring below is still the work: the dashboard chat surface and the SAG
  runtime are different surfaces, and a lifecycle handler that wants to see an
  `icdev chat` turn had nothing to attach to.
- The measurement tool had to learn about string-literal dispatch, or it would
  have reported `chat_message_before`, `agent_start` and `agent_end` dead when
  they demonstrably are not. A measurement that over-reports is as useless as
  one that under-reports.

## The wiring

```python
_dispatch_agent_start(self._lifecycle_context(user_input=user_input))
try:
    result = run_agent_loop(...)
except BaseException as exc:
    error = f"{type(exc).__name__}: {exc}"
    raise
finally:
    self._turn_active.clear()
    _dispatch_agent_end(self._lifecycle_context(..., result=result, error=error,
                                                duration_ms=...))
```

Three properties, each pinned by a test:

**Observational, enforced at the call site.** Both points are declared
`allow_modification: false` in `args/extension_config.yaml`. That declaration is
inert — `ExtensionManager.dispatch` reads no per-point config (which is
hcx-live-02's finding). So the property is enforced where it can be: `run_turn`
**discards the dispatch result**. A handler registered with
`allow_modification=True` — the strongest thing a drop-in can claim — that
returns a fully rewritten context changes nothing about the turn's prompt,
routing function or budget. An observational point that can influence a turn is
a new gating surface nobody reviewed; introducing one would have to be a
deliberate edit here.

**`AGENT_END` pairs with its `AGENT_START` on every path.** It fires from a
`finally`, so a provider exception, a `KeyboardInterrupt` or a `stop()`
cancellation all still produce an END carrying `ok: false` and the error string.
A lifecycle point that only fires on the happy path cannot be used to close
anything a handler opened at START.

**A broken handler cannot take a turn down.** Extensions are a layer over the
runtime, not a dependency of it: an unimportable extension package, a missing
enum member and a raising handler each leave the turn untouched.

### Context payload

Scalars only, read defensively off the result — this payload is offered to
third-party drop-in code, so it never hands out the live `AgentLoopResult`
(whose `messages` a behavioural handler elsewhere could mutate) and never raises
while assembling itself.

| Point | Keys |
|-------|------|
| `AGENT_START` | `context_id`, `resume_session_id`, `user_id`, `tenant_id`, `profile`, `llm_function`, `unattended`, `user_input` |
| `AGENT_END` | the above **plus** `duration_ms`, `ok`, `error`, `stopped`, `turns`, `done`, `truncated`, `truncation_reason`, `result_subtype`, `total_input_tokens`, `total_output_tokens`, `total_cost_usd` |

## The measurement — `tools/extensions/liveness.py`

```bash
python tools/extensions/liveness.py            # human report
python tools/extensions/liveness.py --json
python tools/extensions/liveness.py --dead     # only the dead points
python tools/extensions/liveness.py --gate     # exit 1 on an unlisted dead point
```

Two independent pieces of evidence per point:

- **Dispatchers** (static). A file is a dispatcher for point `P` when it both
  names `P` — as `ExtensionPoint.P` *or* as the bare string `"p"` — and calls
  `dispatch`/`dispatch_async`. This is the load-bearing half: **a point with no
  dispatcher cannot fire**, no matter how many handlers register against it, and
  no amount of runtime telemetry would show otherwise.
- **Handlers** (static *and* live). `EXTENSION_HOOKS` keys and `register(...)`
  arguments, plus `ExtensionManager.handler_count`, which sees drop-ins this
  checkout does not contain.

What it deliberately does not do: count dispatches. Runtime dispatch counting is
hcx-live-02's job and belongs inside `ExtensionManager.dispatch`. A point
reported `live` here is **wired**, not necessarily exercised.

Three scan rules worth knowing, because each was a wrong answer first:

- Skip-directory matching is done on the path **relative to the root**. Matching
  on absolute parts made a worktree under `.tmp/worktrees/` skip every file in
  the repository and report all ten points dead.
- The module that **defines** `ExtensionPoint` names every point by
  construction, and is never credited as a dispatcher. A declaration is not a
  consumption — and hcx-live-02 is editing exactly that file.
- Dispatchers under `tests/` and `features/` are reported separately and do not
  count as production wiring. A point kept alive by its own test is dead.

Blind spot, reported rather than papered over: `chat_manager._dispatch_hook`
resolves `ExtensionPoint(hook_name)` on a variable. Files doing that are listed
under `dynamic_dispatch_sites` instead of being credited to every point.

## Measured 2026-08-16

| Point | Status | Dispatchers |
|-------|--------|-------------|
| `tool_execute_before` | live | `tools/agent_runtime/dispatch.py` (+ mirror) |
| `tool_execute_after` | live | `.claude/hooks/post_tool_use.py` (+ bootstrap copy) |
| `chat_message_before` | dispatcher_only | `tools/dashboard/chat_manager.py` (+ mirror) |
| `chat_message_after` | live | `tools/dashboard/chat_manager.py` (+ mirror) |
| `agent_start` | dispatcher_only | **`tools/agent_runtime/runtime.py`** + `chat_manager.py` |
| `agent_end` | dispatcher_only | **`tools/agent_runtime/runtime.py`** + `chat_manager.py` |

**Removed 2026-08-18 (hcx-live-gate-01):** `memory_save_before`,
`memory_save_after`, `compliance_check_before` and `compliance_check_after`.
They were declared from the beginning and dispatched by nothing, so they were
public names with no behaviour behind them. `ExtensionPoint` now declares six
points and `tools/extensions/liveness.py` reports a dead count of **0** —
`args/extension_liveness.yaml`'s census is empty, which is what a census that
only ever shrinks looks like when it finishes.

`dispatcher_only` means the point fires and nothing is listening. That is not
the same defect as `dead`, and the tool does not merge them: a point with a
dispatcher works the moment somebody drops a handler in, while a point without
one is inert whatever they drop.

## Why the four were deleted rather than wired

**Resolved 2026-08-18 by a human decision, as the section below required.** The
choice was between deleting them, wiring `memory_save_*` into
`tools/memory/memory_write.py`, and wiring `compliance_check_*`. Deletion is the
sanctioned resolution under CLAUDE.md — *wire the capability to a consumer or
stop declaring it* — and the two wiring options were each a new governance
surface rather than a wiring change: `memory_save_before` declared
`allow_modification: true`, meaning a handler could suppress a memory write, and
the compliance gates are the controls the platform's ATO evidence rests on.

**The stated risk was smaller than recorded, and was measured before acting.**
The reasoning preserved below says a site-local drop-in naming a removed member
becomes *"an AttributeError at import — a hard startup failure for that
deployment, not a warning."* That is not what the loaders do. Both discovery
paths resolve a point by **value** inside `try/except ValueError`, and the whole
per-file load sits inside `except Exception` with a log line, so such a drop-in
**fails to load and the platform starts normally**. The consequence is a
silently disabled extension — hence the release note — not a dead deployment.
`tests/test_extension_point_removal_is_contained.py` is the standing proof, and
it asserts the property rather than the four names, so the next removal inherits
it.

## Original reasoning: why the four were reported and not deleted

Deleting a declared-but-unconsumed capability **is** the sanctioned resolution
under CLAUDE.md, and it may well be right for all four. It is not a call an
auto-merging PR gets to make.

`ExtensionPoint` is a public `str`-Enum. Extensions are auto-discovered
`NNN_name.py` drop-ins loaded from the `scan_directories` in
`args/extension_config.yaml`, which includes a project-root `extensions/`
directory **that is not in this repository**. A tenant or site-local drop-in
naming one of these members is invisible to any grep of this checkout, and
removing the member turns that file into an `AttributeError` at import — a hard
startup failure, not a warning.

So: the four are enumerated **by name, with a written reason and a follow-up
card**, in `args/extension_liveness.yaml`; `--gate` fails on a dead point that
is not listed; and `tests/test_extension_point_liveness.py::
test_extension_point_members_unchanged` is the guard that this PR did not take
the removal decision by accident. The removal decision itself is **hcx-live-gate-01** — seeded as a manual-mode
gate, so the runner can never dispatch it and delete the members unattended.

## Tests

`tests/test_extension_point_liveness.py` — 12 tests, ~3s, no DB and no network
(the agent loop is monkeypatched, the tree scan is static). Gated in
`args/ci_test_files/core.txt` in this PR, and RED against the merge base.

Handlers are registered on the **real** module-level singleton resolved through
`tools.extensions.extension_manager` — the same import `runtime.py` uses.
`tools/extensions/` and `icdev/tools/extensions/` are physically distinct copies
holding distinct singletons; a test registering on the other one would pass
against a handler the runtime can never see.
