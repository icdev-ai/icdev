# Phase HGX — SAG Run Observability

**Task:** `hgx-obs-01`. **Card:** HGX — Harness Agent Parity and Graph Runtime
(`args/projects.yaml`, MANUAL-ONLY, gated on `hgx-gate-00`).
**Governing rule:** extend the existing surface — no second recorder, no second
tracer, no second telemetry table.

## The problem

`tools/agent_runtime/` contained **zero** references to `invocation_recorder`,
`tracer`, `span` or `trace_id`. Three consequences, measured before this change:

1. A SAG tool call was recorded in `runtime_invocations` only if it happened to
   route through the MCP unified server. A built-in tool (`write_file`,
   `run_command`) or a `@tool`-decorated function never touches that server, so
   it produced no telemetry at all.
2. The agent loop emitted no spans of its own. It *did* already mint a
   correlation id (`AgentLoopResult.trace_id`) and the router *did* already emit
   `gen_ai.invoke` spans — but nothing connected them, so a run's LLM calls and
   its turns were unrelated rows.
3. Nothing was replayable, and that is structural rather than accidental:
   `invocation_recorder` stores argument KEY NAMES only (a deliberate privacy
   decision) and `base_server.py` stores a result HASH.

## What changed

### 1. SAG tool calls are recorded (`surface="agent"`)

`tools/agent_runtime/dispatch.py::make_handler` now wraps every handler in
`invocation_recorder.record(SURFACE_AGENT, …)`. That is the one choke point
every SAG tool call passes through, whatever its source. Blocked calls (safety
gate refusal) and failed calls are recorded too, with status `error` — a model
asking for a tool it was not allowed to run is exactly what a run needs to show.

A transient read-only failure that `error_recovery` retried **successfully** is
not counted as an error; the wrapper distinguishes the two by the
`ToolResult.render()` failure prefix rather than by the presence of an exception.

`icdev runtime top --surface agent` now has rows.

### 2. A span per turn, joined by the correlation id

New module `tools/observability/agent_trace.py`:

| Piece | Role |
|-------|------|
| `correlation_scope` / `current_correlation_id` | Contextvar carrying the run id. The loop opens it once; the recorder reads it, so `dispatch.py` never has to be told what run it is in. |
| `submit_with_context` | `ThreadPoolExecutor.submit` starts the callable in a **fresh, empty** context — a contextvar set on the calling thread is invisible in the worker. The loop runs its LLM call and its read-only tools through a pool, which is precisely where the correlation would be lost. |
| `TurnTracer` | One `agent.turn` span per turn, with an **explicit** begin/finish lifecycle. The turn body has a dozen `break` paths across four hundred lines; `begin(turn)` closes the previous span and one `finish()` after the loop closes the last, so every exit path is covered by three call sites and nothing was re-indented. |
| `spans_for_correlation` | The join, as a function — backs `icdev runtime trace`. |

The join key is a span **attribute** (`icdev.correlation_id`), not a shared
`trace_id`. The router's `gen_ai.invoke` span is frequently created on a pool
worker whose trace context is its own; an attribute survives that thread hop and
a parent/child trace_id does not. `LLMRequest.correlation_id` carries the id to
the router for the same reason — explicitly on the request, not ambiently.

The reader filters with `LIKE` over the `attributes` JSON text and then checks
the attribute exactly in Python. Deliberately not `json_extract`: that is SQLite
dialect and this table is read on PostgreSQL first.

### 3. Replay is an opt-in widening, never a silent one

`ICDEV_OBS_REPLAY` (default **off**) gates two new columns, `arg_values` and
`result_preview`, both passed through `tools/llm/output_redactor.redact` and
truncated. Three properties, each asserted by a test:

- **Off is the default** — unset, empty, `0`, and any non-affirmative token all
  mean off, so a typo fails closed.
- **Off writes nothing, anywhere** — not a truncation, not a hash, not a length.
  `extract_arg_values` and `capture_result` return `None` and the INSERT/UPDATE
  omits the columns. The test puts a distinctive marker in both the arguments
  and the result and scans the **entire persisted row** for any fragment of it,
  so a future column that quietly stored a preview would fail.
- **On is still redacted** — the flag widens what is stored; it does not switch
  the redactor off.

## Surface

```bash
icdev runtime top --surface agent          # SAG tool calls, ranked
icdev runtime trace <correlation-id>       # every span of ONE run, oldest first
icdev runtime trace <correlation-id> --json
```

```python
run_agent_loop(router, ..., correlation_id="task-hgx-obs-01")   # join a run to its caller
```

Migration `20260808161052_sag_runtime_observability_columns` adds
`correlation_id`, `arg_values`, `result_preview` to `runtime_invocations`
(ALTER expressed in Python — `ADD COLUMN IF NOT EXISTS` is PostgreSQL-only).

## Degradation

Every path degrades rather than failing, because telemetry that can break the
run it observes is worse than no telemetry:

- A database that predates the migration keeps recording with the original
  column set. The recorder probes for the columns once per process instead of
  issuing an INSERT that names a column that does not exist — which, swallowed
  by its own `except`, would have turned a missing migration into **total**
  telemetry loss rather than three absent columns.
- A misconfigured or absent tracer backend gives the loop a no-op `TurnTracer`;
  the loop does not branch on whether tracing is available.
- Context propagation that is unavailable falls back to a plain `submit` — a
  lost correlation id is acceptable, a tool that does not run is not.

## Acceptance criteria

| Criterion | Where it is proven |
|-----------|--------------------|
| `icdev runtime top --surface agent` shows SAG tool calls | `test_runtime_top_surface_agent_reports_the_call`, `test_dispatch_records_agent_surface` |
| A run's spans join to its correlation_id | `test_spans_for_correlation_joins_turn_and_router_spans`, `test_loop_emits_a_turn_span_carrying_the_correlation_id`, `test_a_tool_call_inside_the_loop_records_against_the_run` |
| With the flag off, no argument value or tool result is persisted anywhere | `test_flag_off_persists_no_argument_value_and_no_result`, `test_flag_off_helpers_return_none`, `test_arg_key_names_are_recorded_but_never_the_values` |

Binding card criteria: no model IDs in Python (nothing here resolves a model);
`encoding="utf-8"` is used on every read (no agent-edited files are written);
no shell, no `asyncio` (threads per D36), `pathlib` and `__file__`-relative roots
only; every changed `tools/` module mirrored to `icdev/tools/`, with
`tools/llm/agent_loop.py` left alone because it is a re-export shim.

## Registration checklist (per CLAUDE.md 8-point)

1. `tools/manifest/observability-hooks.md` — Agent Trace, Runtime Trace CLI, and
   the new `agent` choke point added.
2. `docs/reference/commands.md` — `icdev runtime trace` documented.
3. `args/security_gates.yaml` — n/a, no blocking condition introduced.
4. MCP gateway — n/a, no new MCP tool.
5. `.claude/hooks/pre_tool_use.py` — n/a, `runtime_invocations` is telemetry and
   is deliberately NOT append-only.
6. `tests/conftest.py` — the three columns added to `MINIMAL_ICDEV_SCHEMA`.
7. Companion sync — n/a, no skill changed.
8. `coherence_checker --tier fast --gate` — passes.
