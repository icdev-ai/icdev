# CUI // SP-CTI

# idp-gap-01 — A failing scorecard rule becomes a kanban task

**Epic:** IDP / GAP — closing the loop between grading and building
**Status:** shipped (seeding disabled by default; caps proven by dry run)

## Problem

cortex.io surfaces a gap and waits for a human. That is not a product
limitation so much as a boundary: a catalog knows a service is failing a rule,
but it does not own a delivery system, so the red cell is where its
responsibility ends.

ICDEV's boundary is drawn somewhere else. `kanban_tasks` holds thousands of
rows, `promote_backlog_to_scheduled` gates on dependencies, and a runner
dispatches work autonomously. The scorecard (idp-score-02) already produces a
precise, machine-readable list of what is wrong with every component. Nothing
was carrying one to the other.

## What it does

`tools/idp/gap_seeder.py` evaluates `args/scorecards/*.yaml`, takes every
`status == "fail"` outcome, and emits **one task per failing rule per
component**:

| Task field | Comes from |
|------------|-----------|
| `title` | `<component>: <rule title>` |
| `description` | the rule's `failureMessage`, plus level, weight, the component's current standing, and the re-check command |
| `acceptance_criteria` | the IQE query that measured the failure — the evidence source itself |
| `idempotency_key` | `idp-gap:<scorecard>:<component>:<rule>` |
| `depends_on_task_id` | `idpgap-gate-00` |
| `status` | `suggested` |

Putting the IQE expression in the acceptance criteria is what makes the task
verifiable rather than aspirational: the fixer can run the exact query the
grade came from. The criteria also say, in words, that satisfying the rule by
editing the rule, widening its `filter`, or adding an exemption does not count —
an autonomous fixer will otherwise take the cheapest path to green, which for a
measurement is to change the measurement.

## Rate limiting — the part that had to be right

66 components × 11 rules is ~700 candidates. Measured on the live board at
implementation time: **311 failing rules**. An unbounded seeder in this repo has
already produced 353 branches in a single pass, so this is a known failure mode,
not a hypothetical.

Three mechanisms, in the order they apply:

1. **`max_tasks_per_component` (default 2), applied first.** The ordering is
   load-bearing. Capping only the run would let the single worst component's
   eight failures consume a budget of ten and starve every other component.
2. **`max_tasks_per_run` (default 10).** Hard ceiling across all scorecards.
3. **Loud truncation.** Both caps log at WARNING, print to stderr, and report
   `truncated: {by_component_cap, by_run_cap, components_capped}` in the JSON.
   A cap that drops work silently reads as "nothing left to do", which is worse
   than the storm it was added to prevent.

Proven by dry run before anything was enabled:

```
[DRY RUN] scorecards: component-readiness
  gaps found            311
  candidates            311
  selected              10   (cap 10/run, 2/component)
  TRUNCATED             177 by component cap, 124 by run cap
```

## Not reseeding

The idempotency key is stable across runs, so a second pass over an unchanged
estate creates nothing — `task_factory.create_tasks` dedupes on it, and the
seeder *also* pre-filters on it before applying the cap. That second filter is
not redundant: without it, the first ten gaps would be re-offered on every run,
consume the entire budget, and the eleventh would never land.

The deliberate trade: a gap that is closed and later regresses does **not**
reseed under its old key. "Re-running produces none" and "a regression produces
a new card" cannot both hold with a stable key, and the first is the property
that keeps the board usable. `idp_scorecard_history` (idp-score-03) is where a
regression surfaces.

## Nothing dispatches without confirmation

Seeded tasks land as `suggested` **and** carry `depends_on_task_id` pointing at
`idpgap-gate-00`, a `*-gate-00` sentinel created held at `in_progress`.

Only the second of those is a real hold. `suggested` keeps a card out of the
scheduler's dispatch query, but the kanban deadlock-breaker can promote a
`suggested` card to `backlog` — on a chain block, on `priority='critical'`, or
when the queue goes idle. The dependency edge is enforced in code:
`promote_backlog_to_scheduled::_deps_satisfied()` returns `False` unless the
dependency is `done` or `decomposed`. Hence also:

* priority is **never** `critical` — the config value is clamped to `high`,
  because a critical card is exactly what the deadlock-breaker promotes;
* `gate_task_id` must end in `-gate-00`, or `gate_state()` raises — the suffix
  is what `tools/kanban/gates.py::is_manual_gate` matches, and a sentinel it does
  not recognise gets promoted, dispatched, reaped or auto-completed like work;
* seeding is **refused** if the gate already exists in a released state, because
  the tasks would land with nothing holding them.

Release the whole batch by setting the gate to `done`.

Above all of that sits `enabled: false` in `args/idp_gap_seeder.yaml`: `--seed`
is refused outright until an operator flips it. Dry runs always work. There is
deliberately **no scheduled reflex** — an autonomous writer behind a disabled
config is dead weight, and one behind an enabled config is a decision to make on
purpose rather than to inherit from a merge.

## Files

| File | Role |
|------|------|
| `tools/idp/gap_seeder.py` | The seeder. Mirrored to `icdev/tools/idp/`. |
| `args/idp_gap_seeder.yaml` | Caps, selection, task shape, `enabled` switch. |
| `tests/test_idp_gap_seeder.py` | 34 tests: gap extraction, cap ordering, idempotency, gate wiring, refusal paths. |

Seeds through `tools.kanban.task_factory.create_tasks` — never a raw INSERT.
