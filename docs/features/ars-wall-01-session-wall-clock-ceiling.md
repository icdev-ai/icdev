# Session Wall-Clock Ceiling (ars-wall-01)

CUI // SP-CTI

## The gap this closes

`icdev/tools/llm/agent_loop.py` enforced ceilings on everything a session
*consumes* and on every *individual call* it makes:

| Ceiling | Scope |
|---|---|
| `max_total_tokens` | cumulative consumption |
| `max_cost_usd` | cumulative consumption |
| `max_iterations` | turn count |
| `tool_timeout_seconds` | **one tool call** |
| `llm_call_timeout_seconds` | **one `router.invoke()`** |

Nothing bounded *total elapsed time*. Before this change, `grep` over the module
returned zero hits for `wall_clock`, `max_wall` and `elapsed`.

The two timeouts look like they cover it, but they are per-call. A session can
stay under every one of them — every tool returns in 25s, every LLM call in 110s
— and under the token and cost budgets, and still run for hours. That is exactly
the shape a slow external dependency or a patient loop produces: steady, legal,
individually-cheap turns that never end.

The observable failure was worse than the runtime. When the kanban reaper
eventually killed such a task from the outside, the process died mid-turn: no
`AgentLoopResult`, no `truncation_reason`, nothing to attribute. And when a run
did hit a budget first, it reported `error_max_budget_tokens` — which reads as
*"task too big"* when what actually happened is *"ran too long"*. Those call for
different responses (raise the budget vs. fix the dependency), and the telemetry
could not tell them apart.

`kanban_tasks.max_runtime_seconds` already existed at the task layer
(`task_factory` accepts it), so the *concept* was present — just not inside the
loop, and not reconciled with it.

## What was added

### 1. A session budget alongside the existing ones

`args/llm_config.yaml` → `agent_loop.budgets.max_wall_clock_seconds`, loaded by
the same `_load_budget_defaults()` mechanism as every sibling budget. Mirrored to
`icdev/args/llm_config.yaml` and `icdev/data/args/llm_config.yaml`.

```yaml
agent_loop:
  budgets:
    max_wall_clock_seconds: 1800.0    # hard cap on total elapsed session time
```

`1800` is not arbitrary: it is `KANBAN_MAX_EXECUTION_SECONDS`, the kanban
runner's default per-task kill timer, so the loop-level and task-level ceilings
agree out of the box.

`run_agent_loop(..., max_wall_clock_seconds=N)` overrides it per call. `0` (or
negative) disables the ceiling entirely — an explicit opt-out that must never be
confused with "no time left".

### 2. A distinct truncation reason

```python
result.result_subtype   == ResultSubtype.error_max_wall_clock
result.truncation_reason == "max_wall_clock_seconds"
```

Deliberately its own subtype rather than a reused budget one, so "ran too long"
is separable from "task too big" in the harness record.

`AgentLoopResult.elapsed_seconds` is now populated on **every** exit path —
success, truncation, stall, error — and forwarded into the Continuous Harness
codegen decision. A run that stayed under every token and cost cap but burned an
hour is now visible as such.

### 3. Checked twice per turn, on purpose

The deadline is compared **before** the LLM call (so a run already over budget
does not start a turn it cannot afford) and **again after that turn's tools**
(so a single long turn is caught on the turn that blew the budget, not one full
LLM call later). `time.monotonic()` is used throughout, so a clock adjustment
mid-run cannot extend or collapse the budget.

### 4. The multi-round wrappers slice it rather than copy it

`run_agent_loop_with_rubric` and `run_staged_agent_loop` both call
`run_agent_loop` repeatedly. Forwarding the caller's budget verbatim through
`**kwargs` would multiply the ceiling by the round count — a 3-round rubric run
would legitimately run for 3× the budget, which is the exact "budgets race each
other" failure this bound exists to remove.

Both now resolve **one** deadline for the whole run and hand each round only the
time actually remaining. A rubric run whose budget is exhausted between rounds
(including by the *grading* between them) stops with the loop's own
`truncation_reason`; a staged pipeline records the unreachable stage as failed.

## Loop-level and task-level consistency

This is the half of the task that keeps the two ceilings from racing.

`tools/genesis/reflexes/kanban.py::_dispatch_via_rubric_loop` derives the loop
budget from `_get_task_timeout(task_id)` — **the same function the reaper calls**
before killing a running task (`kanban.py:8696`), and one that already honours
`kanban_tasks.max_runtime_seconds` ahead of every heuristic. One source, two
consumers, instead of two independent constants.

```python
_task_budget = _get_task_timeout(task_id)          # what the reaper will use
_wall_budget = max(60.0, _task_budget * 0.9)       # what the loop gives itself
```

Held at 90% deliberately. The loop stops *itself* just inside the kill timer and
returns a real `AgentLoopResult` with
`truncation_reason="max_wall_clock_seconds"`; being killed from outside yields no
result and no reason at all. The dispatch log now records
`elapsed_s=<actual>/<budget>` and the truncation reason on every rubric run.

## Verification

`tests/test_agent_loop_wall_clock.py` — 17 tests, driven by an injected fake
clock so assertions are exact and the suite runs in ~6s with no sleeps.

* **Configuration** — the default ships in the config; it is strictly larger than
  both per-call timeouts (it is a *session* ceiling, not another per-call one);
  the subtype is unique among all `ResultSubtype` values.
* **Enforcement** — a slow tool ends the session with its own reason while the
  token and cost ceilings are provably untouched; a slow *LLM* alone does the
  same; a single long turn is caught on that turn (asserted via exactly one LLM
  call being made).
* **No false positives** — a fast session completes normally; `0` disables the
  ceiling under an identically slow workload, which is the suite's built-in
  control that the enforcement path is what causes the stop.
* **Budget slicing** — rubric and staged rounds receive `[1000, 900, 800]`, a
  monotonically shrinking remainder, not three copies of `1000`.
* **Task consistency** — an explicit `max_runtime_seconds=900` resolves through
  `_get_task_timeout`, and the derived loop budget is strictly below it.

Regression: `tests/test_agent_loop.py`, `test_agent_loop_wiring.py` and
`test_agent_loop_semantic_loop.py` — 126 passed.

## Related

* [ars-loop-01 — semantic loop detection](ars-loop-01-semantic-loop-detection.md):
  catches an agent looping through *equivalent* actions. This catches one that is
  merely slow. Different causes, different reasons, both distinct from "budget".
