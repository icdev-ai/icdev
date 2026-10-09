# TSR GEN — bare `sqlite3.connect` fixtures converted to the translating wrapper (tsr-gen-01-d3)

GEN-slice shard (Genesis, Oracle & autonomy) of the `%s`/`?` anti-pattern sweep.
Sibling of `tsr-dash-01-d3-sql-compat.md`, same method, different subsystem.
Three test files, 11 failures, zero production changes.

## The defect

The repo authors SQL for PostgreSQL — `%s` placeholders — and
`tools.db.storage.StorageConnection` rewrites them to `?` on the SQLite backend.
A test that patches `get_connection` with a bare `sqlite3.connect` removes that
rewrite, so every statement the code under test runs raises:

```
sqlite3.OperationalError: near "%": syntax error
```

Where the caller wraps its work in a best-effort `except` — common on this slice,
because Genesis reflexes are written never to crash a scheduler cycle — nothing
surfaces at all. The test then asserts against a no-op it caused itself.

## Files converted

| file | patched symbol | consumer that needs the rewrite | before → after |
|---|---|---|---|
| `tests/test_value_scorer_and_bulk_move.py` | `tools.dashboard.api.kanban.get_connection` | `list_tasks`, `bulk_move_tasks` | 7 failed / 24 passed → **0 / 31** |
| `tests/test_nova_sela_evolution.py` | `tools.db.storage.get_connection` | `_query_low_performing_skills`, `run_evolution` | 3 failed / 6 passed → **0 / 9** |
| `tests/test_kanban_silent_cleanup.py` | *(already routed correctly)* — fixture schema gap | `_record_failure_and_maybe_flag` | 1 failed / 5 passed → **0 / 6** |

Only connections **handed to production code** were converted. The five raw
`sqlite3.connect` calls in `test_value_scorer_and_bulk_move.py` that serve the
file's own assertions and reseeds stay raw on purpose: they are written in
SQLite's own `?` dialect, and routing them through the wrapper would test the
wrapper rather than the code.

## What each failure actually was

### `test_value_scorer_and_bulk_move.py` — one loud case and one silent one

`TestListTasksSort` (5 failures) was the loud kind: `list_tasks` runs
`WHERE kt.status = %s`, the syntax error escaped, and the route 500'd. Two of
those then failed a second time on `r.get_json()["tasks"]` of a `None` body.

`TestBulkMoveEndpoint::test_bulk_dismiss_marks_prediction_dismissed` was the
silent kind, and is the clearest example on this slice. `bulk_move_tasks` dismisses
the backing Oracle prediction inside a nested try/except whose outer handler exists
to tolerate PostgreSQL rejecting `IN (..., NULL)`:

```python
except Exception:
    # Postgres will reject `IN (..., NULL)`, fall back
    # to the portable form that treats NULL as pending.
```

Both arms are `%s`, so both raised, and the innermost handler files the syntax
error as a `warning` in the response and moves on. The task moved to `done`, the
endpoint returned 200, and the prediction stayed `'pending'` — reading exactly
like a dismiss feature that was never wired up.

### `test_nova_sela_evolution.py` — swallowed into a log line

`_query_low_performing_skills` catches its own query failure:

```python
except Exception as exc:
    log.warning("[sela] trace query failed: %s", exc)
```

so the `%s` error became `[]`, the reflex reported no low-performing skills, and
all three assertions read as missing behaviour. Note the grep for
`near "%": syntax error` in this file's pytest output returns **zero** hits —
the error never reaches the report. That is why static scanning, not failure-text
scanning, is what finds these.

The file's `_NoCloseConn` shim (a wrapper that suppressed `close()` so the
in-memory DB survives `run_evolution` closing it) was deleted outright:
`_sql_compat.translating(db, unclosable=True)` already does exactly that, plus
the translation.

### `test_kanban_silent_cleanup.py` — a fixture schema gap of the same shape

