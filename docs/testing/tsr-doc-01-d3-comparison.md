# TSR DOC — populated-checkout baseline and clean/populated comparison (tsr-doc-01-d3)

Diagnostic only. Produced 2026-08-01. **No source or test file was modified** — this task measures.

[tsr-doc-01-d2](tsr-doc-01-d2-baseline.md) ran the 162-file DOC slice in a freshly seeded worktree.
This task ran the **identical selection** in the populated shared checkout `C:\AI\ICDev` and asks one
question: does the environment change the answer?

It does — in exactly one file, and for a reason worth knowing.

Evidence:
- [`tsr-doc-01-d3-baseline_populated.log`](tsr-doc-01-d3-baseline_populated.log) — full console output per file
- [`tsr-doc-01-d3-baseline_populated.json`](tsr-doc-01-d3-baseline_populated.json) — machine-readable per-file counts
- [`tsr-doc-01-d3-comparison.json`](tsr-doc-01-d3-comparison.json) — the diff, generated not hand-written
- [`tsr-doc-01-d3-control.log`](tsr-doc-01-d3-control.log) / [`.json`](tsr-doc-01-d3-control.json) — the control cell (§4)

Logs are force-added; `.gitignore:68` excludes `*.log`.

## 1. Run conditions

Method held constant with d2 — one pytest invocation per file, so counts are per-file and one wedged
file cannot mask another:

```
python -m pytest <file> -q -rfE --timeout=120 -p no:cacheprovider
```

```bash
cd C:\AI\ICDev
export PYTHONPATH="C:\AI\ICDev"
export ICDEV_STORAGE_BACKEND=sqlite      # the ambient env carries postgresql; an explicit
unset ICDEV_PG_NO_FALLBACK ICDEV_DB_PATH #   env var beats load_dotenv, so this must be re-pinned
```

| | d2 (clean) | d3 (populated) |
|---|---|---|
| checkout | fresh worktree | `C:\AI\ICDev`, the shared developer checkout |
| code | `de2332135` | `08466d7f3` (`== origin/main`) |
| `data/icdev.db` | seeded from scratch — **541 tables**, no rows beyond seed | the live developer DB — **920 tables**, months of accumulated rows |
| slice file | `tsr-doc-01-slice.txt`, 162 files | same file, byte-identical |
| wall clock | 616s | 632s |

Two variables moved, not one: the checkout carries a different commit as well as a different DB.
§4 resolves which one did the work.

## 2. Totals

| metric | d2 clean | d3 populated | delta |
|--------|---------:|-------------:|------:|
| files run | 162 | 162 | 0 |
| files clean (rc=0) | 154 | **153** | **−1** |
| files failing | 8 | **9** | **+1** |
| files collecting zero tests | 0 | 0 | 0 |
| individual passed | 2539 | 2534 | −5 |
| individual failed | 16 | 17 | +1 |
| individual errors | 1 | 9 | +8 |
| individual skipped | 10 | 10 | 0 |

Selection was identical — no file present in one run and absent from the other.

## 3. The single discrepancy

Every one of d2's eight failing files fails in d3 with the **same counts and the same messages**. The
whole delta is one file:

| file | d2 | d3 |
|------|----|----|
| `tests/govcon/test_past_performance_suggester.py` | 5 passed, rc=0 | 0 passed, **1 failed + 8 errors**, rc=1 |

Nine of that file's ten outcomes are the same defect surfacing twice per test (a setup error plus the
transaction-leak guard firing on the connection the failed setup left open):

```
ERROR  ...::test_ranked_suggestions_with_citations
       sqlite3.IntegrityError: UNIQUE constraint failed: cpmp_contracts.id
ERROR  ...::test_ranked_suggestions_with_citations
       Failed: Transaction leak: ... finished with 1 SQLite connection(s) holding an
       uncommitted write transaction
FAILED ...::test_missing_tables_degrade_gracefully
       sqlite3.IntegrityError: FOREIGN KEY constraint failed
```

