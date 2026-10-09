# TSR DOC — the `%s`-on-raw-sqlite fixture, found and fixed (tsr-doc-01-d4)

DOC shard of the `%s`/`?` anti-pattern sweep that produced
[tsr-dash-01-d3](tsr-dash-01-d3-sql-compat.md) and
[tsr-gen-01-d3](tsr-gen-01-d3-sql-compat.md).

**One test file carried the defect. Not two, not three — and that is a measured
result, not a budget that ran out.** §3 is the evidence.

## 1. The defect

The repo authors SQL for PostgreSQL — `%s` placeholders — and
`tools.db.storage.StorageConnection` rewrites them to `?` on the SQLite backend.
A fixture that hands production code a bare `sqlite3` connection removes that
rewrite, so every statement the code under test runs raises:

```
sqlite3.OperationalError: near "%": syntax error
```

`tests/test_rted_conflict_detector.py` did exactly that, through a hand-rolled
shim whose own docstring asserted the opposite of what the code does:

```python
class _FakeConn:
    """Minimal conn shim; conflict_detector uses ? placeholders via existing conn."""
    def execute(self, sql, params=()):
        # conflict_detector passes its caller's conn which uses ?
        return self._db.execute(sql, params)
```

`tools/document_intelligence/conflict_detector.py:41` authors `%s`, and has for
as long as the file has existed:

```python
row = conn.execute(
    "SELECT content FROM dic_sections WHERE section_id = %s LIMIT 1", (section_id,),
).fetchone()
```

Every test that reached the DB died. The four that passed
(`test_compute_hash_*`) never touch a connection. The comment was not describing
the code — it was describing an assumption, and nothing checked it.

## 2. The fix

`_FakeConn` deleted; the fixture now opens `tests/_sql_compat.connect`, which
delegates to the same `tools.db.storage.translate_sql` the runtime uses, so the
fixture cannot drift from the behaviour it stands in for.

```bash
export ICDEV_STORAGE_BACKEND=sqlite
export PYTHONPATH=<worktree root>
python -m pytest tests/test_rted_conflict_detector.py -q -rfE --timeout=120 -p no:cacheprovider
```

| | before | after |
|---|---|---|
| passed | 4 | **11** |
| failed | **7** | **0** |
| `near "%": syntax error` | **7** | **0** |

All 7 pre-existing failures were the syntax error; there were no other failures
in this file, and none were introduced. `ruff check` clean.

The seven: `test_get_section_state_returns_content_and_hash`,
`test_get_section_state_returns_none_for_missing`,
`test_check_conflict_no_conflict_when_hash_matches`,
`test_check_conflict_conflict_when_hash_differs`,
`test_check_conflict_missing_section_returns_no_conflict`,
`test_check_conflict_exposes_current_content`, `test_check_conflict_empty_content`.

These are not hollow after the fix — they assert real returned content and
hashes (`state["content"] == "some content"`), so the passes are earned.

## 3. Why only one file — the probe

Grep is the wrong instrument here. 38 of the DOC slice's 162 files contain a
literal `sqlite3.connect`, but most of those connections only serve assertions
the test writes itself in SQLite's own `?` dialect, which is correct and must
stay. The question is not *does a raw connection exist* but *does an
untranslated `%s` statement reach one*, and only execution can answer that.

So the whole slice was run under a pytest plugin that subclasses
`sqlite3.Connection`/`Cursor` and records any statement containing `%s`:

```python
class ProbeConn(sqlite3.Connection):
    def execute(self, sql, *a, **k):
        _note(sql)                     # records if "%s" in sql
        return super().execute(sql, *a, **k)

sqlite3.connect = lambda *a, **k: _orig_connect(*a, **{**k, "factory": ProbeConn})
```

Production never trips it: `StorageConnection` translates *before* the statement
reaches `sqlite3`. Anything the probe sees arrived untranslated — which is
precisely the defect. It also catches the **silent** variant, where the error is
swallowed by a best-effort `except` and the test asserts against a no-op it
caused itself, because the recording happens before `execute` raises.

Validated against the known-bad file first — it reported exactly the 7 hits
above — then run in two passes, one pytest invocation per file:

| pass | scope | when | hits |
|---|---|---|---|
| 1 | the 38 slice files containing a literal `sqlite3.connect` | **before** the fix | **1** — `test_rted_conflict_detector.py` (7 statements) |
| 2 | all 162 slice files | **after** the fix | **0** |

Pass 1 establishes that no *other* raw-connection fixture in the slice carried
the defect. Pass 2 closes pass 1's grep gap — it makes no assumption about how a
connection was obtained, so a file that had the defect without a literal
`sqlite3.connect` would have surfaced there. It reports nothing.

**`tests/test_rted_conflict_detector.py` was the only affected file in the DOC
slice, and it is now clean.** `coherence_checker.py::check_test_db_isolation`
independently agrees after the change — *"No test hands runtime `%s` code a raw
sqlite3 connection."*

`tests/test_dsyn_consistency.py` and `tests/test_dsyn_patch_mode.py` were the
other plausible candidates on shape — the d2 baseline recorded them failing
`assert 0 >= 1`, the classic swallowed-write signature — but both already route
through `_sql_compat` and both now pass (39 passed). The d2 failure list is
stale for them.

## 4. What this shard did *not* fix

Seven of the eight files in the d2 failure list fail for reasons unrelated to
placeholder translation, and were left alone deliberately — they need behavioural
diagnosis, not a fixture swap:

| file | current failure | shape |
|---|---|---|
| `tests/docmod/test_regen_quality_gate.py` | 2 failed — `out["blocked"] is True` / `out["forced"] is True` both False | gate not firing; real code defect |
| `tests/test_dic_techwriter.py` | 1 error — transaction leak in `blueprint.py:906 api_import_from_docgen` | production connection leak |
| `tests/browser/test_scope.py` | 1 failed — `'agent_task_completed'` absent from the audit-trail CHECK constraint | schema drift |
| `tests/genesis_auto/test_extractors.py` | 1 failed — `Missing constant _YIELD_RICH` | stale assertion or dropped constant |
| `tests/test_idr_multi_source.py` | 1 failed — CoT not called on evidence > 500 chars | routing defect |
| `tests/test_dsyn_consistency.py` | now passes | fixed upstream |
| `tests/test_dsyn_patch_mode.py` | now passes | fixed upstream |

Re-measured in this worktree, so the counts supersede d2 where they differ.

## 5. Unrelated fix carried in this change

`tools/kanban/pr_linker.py:40` used raw `logging.getLogger()`, failing
`coherence_checker.py::check_log_standard` — the single hard `fail` in the
fast-tier gate. It arrived on `main` in `0cb407a5d` and is unrelated to this
task, but it is a one-line migration to `tools.logging.icdev_logger.get_logger`
and it makes the gate green rather than leaving a red check for the next shard to
re-diagnose. `logging` is still imported — `main()` calls `logging.basicConfig`.
The kanban PR-linker tests pass unchanged (22 passed, 1 skipped).
