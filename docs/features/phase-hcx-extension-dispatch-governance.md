# CUI // SP-CTI

# Extension dispatch: per-point governance and a countable seam (hcx-live-02)

`args/extension_config.yaml` has declared a `hook_points:` block since Phase 44 —
ten points, each with `enabled`, `allow_modification`, `max_handlers` and
`timeout_ms`. `ExtensionManager.dispatch()` read `extensions.enabled` and two
`extensions.safety` keys and **nothing else**. Forty declared values, zero
consumers: the same declared-but-unconsumed defect as the dead hook points
themselves, one layer down.

Nothing counted a dispatch either, so
`tools/awareness/capability_consumption.py` — the tool built specifically to
measure this class of defect — could not see the seam at all.

## What was wrong

| Symptom | Consequence |
|---|---|
| `hook_points.<point>.enabled` unread | The only working kill switch was the global `extensions.enabled`, which also stops the nine chat handlers that are in use. An operator standing one point down edits a key with no effect. |
| `hook_points.<point>.max_handlers` unread | `safety.max_handlers_per_point: 20` was unread too. Nothing bounded a dispatch. |
| `hook_points.<point>.timeout_ms` unread | Every point ran against the 30 s global ceiling, six times its own declared budget. |
| `hook_points.<point>.allow_modification` unread | `tool_execute_after` ships `false` ("Post-hooks observe only by default") and a handler registering `allow_modification=True` there could rewrite the context anyway. |
| No dispatch count anywhere | "This point is consumed" and "this point has never been called in the platform's history" were the same reading. |
| `catch_handler_exceptions: true` (correct) with no counter (not) | A raising handler was logged and forgotten. Fail-soft had become fail-silent. |

## What changed

### 1. `point_config()` resolves all four keys; `dispatch()` enforces them

`enabled` is a real per-point kill switch. `max_handlers` bounds one dispatch in
priority order (falling back to `safety.max_handlers_per_point`). `timeout_ms`
is combined with the global ceiling by `min()` — a point may tighten the global
bound, never raise it. Every fallback is the behaviour `dispatch()` had before
the keys were read, so an absent block can never be the thing that turns a
working handler off.

`allow_modification` is enforced as a **ceiling** on what a handler may declare,
not as a duplicate of the per-handler flag. It only ever lowers: a point that
permits modification does not grant it to an observational handler. A discarded
modification is logged and counted.

### 2. One `runtime_invocations` row per dispatch, on a new `extension` surface

No new table — `runtime_invocations` (migration 341) already has this exact
shape and already enforces the rules that matter: never raises, stores argument
**key names** only, degrades before its migration has run. A dispatch whose
handler raised closes as `status='error'` with `error_class` and an
`error_message` naming the handler, which puts a broken extension in the
`errors` column of `invocation_recorder.summary()`, in `icdev runtime top`, and
on the Runtime Performance panel at `/monitoring`.

Two deliberate choices:

* **A dispatch with zero handlers is still recorded.** "Dispatched, nothing
  listening" and "never dispatched" are different defects with different fixes,
  and eight of the ten points are currently one of the two.
* **A point disabled by config records nothing at all.** The kill switch is
  total, including its telemetry cost — an operator standing a hot-path hook
  down should not keep paying two SQL statements per call for it. The
  suppression is counted in `ExtensionManager.stats()` instead.

**Cost, measured rather than assumed.** One dispatch costs ~8 ms wall-clock for
the open/close pair (SQLite, warm; ~2.4 ms of that is two `get_connection()`
round trips, the rest the commit). That is the same price `record()` has charged
the MCP surface per tool call since migration 341, so this doubles an accepted
cost rather than introducing a new class of one — but since hcx-live-01 it lands
on **every tool call in the SAG runtime**. Two named, auditable switches turn it
off: `ICDEV_OBS_INVOCATIONS=0` for all invocation telemetry, or
`hook_points.<point>.enabled: false` for one point, which skips the dispatch
entirely at ~0.002 ms. Never a code edit that drops the row while leaving the
point live — that is how the seam became unmeasurable in the first place.

`stats()` is the in-process half: per point, `dispatches`, `suppressed`,
`handlers_run`, `handler_failures`, `handlers_dropped`, `timeouts`,
`modifications_suppressed`, and `last_error` verbatim. It is what a caller with
no database — a test, an air-gapped run, a process started before the
migration — can still read.

### 3. `extension_hook_point` is now a measured capability class

```bash
python tools/awareness/capability_consumption.py --class extension_hook_point --json
```

Declared units are points present in both the `ExtensionPoint` enum and an
enabled `hook_points` block, for the same reason `probe_reflex` requires both
halves. The enum is read with `ast`, never imported: importing it builds the
singleton, and the singleton auto-loads nine chat builtins that pull in RAG,
Bayesian learning and the genesis status reader — and this probe runs twice per
commit inside `check_capability_liveness`.

Measured on the live PostgreSQL board 2026-08-16 (`audit_trail`: 100,265 rows):
**10 declared, 10 never consumed**, which is a property of the counter's first
day rather than of the seam. `chat_message_before` / `chat_message_after` have a
live call site in `tools/dashboard/chat_manager.py` and drain on the first chat
message served. The other eight have **no dispatch call site anywhere in the
tree** — `tool_execute_after` sharpest of all, since `tools/awareness/hooks.py`
registers a handler on it at process start and nothing has ever dispatched it.
`args/liveness_gate.yaml` grandfathers the class at 10 with that argument
written down, and it is a ratchet: lower it as points drain, never raise it.

## What it found on its first run

The first dispatch under the new telemetry reported an `error` row:

```
081_build_kanban_sync: TypeError: handle_chat_message_after()
                       missing 1 required positional argument: 'ctx'
```

`tools/extensions/builtins/081_build_kanban_sync.py` declared
`handle_chat_message_after(event, ctx)` against a contract of
`handler(context) -> dict | None`. Every other builtin spells it
`handle(context)`. It raised on **every chat message the platform has ever
served** — registered, catalogued, `enabled: True`, and structurally incapable
of running — and `catch_handler_exceptions` swallowed it while nothing counted
the failure. Fixed by defaulting the unused parameter, and pinned by
`test_every_registered_handler_accepts_the_one_dict_the_contract_passes`, which
asserts the arity of every callable the real singleton would invoke.

That is the whole thesis of the change in one incident: the exception was
already being caught correctly; what was missing was anyone able to notice.

## Files

| File | Change |
|---|---|
| `tools/extensions/extension_manager.py` | `point_config()`, per-point enforcement in `dispatch()`, telemetry, `stats()` / `reset_stats()`, `ExtensionManager(config=…, load_builtins=…)` |
| `tools/observability/invocation_recorder.py` | `SURFACE_EXTENSION` |
| `tools/awareness/capability_consumption.py` | `probe_extension_hook_point`, `_extension_points_from_source` |
| `tools/extensions/builtins/081_build_kanban_sync.py` | the signature fix |
| `tools/dashboard/templates/monitoring/_runtime_performance.html` | Extensions filter button |
| `args/extension_config.yaml` | documents what each key now means |
| `args/capability_consumption.yaml`, `args/liveness_gate.yaml` | the new class and its measured budget |
| `tests/test_extension_dispatch_governance.py` | 23 tests, gated in `args/ci_test_files/core.txt` |
| `tests/test_capability_consumption.py` | 6 tests for the new probe |

Everything under `tools/` is mirrored to `icdev/tools/`; the two singletons are
distinct objects, and a test asserts the two manager sources stay byte-identical
so which one a caller imported cannot decide whether a kill switch works.