This file's fixture was **already** routed correctly through
`_real_get_connection(db_path=...)`, with a comment explaining why. Its single
failure is the same *class* of bug one layer down: `_record_failure_and_maybe_flag`
writes `last_run_summary` in the same UPDATE that bumps `failure_count`, the
fixture's `kanban_tasks` had no such column, and the whole block sits under
`except Exception`. So the bump was swallowed while the reason-only write further
down the stale-cleanup path still succeeded — which is why only the
`failure_count` assertion failed, and why it read as an unimplemented feature.
Fixed by adding the column to the fixture schema.

## A masked second defect

Converting `test_value_scorer_and_bulk_move.py`'s `_fake_conn` did not make
`TestListTasksSort` pass on its own. With the `%s` error gone, the same 5 tests
failed on `no such table: kanban_verifications` — `list_tasks` LEFT JOINs the
newest verification per task for `phantom_ratio` (migration 019) and the fixture
never created that table. The syntax error had been failing first and hiding it.
Worth stating plainly for the next shard: **a converted fixture can surface a
second, unrelated gap, and the conversion is not done until that one is fixed
too.** The table is seeded empty — the join is what the route needs, not any row.

## Before / after

Identical command in both arms, from the worktree root, against a seeded
`data/icdev.db`:

```bash
export ICDEV_STORAGE_BACKEND=sqlite
export PYTHONPATH=<worktree root>
python -m pytest tests/test_value_scorer_and_bulk_move.py \
    tests/test_nova_sela_evolution.py tests/test_kanban_silent_cleanup.py \
    -q -rfE --timeout=90 --continue-on-collection-errors -p no:cacheprovider
```

| | before | after |
|---|---|---|
| passed | 35 | **46** |
| failed | 11 | **0** |
| errors | 0 | 0 |

Across the full 26-file GEN candidate sweep both batches were re-run to check for
regressions: **15 failed / 305 passed → 4 failed / 316 passed**. No test that
passed before fails now.

## The 11, by test

`tests/test_value_scorer_and_bulk_move.py` —
`TestBulkMoveEndpoint::test_bulk_dismiss_marks_prediction_dismissed`,
`TestListTasksSort::test_sort_by_value`,
`TestListTasksSort::test_sort_by_confidence`,
`TestListTasksSort::test_sort_default_preserves_priority_order`,
`TestListTasksSort::test_sort_by_priority`,
`TestListTasksSort::test_sort_priority_secondary_value_tiebreak`,
`TestListTasksSort::test_sort_priority_unknown_rank_bottom`.

`tests/test_nova_sela_evolution.py` — `test_query_returns_low_performers`,
`test_run_evolution_produces_artifact`,
`test_run_evolution_skips_when_no_mutations`.

`tests/test_kanban_silent_cleanup.py` —
`TestSilentCleanup::test_stale_cleanup_bumps_failure_count`.

## How these three were found

Scanned `tests/` for files carrying both `sqlite3.connect` and `get_connection`
whose imports actually reach `tools.genesis` / `tools.oracle` / `tools.autonomy` /
`tools.nova` / `tools.awareness` / `tools.innovation` — the GEN slice is defined by
what a file imports, not by its filename. 36 candidates, ranked by how many
`sqlite3.connect` calls sit within 14 lines of a `get_connection` reference, then
the top 26 were executed in two batches and the results read for both the loud
signature (`near "%": syntax error`) and the silent one (a whole assertion class
reading as unimplemented).

## Known-unrelated failures left in place

Two files in the sweep still fail for causes outside this shard, and were left
untouched so the before/after count stays honest:

- `tests/test_integrity_monitor_reflex.py` (2) — `_rel_path` basename-vs-relpath
  fallback disagreement. Pre-existing, unrelated to SQL dialect.
- `tests/test_red_cell_anomaly_threshold.py` (2) — `calibrate_vuln_threshold`
  does not clamp to the configured `min_floor` / `max_cap`. Pre-existing,
  unrelated to SQL dialect.

Both are real defects worth their own cards. The remaining 10 unexecuted GEN
candidates from the static list are the natural input to the next shard.
