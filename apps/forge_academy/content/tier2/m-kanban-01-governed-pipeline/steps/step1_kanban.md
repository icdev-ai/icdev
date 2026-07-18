---
ontology_id: icdev:mission:m-kanban-01-governed-pipeline:step:1
step_class: icdev:Lab
---

# The Governed Delivery Pipeline

Every multi-task build in ICDEV runs through a **governed kanban pipeline**
(`tools/kanban/`). Understanding its lifecycle and gates is what keeps autonomous
sessions from shipping half-finished or unauthorized work.

## The task lifecycle

A task moves through a fixed set of statuses (this lab models the core happy path; the
real `KanbanState` enum in `tools/kanban/state_machine.py` also has `pr_opened`,
`ci_failed`, `merge_conflict`, `changes_requested`, `token_exhausted`, `decomposed`,
and `failed`):

```
suggested → backlog → scheduled → in_progress → validating → done
```

- **`suggested`** — AI-proposed tasks land here in **quarantine**. They are never
  dispatched directly; a human (or a bounded auto-revive) moves them to `backlog` first.
- **`backlog` → `scheduled`** — the promoter (`promote_backlog_to_scheduled`) picks up
  eligible backlog tasks and schedules them for dispatch.
- **`scheduled` → `in_progress` → `validating` → `done`** — the task is built, then
  verified, then closed.

## The gates

**Manual gate-00 sentinel.** Some projects must not be auto-built (e.g. work in a
private external repo). Those projects hold a `<prefix>-gate-00` task **in_progress**.
Because the promoter refuses to dispatch a project with an unreleased gate task, the
whole project stays parked until a human releases the gate. The gate task itself is
never dispatched.

**Done-verification against origin/main.** A task is only truly `done` once its PR is
**merged into `origin/main`**. Marking a task done without the merge landing is treated
as a lie — the pipeline verifies the merge before it accepts the terminal state. (Set a
task's status via `python tools/kanban/cli.py --set-status <id> done`, after fetching
`origin/main`.)

## What you'll build

The lifecycle rules and both gates, with the stdlib:

1. `can_transition()` — enforce the legal status transitions.
2. `is_gate_task()` / `project_is_gated()` — detect the manual gate-00 sentinel.
3. `promote_backlog_to_scheduled()` — the promoter: schedule only ungated,
   non-quarantined backlog tasks.
4. `verify_done()` — a task reaches `done` only when merged to `origin/main`.

Open `step1_starter.py` and implement the `TODO`s.
