# Seed Tasks — Queue a Decomposed Initiative onto the Kanban Board

Turn a decomposition into validated, deadlock-safe Kanban tasks through the
shared `task_factory` — the ONLY correct way to queue autonomous work. Never
hand-roll `INSERT INTO kanban_tasks`. Read `tools/kanban/SEEDING.md` once if
unsure; do NOT re-derive the schema.

## Variables

project_key: $1   (e.g. `cwk`, `dt-iqe`; lowercase, the task-id prefix)
decomposition: $ARGUMENTS  (the work to decompose; or take it from the conversation)

## Instructions

### Phase 1: Decompose into atomic tasks
- Break the initiative into small, single-responsibility tasks. Order them so
  each task depends on the prior work it needs (`depends_on_task_id`).
- Task ids MUST be `<project_key>-<epic>-<NN>` (e.g. `cwk-db-01`). Zero-pad NN.

### Phase 2: Write a rich description for EVERY task (this is the point)
The dispatcher hands the description verbatim to an autonomous worker with NO
other context. Each description MUST be ≥200 chars and contain all four:
1. **What & why** — one or two sentences.
2. **Files to touch / reuse** — concrete paths (`tools/x/y.py`) + reuse pointers.
3. **Acceptance criteria** — observable, checkable outcomes ("done when …").
4. **Test/verify plan** — the test file + command (`tests/x/test_y.py`; `pytest -q`).

Status is `backlog` by default (dep-gated, dispatch ASAP). Only use `scheduled`
for genuinely time-deferred work, and then you MUST set `scheduled_at`.

### Phase 3: Emit a spec file
Write the batch to `.tmp/seed_<project_key>.json`:
```json
{"project_key": "<project_key>",
 "tasks": [
   {"id": "<project_key>-<epic>-01", "title": "...", "description": "...(4 sections, >=200 chars)...",
    "task_type": "build", "priority": "high", "depends_on_task_id": null}
 ]}
```

### Phase 4: Gate (validate BEFORE seeding)
```bash
python tools/kanban/seed_validator.py --gate --file .tmp/seed_<project_key>.json
```
Show the scorecard to the user. If it FAILS (structural, thin description, cycle,
or LLM rubric < 70), FIX the offending tasks and re-run. Do NOT seed until PASS.
(Air-gap / no LLM: add `--no-llm` — deterministic checks still apply.)

### Phase 5: Seed (only on PASS)
```bash
python -c "
import json
from tools.kanban.task_factory import create_tasks
spec = json.load(open('.tmp/seed_<project_key>.json', encoding='utf-8'))
print(create_tasks(spec['project_key'], spec['tasks']).summary())
"
```
`create_tasks` re-validates (strict), is idempotent (skips existing ids), writes
scalar + junction deps, and auto-registers the project in `args/projects.yaml`.

### Phase 6: Confirm
- Run `python tools/project/kanban_project_sync.py` if the project card didn't appear.
- Report: tasks created/skipped, the root (no-dep) tasks the scheduler will pick
  up first, and the scorecard summary.

## Report
> **Seeded `<project_key>`:** N created, M skipped.
> Roots ready to dispatch: `<ids with no deps>`.
> Validation: PASS (rubric on/off). Project card live on Home.

## Notes
- Primary DB is PostgreSQL; the factory uses the storage shim so the same code
  runs on PG and SQLite.
- Reference: `tools/kanban/SEEDING.md`, `tools/kanban/task_factory.py`,
  `tools/kanban/seed_validator.py`.