### Why

`tests/govcon/test_past_performance_suggester.py:93` — the `seeded_conn` fixture:

```python
@pytest.fixture()
def seeded_conn():
    from tools.db.storage import get_connection
    conn = get_connection()          # <- the REAL data/icdev.db, not a tmp_path DB
    _create_tables(conn)
    _insert_contract(conn, cid="c1", ...)   # <- hardcoded PKs, no cleanup, no rollback
```

The fixture writes fixture rows with fixed primary keys `c1`/`c2`/`c3` straight into whatever
`data/icdev.db` the cwd resolves to, and never removes them. So:

- against a DB where those ids are free, it passes;
- against a DB where a previous run already inserted them, every test using the fixture dies in
  setup on the `cpmp_contracts.id` UNIQUE constraint.

This is not a flake and not a race. **The test passes exactly once per database and fails on every
run after that.** A pre-run read of the live DB confirms it: `c1`, `c2`, `c3` were *already present*
before this baseline started, left there by some earlier run — which is why d3 failed at file #1
rather than polluting and then failing next time.

It is the [`tests/conftest.py::icdev_db(tmp_path)`](../../tests/conftest.py) pattern being skipped:
sibling fixtures bind to a `tmp_path` DB, this one binds to the repo's.

## 4. Control cell — the DB did it, not the commit

d3 changed two things at once, so a third cell was run to separate them: the **stale code**
(`08466d7f3`, exactly what the populated checkout carries) against a **freshly seeded 541-table DB**
(exactly what d2 had), for all nine files that fail in either run.

| file | d2<br>new code + seeded db | control<br>stale code + seeded db | d3<br>stale code + populated db |
|------|:--:|:--:|:--:|
| `tests/govcon/test_past_performance_suggester.py` | PASS | **PASS** | **FAIL** |
| `tests/browser/test_scope.py` | FAIL | FAIL | FAIL |
| `tests/docmod/test_regen_quality_gate.py` | FAIL | FAIL | FAIL |
| `tests/genesis_auto/test_extractors.py` | FAIL | FAIL | FAIL |
| `tests/test_dic_techwriter.py` | FAIL | FAIL | FAIL |
| `tests/test_dsyn_consistency.py` | FAIL | FAIL | FAIL |
| `tests/test_dsyn_patch_mode.py` | FAIL | FAIL | FAIL |
| `tests/test_idr_multi_source.py` | FAIL | FAIL | FAIL |
| `tests/test_rted_conflict_detector.py` | FAIL | FAIL | FAIL |

Holding the code at d3's commit and only restoring the DB restores the pass (5 passed, rc=0,
identical to d2). **The discrepancy is caused by DB state, not by the 549-file commit delta**, and
the other eight failures are code-level — they reproduce in all three cells with identical counts.

## 5. What this means for the rest of the TSR epic

1. **The d2 failure list is the real one.** It is reproducible under both DB conditions, so
   tsr-doc-01's remediation slice needs no adjustment. The eight files stand.
2. **One extra defect, of a different kind, was found here and only here.**
   `test_past_performance_suggester.py` is not a stale assertion — it is a test-isolation bug, and a
   clean-worktree baseline is structurally incapable of seeing it. That is the argument for running
   this second cell at all.
3. **A green run in a fresh worktree does not mean a developer will see green.** Any sibling TSR
   slice baselined only in a clean worktree may be hiding the same class of defect. The cheap probe
   is to re-run a slice twice in a row against the same DB: a fixture that writes fixed primary keys
   through `get_connection()` passes the first time and fails the second.

## 6. Shared-checkout safety

The live `data/icdev.db` was snapshotted before the run and re-read after. `cpmp_contracts` = 5 rows
and `kanban_tasks` = 710 both before and after — this baseline did **not** mutate the shared DB. The
one write it attempted was rejected by the UNIQUE constraint and rolled back by the transaction
guard.
