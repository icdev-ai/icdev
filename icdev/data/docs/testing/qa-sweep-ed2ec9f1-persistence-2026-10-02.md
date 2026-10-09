# CUI // SP-CTI

# QA-agent E2E sweep — `ace_qa_runs` persistence record — 2026-10-02 (task-qa-sweep-ed2ec9f1)

Step `task-qa-sweep-ed2ec9f1-d4` of the `[QA-AGENT] Full E2E Suite Sweep` card:
confirm the sweep is persisted to `ace_qa_runs` through
`qa_agent_runner.record_run()`.

This record covers persistence only. The route smoke gate, the suite execution
and failure filing belong to steps `-d1`, `-d2` and `-d3` and are **not**
asserted here.

## Result

The row exists. It was written by `record_run()` when the sweep itself ran
(`qa_agent_runner --run` records by default), so this step **inserted nothing**:
`ace_qa_runs` is append-only, and a second `record_run()` for the same sweep
would either no-op on `ON CONFLICT (id) DO NOTHING` or, under a new run id,
add a duplicate row describing a run that did not happen.

| Column | Value |
|---|---|
| `id` | `qa-1790943177` |
| `trigger` | `kanban:task-qa-sweep-ed2ec9f1` |
| `canvas_filter` | `''` (full suite) |
| `status` | `passed` |
| `total_tests` | 853 |
| `passed` | 837 |
| `failed` | 0 |
| `screenshot_count` | 624 |
| `started_at` | 2026-10-02 12:12:57.811250+00 |
| `completed_at` | 2026-10-02 12:30:28.824510+00 |
| `report_path` | `.tmp/worktrees/task-qa-sweep-ed2ec9f1-d3/.tmp/ace/qa/qa-1790943177-results.json` |

`ace_qa_runs` has no skipped column; 853 − 837 − 0 = 16 is derived, and matches
the 16 skipped of the 2026-09-29 and 2026-09-30 sweeps.

## How it was checked

Read on the live PostgreSQL board, 2026-10-02:

- `python -m icdev.tools.testing.qa_agent_runner --status qa-1790943177` printed
  `status=passed total=853 passed=837 failed=0`.
- `SELECT ... FROM ace_qa_runs WHERE trigger = 'kanban:task-qa-sweep-ed2ec9f1'`
  returns exactly **one** row, the one above. It is the latest row for this
  sweep; the only newer row in the table (`qa-1790944237`, started 12:30:37)
  belongs to a different card, `task-qa-sweep-6b6dda67`.
- `ace_qa_failures` holds **0** rows for `run_id = 'qa-1790943177'`, consistent
  with `failed = 0` — there are no failure ids to record.
- `started_at` precedes `completed_at` by 17m31s and both are populated, i.e.
  the times are the sweep's own and not the column default.

## What could not be checked

The `report_path` file no longer exists: the `-d3` worktree it was written in
has been removed. The row's totals therefore could not be re-derived from the
results JSON in this step; they are consistent with the two preceding sweeps
(837 passed / 0 failed of 853) but that is corroboration, not a re-read of this
run's report.
