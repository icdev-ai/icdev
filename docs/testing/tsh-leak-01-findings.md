# CUI // SP-CTI

# tsh-leak-01-d2 — suite sweep with the transaction-leak guard

Ran the pytest suite with the guard from #1066 (`tests/_txn_guard.py` +
`assert_no_leaked_transaction` in `tests/conftest.py`) active, to get the leaking
tests named instead of inferred.

- **Date:** 2026-07-31
- **Worktree:** `kanban/tsh-leak-01-d2`, seeded `data/icdev.db` (541 tables)
- **Backend:** `ICDEV_STORAGE_BACKEND=sqlite`
- **Scope:** 1850 test files, run in 31 batches of 60
- **Result:** **25 leaking tests across 10 files.** 4 of them leak on the
  *shared* `data/icdev.db` — those are the only ones that can block another test.

## The four that matter

These finish with an uncommitted write transaction on the repo's shared
`data/icdev.db`, so they hold a write lock that a later test contending for the
same file will block on. This is the failure mode tsh-leak-01 was opened for.

| Test | DB |
|------|-----|
| `tests/test_autoresearch.py::TestGenesisReflex::test_reflex_interface` | `data/icdev.db` |
| `tests/test_mcp_instrumentation.py::TestLLMRouterInstrumentation::test_successful_invoke_creates_span` | `data/icdev.db` |
| `tests/test_routing_policy_enforcement.py::test_chain_walk_falls_through_to_the_LOCAL_model_instead_of_the_cloud_one` | `data/icdev.db` |
| `tests/test_routing_policy_enforcement.py::test_ordinary_content_still_reaches_the_cloud` | `data/icdev.db` |

Both `test_autoresearch.py` and `test_mcp_instrumentation.py` *do* define a
`tmp_path` database fixture — but the connection left open is on the real
`data/icdev.db`, i.e. the code under test fell through to the production
connection rather than the redirected one. Worth fixing at that seam, not by
adding a `commit()`.

The two `test_routing_policy_enforcement.py` leaks are **order-dependent**: they
reproduce when that file runs alone but not when it runs alongside the other
flagged files, so a fix must be verified with the file run on its own.

## Verified harmless, but still flagged

| Test | DB |
|------|-----|
| `tests/test_procurement_quote_compare.py::TestQuoteCapture::test_add_quote_rejects_duplicate` | per-test `tmp_path` |
| 20 tests in `test_aca_grading_integrity.py`, `test_aca_rank_recompute.py`, `test_aca_step_asset_reconcile.py`, `test_aca_xp_ledger.py`, `test_ato_compliance_dashboard.py`, `test_iqe_seed_queries.py` | `:memory:` |

An in-memory or per-test-`tmp_path` connection is private to the test, so it
cannot block anything — the transaction dies with the connection. They are real
findings against the guard's rule but carry no cross-test risk; treat them as
hygiene, below the four above.

The `test_aca_*` cluster is one shared pattern: `_academy_conn.academy_conn()`
defaults to `:memory:`, the fixture seeds and commits, then the code under test
runs DML and leaves the commit to a caller that never comes. Fixing the fixture
to close the connection in teardown clears all 13 at once.

Full node-id list: [tsh-leak-01-leaks.txt](tsh-leak-01-leaks.txt).

## Re-running this

```bash
export PYTHONPATH=<repo-root> ICDEV_STORAGE_BACKEND=sqlite COLUMNS=200
python -m pytest -q --tb=no <files>
grep 'Failed: Transaction' <log>
```

Two things will silently cost you a whole run if you skip them:

1. **`COLUMNS=200` is load-bearing.** The guard fires in *teardown*, so it lands
   as an `ERROR` in pytest's short summary, and pytest truncates that line to the
   terminal width. At the default 80 columns it reads `... - Failed:
   Transaction...` — so the obvious `grep 'Transaction leak'` matches **nothing**
   and the run looks clean. Grep `Failed: Transaction`, and widen the terminal.
2. **The guard only applies under `tests/`.** A probe file placed outside that
   tree never loads `tests/conftest.py`, passes happily, and reads as "no leak".

Batch the run (this sweep used 60 files per `pytest` process). Beyond capping the
blast radius of a hang, it means one file that errors at *collection* only costs
its own batch.

## Coverage note

One batch aborted at collection: `tests/test_pgp_tx02_json_portability.py` raises
`SqliteServerRefused` at import (`ICDEV_STORAGE_BACKEND=sqlite` against an install
that sets `ICDEV_PG_NO_FALLBACK`), which interrupts the whole batch. That batch
was re-run with the file excluded so its other 59 files were still swept — that
re-run is where the `test_procurement_quote_compare.py` leak came from. So
1849 of 1850 files were swept; `test_pgp_tx02_json_portability.py` is the one
file never checked for leaks, and it needs a PG-backed run.

No batch hit the 900s timeout.
