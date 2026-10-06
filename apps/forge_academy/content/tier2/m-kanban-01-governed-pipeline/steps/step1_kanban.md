---
ontology_id: icdev:mission:m-kanban-01-governed-pipeline:step:1
step_class: icdev:Lab
---

# The Governed Delivery Pipeline

Every multi-task build in ICDEV runs through a **governed kanban pipeline**
(`tools/kanban/`). Understanding its lifecycle and gates is what keeps autonomous
sessions from shipping half-finished or unauthorized work.

## The task lifecycle

A task moves through a fixed set of statuses. This lab models a simplified path:

```
suggested → backlog → scheduled → in_progress → validating → done
```

On the real board (`kanban_tasks.status`; the `KanbanState` enum lives in
`tools/kanban/state_machine.py`) the happy path runs `in_progress → pr_opened → done`,
with `validating`, `ci_failed`, `merge_conflict`, `changes_requested`,
`token_exhausted`, `decomposed` and `failed` as the off-path states the pipeline stepper
(`tools/kanban/pipeline.py`) flags. Here `validating` stands in for "built, waiting on
the merge".

- **`suggested`** — AI-proposed tasks land here in **quarantine**. They are never
  dispatched directly; a human (or a bounded auto-revive) moves them to `backlog` first.
- **`backlog` → `scheduled`** — the promoter (`promote()` in
  `tools/kanban/promote_backlog_to_scheduled.py`) picks up eligible backlog tasks and
  schedules them for dispatch.
- **`scheduled` → `in_progress` → `validating` → `done`** — the task is built, then
  verified, then closed.

## The gates

**Manual gate-00 sentinel.** Some projects must not be auto-built (e.g. work in a
private external repo). Those projects hold a `<prefix>-gate-00` task **in_progress**.
The promoter never promotes a gate itself (`is_manual_gate()` in
`tools/kanban/gates.py` recognises any `<card>-gate-<n>` id), and it holds back every
task whose `depends_on_task_id` points at an unreleased gate — so the project stays
parked until a human releases it. This lab simplifies that to "a project with an open
gate is gated".

**Done-verification against origin/main.** A task is only truly `done` once its PR is
**merged into `origin/main`**. Marking a task done without the merge landing is treated
as a lie — the pipeline verifies the merge before it accepts the terminal state.
`python tools/kanban/cli.py --set-status <id> done` refuses while the task's branch still
has commits not on `origin/main`; `--set-status <id> done --merge` lands the task's PR
first and marks it done only once GitHub reports it merged.

## What you'll build

The lifecycle rules and both gates, with the stdlib:

1. `can_transition()` — enforce the legal status transitions.
2. `is_gate_task()` / `project_is_gated()` — detect the manual gate-00 sentinel.
3. `promote_backlog_to_scheduled()` — the promoter (named after the real module):
   schedule only ungated,
   non-quarantined backlog tasks.
4. `verify_done()` — a task reaches `done` only when merged to `origin/main`.

Open `step1_starter.py` and implement the `TODO`s.
