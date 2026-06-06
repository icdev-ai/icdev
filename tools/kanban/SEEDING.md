# Kanban Task Seeding — Source of Truth

> One place to learn how to queue decomposed work onto the Kanban Task Board.
> Do not re-derive the schema by hand — use the factory.

## TL;DR

```python
from tools.kanban.task_factory import TaskSpec, create_tasks

specs = [
    TaskSpec(
        id="myproj-db-01",
        title="Create the foo schema + init_db",
        description=(
            "Create tools/myproj/db/init_db.py with a `foo` table (id, name, "
            "created_at). Reuse get_canvas_connection() from tools/db/storage.py. "
            "Acceptance criteria: init_db() is idempotent and the table exists "
            "after one call. Test plan: tests/myproj/test_init_db.py with pytest; "
            "run pytest -q."
        ),
        task_type="build", priority="high",
    ),
    TaskSpec(
        id="myproj-api-01",
        title="Add the /myproj blueprint",
        description="...(>=200 chars, names files, acceptance criteria, test plan)...",
        task_type="build", priority="high",
        depends_on_task_id="myproj-db-01",
    ),
]
report = create_tasks("myproj", specs)
print(report.summary())
```

## The two rules that prevent the recurring bugs

1. **Status defaults to `backlog`. Never seed `scheduled` without a `scheduled_at`.**
   The dispatcher's scheduled query (`tools/genesis/reflexes/kanban.py` →
   `_get_due_tasks`) requires `scheduled_at IS NOT NULL`. A `scheduled` row with
   NULL `scheduled_at` matches neither dispatcher query and **deadlocks every
   task that depends on it**. The factory refuses to construct that state. Use
   `backlog` for dep-gated work; only use `scheduled` for genuinely time-deferred
   tasks, and then pass `scheduled_at`.

2. **Every task needs a rich, self-contained description.** The dispatcher hands
   the description verbatim to an autonomous Claude CLI worker that gets *no other
   context*. The validator enforces, per task:
   - `len(description) >= 200`
   - names at least one **file/dir path** (point the worker at concrete files)
   - states **acceptance criteria** ("acceptance" / "done when" / "success criteria")
   - states a **test/verify plan** ("test" / "pytest" / "verify" / "e2e")

   Recommended 4-section description shape:
   ```
   What & why: <one or two sentences>
   Files to touch / reuse: tools/x/y.py (reuse helper_z from tools/x/util.py)
   Acceptance criteria: <observable, checkable outcomes>
   Test/verify plan: tests/x/test_y.py — run pytest -q
   ```

## TaskSpec fields

| field | default | notes |
|-------|---------|-------|
| `id` | — | `^<prefix>(-<epic>)+-<NN>$`, must start with `project_key` |
| `title` | — | required, non-empty |
| `description` | — | see rule 2 |
| `task_type` | `build` | feature\|bug\|chore\|test\|build\|research\|docs\|spike\|refactor |
| `priority` | `medium` | critical\|high\|medium\|low |
| `depends_on_task_id` | None | scalar parent (scheduler gate + cycle detection) |
| `depends_on` | `[]` | extra parents → `kanban_task_deps` junction (UI dep graph) |
| `status` | `backlog` | only `scheduled` with a `scheduled_at` |
| `scheduled_at` | None | required iff `status='scheduled'` (ISO-8601 UTC) |
| `classification` | `CUI // SP-CTI` | |
| `project_id` | `=project_key` | |

## Validation gate (run before seeding)

```bash
# validate a JSON spec  {"project_key": "...", "tasks": [ {...}, ... ]}
python tools/kanban/seed_validator.py --gate --file spec.json
# validate live DB rows for a project
python tools/kanban/seed_validator.py --gate --project myproj
python tools/kanban/seed_validator.py --gate --file spec.json --no-llm   # air-gap
```
`create_tasks(..., strict=True)` (the default) runs this automatically and raises
`SeedValidationError` (with the scorecard) on any failure — nothing is written.

## Dependency gating (how the chain runs)

A task dispatches only when **all** its deps are `done`/`decomposed` (scalar
`depends_on_task_id` AND every `kanban_task_deps` row). `depends_on_task_id` is
single-parent; put secondary cross-epic prereqs in `depends_on` and/or name them
in the description. The factory writes both. The board UI reads the junction for
multi-parent dep graphs; the scheduler reads the scalar for cycle detection.

## Self-healing backstop

`_reconcile_limbo_tasks()` (kanban reflex, every cycle) converts any
`scheduled`+NULL row to `backlog` and logs dangling deps — so even a hand-written
INSERT or manual SQL self-corrects within ~60s. The coherence gate
`check_kanban_seed_integrity` fails on the deadlock pattern in seeders and warns
on raw INSERTs that should move to the factory.

## Don't

- Don't `INSERT INTO kanban_tasks` by hand in a seeder — use `create_tasks`.
- Don't seed `status='scheduled'` for dep-gated work.
- Don't write one-line descriptions — the worker can't act on them.

Related: `tools/kanban/task_factory.py`, `tools/kanban/seed_validator.py`,
`.claude/commands/seed-tasks.md`, memory `kanban-autonomous-seeding-workflow`.
