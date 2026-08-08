# Durable Workflow Orchestration — Studio Run Checkpoint & Resume

**Card:** DWO (Durable Workflow Orchestration) — tasks `dwo-dur-01` … `dwo-dur-03`
**Modules:** `tools/studio/workflow_runner.py`, `tools/dashboard/api/studio.py`,
`tools/dashboard/static/js/workflow-studio-exec.js`

CUI // SP-CTI

---

## What this delivers

A Studio workflow run survives the process that started it. If the dashboard is
restarted, killed, or crashes mid-run, the run can be picked back up from the
step it reached instead of being re-executed from step 1.

| Capability | Where |
|---|---|
| Approval gates persist across a restart | `_load_prior_steps()`, `_await_gate()` (dwo-dur-01/02) |
| Completed steps are replayed, not re-run | `prior_status` branch in `_worker()` (dwo-dur-01/02) |
| Per-step `retries` / `retry_backoff_seconds` | `_retry_policy()`, `_exec_step_with_retries()` |
| `POST /api/studio/runs/<run_id>/resume` | `api_resume_run()` |
| `▶ Resume Run` control on the run detail modal | `StudioWF.resumeRun()` |

---

## Decision: resume continues the run IN PLACE

`resume_run()` re-attaches a worker to the **original** run row. It does not
create a second run linked back by a `resumed_from_run_id`.

`RESUME_MODE = "in_place"` in `tools/studio/workflow_runner.py` names this
choice so callers (and the API response) can see which model is in force.

**Why in place, and not a forked linked run**

The alternative — insert a new run row carrying `resumed_from_run_id`, leaving
the original immutable — was considered and rejected:

1. **It would strand pending approvals.** A step parked at `awaiting_approval`
   keeps its original `step_run_id`, and that step row is keyed to the original
   `run_id`. An approval issued before the interruption (an email link, another
   operator's browser tab, a cross-process approval already written to the DB)
   resolves *that* `step_run_id`. Fork the run and the approval lands on a run
   no worker is executing, while the new run parks a second gate that nobody
   was told about. Continuing in place is what makes the dwo-dur-01/02 gate
   re-attachment work at all.
2. **The immutability it would buy is not actually at risk.** `_worker()` only
   ever *inserts* into `studio_workflow_run_steps`; resume never rewrites a
   prior step's record — replayed steps are re-emitted to the SSE queue and
   skipped, explicitly "without touching the append-only record again". The run
   row's `status` is mutated during any normal run (`pending → running →
   success`), so a resume mutating it is not a new class of write.
3. **Splitting the history costs more than it returns.** Every consumer of run
   history — the run list, the detail modal, `summary_json`, artifact
   aggregation — would need to walk a resume chain to answer "what happened in
   this run?".

**What is given up:** there is no separate row recording "a resume happened at
T". The evidence is indirect — the run's steps span a gap in `started_at`, and
the SSE stream marks replayed steps with `resumed: true`. If a hard audit
requirement for resume events appears later, the right shape is an append-only
`studio_workflow_run_resumes` table (one row per resume, same `run_id`), not a
forked run.

---

## Per-step retries

Declared on a step in the template YAML. Both default to `0`, so every existing
template behaves exactly as it did before.

```yaml
steps:
  - id: plan
    name: Terraform Plan
    tool: tools/deploy/terraform_plan.py
    retries: 2                   # up to 2 extra attempts after the first
    retry_backoff_seconds: 5     # linear: 5s before attempt 2, 10s before attempt 3
```

- Only `failed` and `timeout` are retried. A `skipped` step has nothing to
  retry, and a human gate is decided by a person, not by re-execution.
- Backoff is linear (`backoff * attempt`) and each sleep is capped at
  `_MAX_RETRY_BACKOFF` (300s), so a typo cannot wedge a worker thread for a day.
- The step's `step_done` SSE event carries `attempts`; the result payload gains
  an `attempts` key only when more than one attempt was made.
- Retries apply to freshly executed steps only. A replayed step is already
  satisfied, and a re-attached gate is waiting on a human.

---

## Resume API

```
POST /api/studio/runs/<run_id>/resume
POST /api/studio/workflows/runs/<run_id>/resume     (alias, groups with the other run routes)
```

| Response | Meaning |
|---|---|
| `202 {"status":"resuming","run_id":…,"mode":"in_place"}` | A worker was re-attached |
| `404 {"error":"Run not found"}` | No such run |
| `409 {"error":"Run is 'success' — only …"}` | Terminal run; nothing to resume |
| `409 {"error":"Run could not be resumed …"}` | A live worker already owns it, or the workflow was deleted |

Resumable statuses (`RESUMABLE_RUN_STATUSES`): `pending`, `running`,
`awaiting_approval`, `failed`. `failed` is included deliberately — a run that
died part-way is the case resume exists for.

## Resume UI

The run detail modal (Studio → Workflow Studio → Run History → **Details**)
shows a **▶ Resume Run** button whenever the run's status is resumable. The
JavaScript list `_RESUMABLE_RUN_STATUSES` mirrors the Python constant; a test
asserts the two stay in step.

---

## Decision: ONE reviewer inbox — Studio gates produce `wf_external_steps` (dwo-dur-04)

There were two human-approval surfaces that did not know about each other:

- Studio gates — `node_type: human` steps parked as `studio_workflow_run_steps`
  rows with `status='awaiting_approval'`, released by an in-process
  `threading.Event` or by a DB poll (Telegram `/approve`);
- workflow_hitl external steps — `wf_external_steps`, completed via the
  webhook-token path, with notifier/teams/templates behind them.

An approver had two inboxes and a reviewer could not see one from the other.

**Direction (a) was taken: Studio is a *producer* of `wf_external_steps`, and
workflow_hitl remains the single reviewer inbox.** workflow_hitl already owns
notification routing, teams, templates and the webhook-token completion path;
Studio reimplements none of it. Direction (b) — a foreign key from
`wf_external_steps` back to the Studio gate, with the HITL blueprint rendering
both — was rejected because it leaves two inboxes and asks the blueprint to
special-case a second row shape.

`tools/studio/gate_bridge.py` is the whole of the seam.

### Linkage carries no new schema

`wf_external_steps.external_ref` holds the Studio `step_run_id` and
`external_system` is `'studio'`. That gives lookup in both directions off
columns that already exist — no migration, no bridge table, no new column.

`wf_external_steps.step_type` is CHECK-constrained, so a Studio gate rides the
existing `'manual'` type rather than widening the constraint. The
`instance_id`/`template_id` foreign keys are satisfied by one shared system
template (`wft-studio-gate`) and one shadow instance per run
(`wfi-studio-<run_id>`), both created idempotently.

### Exactly once, in either direction

Approving in either surface releases the other, once:

| Decision taken in | Path |
|---|---|
| workflow_hitl (`POST /wf/external/<id>/complete`, webhook token) | `external_steps.mark_complete()` → `gate_bridge.release_studio_gate()` → `workflow_runner.approve_step()` |
| Studio (Details modal, `POST /api/studio/.../approve`) | `workflow_runner.approve_step()` → `gate_bridge.complete_external_step()` → `external_steps.mark_complete()` |
| Telegram `/approve <step_run_id>` | `telegram_listener._write_hitl_decision_to_db()` → `gate_bridge.complete_external_step()` |

Two guards keep that from looping or double-firing:

1. **Re-entrancy** — `gate_bridge._bridging` holds the `step_run_id` currently
   being bridged, so the callback into the originating surface is suppressed.
2. **Terminal status** — `mark_complete()` now refuses a step already in
   `completed`/`failed`/`timed_out`, and the Studio DB update is still scoped
   `WHERE status='awaiting_approval'`. A second decision on a decided gate is
   rejected and audited, not applied.

Telegram keeps working unchanged: the `/approve <step_run_id>` command router
resolves the same `step_run_id`, which is still what the notification body
carries. That path wrote its decision straight to the DB, bypassing
`approve_step()`, so it now closes the mirrored external step itself — otherwise
a Telegram approval left an orphan in the reviewer inbox.

### One audit trail

Gate decisions go to the shared append-only audit via
`tools.audit.audit_logger.log_event` — `studio_gate_opened`,
`studio_gate_approved`, `studio_gate_rejected`, and
`studio_gate_duplicate_decision_rejected` — regardless of which surface the
decision came from. Audit failure never breaks the gate.

If workflow_hitl is unavailable (tables absent, feature disabled), `open_gate()`
returns `None` and Studio falls back to its own Details-modal gate, unchanged.

## MCP human approval gate (dwo-mcp-02-d4)

A tool on `mcp_workflow_tools.requires_approval` (terraform_apply, k8s_deploy,
rollback, …) is dispatchable from a workflow step, but only behind an approved
human gate in the same run.

**No new approval mechanism.** The gate *is* the existing HITL representation:
a `studio_workflow_run_steps` row with no tool path and status
`awaiting_approval` — byte-for-byte what an authored `node_type: human` step
writes. So `workflow_runner.approve_step()` / `reject_step()` /
`get_pending_approvals()`, the Details modal, the Telegram approver and the
resume surface all drive it unchanged. There is no d4-only flag, table, column
or approval call.

Ordering inside `mcp_executor.run()`: allowlist → registry lookup → caller
IL/roles → parameter schema → **gate** → dispatch. The gate is last on purpose
— nobody should be woken to approve a call that would have been refused anyway.

| Outcome | Result |
|---|---|
| Approved | Handler dispatches; the payload carries `approval.step_run_id` |
| Rejected | Refused, `mcp_tool_approval_rejected`, handler never loaded |
| Undecided within the wait window | Refused, `mcp_tool_awaiting_human_approval`; **the gate stays parked** |
| No run to park a gate on, or gate store unreachable | Refused, `mcp_tool_approval_gate_unavailable` |

One gate per `(run, tool)`, found-or-created. That is what makes the undecided
case durable rather than lossy: the parked gate outlives the executor process,
so resuming the run re-attaches to the gate the approver was already shown and
dispatches immediately if it was decided meanwhile — it never opens a second
gate beside the first, and never asks a second approver the same question.

The wait window resolves env (`ICDEV_MCP_APPROVAL_WAIT`) → policy
(`approval_wait_seconds`) → 900s, with `--approval-wait` overriding per
dispatch. `0` parks the gate without blocking.

---

## MCP dispatch audit (dwo-mcp-02-d5)

d1–d4 decide; d5 records. Every attempt writes exactly one row to the
append-only `studio_mcp_dispatch_audit` table (migration 307), on all three
paths, before `run()` returns or re-raises.

| Path | `decision` | `reason` |
|---|---|---|
| Handler dispatched | `allowed` | `dispatched` |
| Not on the allowlist | `refused` | `mcp_tool_not_allowlisted` |
| Caller below the tool's IL | `refused` | `mcp_tool_exceeds_caller_il` |
| Caller lacks the owning component's role | `refused` | `mcp_tool_missing_required_role` |
| Human gate rejected / unavailable | `refused` | `mcp_tool_approval_rejected` / `…_gate_unavailable` |
| Human gate parked, undecided | `pending_approval` | `mcp_tool_awaiting_human_approval` |
| Unknown tool, bad params, raising handler | `refused` | `unknown_tool` / `invalid_params` / `dispatch_error` |

`reason` is the same string the CLI reports as `error_type`, so a row and the
step's stdout name one cause and neither has to be re-parsed to correlate.

Each row carries the tool, run, step, the actor (`principal_id`, `tenant_id`,
`caller_il`, `caller_roles`, and `caller_source` — *where* that identity came
from), the decision, the reason, and a UTC timestamp. The caller is resolved
before the allowlist check specifically so a refusal is attributed rather than
anonymous; resolving it reads run memory and the environment only, so a refused
tool's registry entry is still never touched.

**Parameters are digested, never stored.** `params_sha256` is SHA-256 over
canonical (sorted-key) JSON, so the same arguments in a different order digest
identically and "were these the arguments the approver saw" is answerable —
without copying credentials and CUI into the audit store.

**No hardcoded banner.** `classification` per row comes from
`classification_manager.get_classification_for_il()` applied to the caller's
impact level, so an IL6 dispatch is marked SECRET. An unrecognized level falls
back to the platform IL4 marking, still via the manager.

**The audit never decides the dispatch.** The write is best-effort: an
unreachable audit store must not turn an approved deployment into a failure,
nor a refusal into a pass. The outcome surfaces as `audit_written` /
`audit_skipped` in the step payload, so an audit outage is visible rather than
assumed. Read rows back with `mcp_executor.query_dispatch_audit(run_id=…,
tool=…, decision=…)`.

---

## Parallel DAG dispatch (hgx-par-01)

`_resolve_dag` returned `list(TopologicalSorter(...).static_order())` — a
FLATTENED linear list — and `_worker` walked it with `for i, step in
enumerate(ordered_steps)`. Templates authored as a fan-out therefore ran one
step at a time. `args/workflow_templates/ai_ml_transformation.yaml` is the
textbook case: `coa_a`/`coa_b`/`coa_c` share the same two dependencies and then
join at `leadership_brief` — a diamond, executed serially.

`_worker` now walks a **prepared** sorter (`_prepare_dag`) with
`get_ready()`/`done()` inside a bounded `ThreadPoolExecutor`, mirroring
`tools/agent/team_orchestrator.py::execute_workflow`. Decisions D40 (graphlib)
and D36 (threads, not asyncio — which is also what keeps it portable).

**Concurrency is opt-in.** `max_parallel:` is a top-level template key that
**defaults to 1**, so all 61 shipped templates (42 in `args/workflow_templates`,
19 in `context/workflow_templates`) stay byte-for-byte sequential. At 1 the
executed order is identical to the old `static_order()` walk, and
`tests/studio/test_workflow_parallel.py` asserts that per template rather than
in aggregate.

Preserving that order took two deliberate choices, both of which are silent
correctness traps:

* The pool's queue is FIFO, so with one worker submission order **is** execution
  order. Ready nodes are submitted as a group, exactly as `static_order()`
  yields them.
* Completed futures are retired in **submission** order, not in the
  set-iteration order `concurrent.futures.wait` hands back. graphlib appends
  each newly-unblocked node as its last dependency is `done()`, so walking the
  completed set directly would make dispatch order depend on thread scheduling.

**No barrier primitive was added.** Fan-in falls out of graphlib: a join is just
a step that names several ids in `depends_on`, and `get_ready()` withholds it
until all of them are retired.

Three things the parallel loop has to get right that the linear walk never had
to:

* **A human gate parks its own branch, not the pool.** `_await_gate` blocks by
  design; it now blocks a pool thread, leaving the other `max_parallel - 1`
  slots free for sibling branches.
* **A rejected or timed-out gate still stops the run.** Under the linear walk
  that was a `break`. It cannot be one here, because a sibling of the gate may
  already be sitting in the pool's queue — so it sets an abort flag that every
  queued step checks on entry, and the main loop retires the remainder without
  executing them. A step the old walk never reached still leaves no record.
* **Emission is no longer ordered.** The per-run SSE `queue.Queue(maxsize=500)`
  and the old `index` field assumed positional emission. `step_started` /
  `step_done` now carry a monotonic `seq` instead of a list position. Nothing
  consumed `index` (`tools/dashboard/templates/studio/execution.html` keys off
  `step_id`). `run_started` gained `max_parallel`.

Step records are keyed by `step_run_id`, so DB writes were already safe. The two
pieces of shared in-process state were not: `results`/`all_artifacts` are guarded
by a run-scoped lock, and `_remember_artifacts` — one JSON blob updated
read-modify-write — holds `_artifacts_lock` across the whole get/set pair, or
two steps of the same wave would drop one of the two artifact sets.

---

## Tests

`tests/studio/test_mcp_executor_approval.py` — the gate parks a pending human
node and blocks, approval dispatches, rejection refuses, an undecided gate
survives for resume without duplicating, and every fail-closed edge.

`tests/studio/test_mcp_executor_audit.py` — one correct row per attempt across
all four acceptance paths (allowed, allowlist refusal, IL refusal, parked
approval), read back through `query_dispatch_audit`; params digested not
stored; the marking tracks the caller's IL; the `decision` CHECK list matches
the Python constants; and an audit outage does not change the dispatch.

`tests/test_dwo_dur_03_resume_surface.py` — retry policy parsing and defaults,
retry execution paths, resumable-status contract, the API's 202/404/409
outcomes, and the UI control's presence and status parity.

`tests/studio/test_workflow_parallel.py` — every shipped template dispatches in
the old `static_order()` sequence when `max_parallel` is unset (one case per
template, plus a guard that the corpus is non-empty and that no shipped template
declares the key); `max_parallel` parsing and clamping; a diamond's three
branches overlap pairwise while the join waits for all three, and the same
template minus the key overlaps not at all; a human gate parks its branch while
siblings run to completion (the fake gate refuses to resolve until both siblings
finish, so blocking the pool deadlocks the test rather than passing it); a
rejected gate leaves every later step unexecuted; a failing tool step does not
block its wave; dangling `depends_on` and cycles.

`tests/test_dwo_gate_bridge.py` — one parked gate produces exactly one
reviewer-inbox row (and re-parking does not duplicate it); approving from
workflow_hitl releases the Studio run gate; approving from Studio completes the
external step; a Telegram approval closes the external step; rejection
propagates; and a second decision on a decided gate is refused without
overwriting the first decider.
