# TSR DASH — bare `sqlite3.connect` patches converted to the translating wrapper (tsr-dash-01-d3)

Third shard of the `%s`/`?` anti-pattern sweep started in `2aac79b5e`. Three test
files, converted to `tests/_sql_compat.connect`.

## The defect

The repo authors SQL for PostgreSQL — `%s` placeholders — and
`tools.db.storage.StorageConnection` rewrites them to `?` on the SQLite backend.
A test that patches `get_connection` (or a module-private `_get_connection`) with
a bare `sqlite3.connect` removes that rewrite, so every statement the code under
test runs raises:

```
sqlite3.OperationalError: near "%": syntax error
```

The failure reads as a broken feature. It is a broken fixture.

## Files converted

| file | patched symbol | consumer that needs the rewrite |
|---|---|---|
| `tests/test_autonomy_engine.py` | `tools.autonomy.trust_engine._get_connection` | `get_trust_state`, `observe` |
| `tests/test_history.py` | `get_connection` on 4 `tools.network.*` modules | `poam_generator`, `exception_registry` |
| `tests/tech_radar/test_tech_radar.py` | `tools.db.storage.get_connection` | `RadarEngine.run` |

Only connections **handed to production code** were converted.
`tests/test_history.py::_get_conn` stays a bare `sqlite3.connect` on purpose — it
serves only assertions this file writes itself, in SQLite's own `?` dialect, and
routing it through the wrapper would test the wrapper rather than the code.

`tests/tech_radar` also converted the `_open_conn` in
`test_run_full_cycle_no_persist_on_dry_run`, which passed before the change. It is
the same closure feeding the same patched symbol; it survived only because the
dry-run path never reaches a parameterised statement. Left raw it would fail the
moment that path grew one.

## Before / after

Identical command in both arms, from the worktree root:

```bash
export ICDEV_STORAGE_BACKEND=sqlite
export PYTHONPATH=<worktree root>
python -m pytest tests/test_autonomy_engine.py tests/test_history.py \
    tests/tech_radar/test_tech_radar.py \
    -q -rfE --timeout=60 --continue-on-collection-errors -p no:cacheprovider
```

| | before | after |
|---|---|---|
| passed | 36 | **48** |
| failed | 12 | **0** |
| errors | 0 | 0 |
| `near "%": syntax error` | **12** | **0** |

All 12 pre-existing failures were `near "%": syntax error`; there were no other
failures in these three files, and none were introduced.

### The 12, by test

`tests/test_autonomy_engine.py::TestTrustEngine` — `test_get_trust_state_initializes`,
`test_observe_success_increases_alpha`, `test_observe_failure_increases_beta`,
`test_ceiling_enforced`.

`tests/test_history.py` — `TestPoam::test_generate_poam_inserts_row`,
`TestPoam::test_generate_poam_with_unknown_advisory_uses_data`,
`TestExceptionRegistry::test_file_exception_inserts_row`,
`TestExceptionRegistry::test_file_exception_with_advisory_id`,
`TestExceptionRegistry::test_approve_exception_isso_sets_flag`,
`TestExceptionRegistry::test_approve_exception_invalid_level_raises`,
`TestExceptionRegistry::test_list_exceptions_filters_by_status`.

`tests/tech_radar/test_tech_radar.py::test_run_full_cycle_persists_ring_change`.

## The gate rejected its own remedy

`coherence_checker.py::check_test_db_isolation` flags a file that patches a DB
connection factory while a raw `sqlite3.connect` appears anywhere in it. After the
conversion, `tests/tech_radar/test_tech_radar.py:248` was **still flagged** — the
patched factory (`_open_conn`) now returns `_sql_compat.connect`, but the file
retains a raw `sqlite3.connect` at line 90 for schema seeding, which is correct
and must stay: that connection is never handed to production code.

Left alone, the gate would fail the very change that fixes the defect, and the
obvious way to silence it (deleting the seeding connection) would be strictly
worse. So the check now clears a patch call whose replacement is a factory that
returns a `_sql_compat` connection:

- `_sql_compat_factory_names` — collects local factories that call
  `_sql_compat.connect`/`translating` **and contain no raw `sqlite3.connect`**.
  A factory that mixes both stays a violation.
- `_patch_replacement_names` — reads the replacement out of `side_effect=`,
  `new=`, `return_value=`, or the trailing positional of `monkeypatch.setattr`.

Only the factory actually named in the patch call is cleared; a second, still-raw
factory in the same file is still reported.

Verified narrow, not neutering:

| arm | `check_test_db_isolation` on the 3 files | repo-wide violations |
|---|---|---|
| before exemption | **fail** (1: `test_tech_radar.py:248`) | 195 |
| after exemption | **pass** | 195 |

The repo-wide count is unchanged, so no genuine violation was silenced.

## How these three were found

The DASH slice's own failures are dominated by a *different* cause —
`tools.db.backend_guard.SqliteServerRefused`, which the dashboard route tests hit
before any SQL runs — so the `%s` cases do not surface by reading that slice's
failure list. They were found by scanning `tests/` for a bare `sqlite3.connect`
within 12 lines of a `get_connection` reference (135 files), then running the
highest-density candidates and grepping the output for the error string. Of 30
files executed across two batches, 4 carried the defect; the fourth,
`tests/unit/test_audit_trail.py` (1 failure), was left for a sibling shard
because it also carries unrelated transaction-leak errors that would confound the
before/after count.

Remaining unscanned candidates from that static list are the natural input to the
next shard.
