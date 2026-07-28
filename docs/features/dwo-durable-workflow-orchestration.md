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

## Tests

`tests/test_dwo_dur_03_resume_surface.py` — retry policy parsing and defaults,
retry execution paths, resumable-status contract, the API's 202/404/409
outcomes, and the UI control's presence and status parity.

`tests/test_dwo_gate_bridge.py` — one parked gate produces exactly one
reviewer-inbox row (and re-parking does not duplicate it); approving from
workflow_hitl releases the Studio run gate; approving from Studio completes the
external step; a Telegram approval closes the external step; rejection
propagates; and a second decision on a decided gate is refused without
overwriting the first decider.
