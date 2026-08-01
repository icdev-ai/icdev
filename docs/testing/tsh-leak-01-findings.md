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

## Resolution — tsh-leak-01-d3

All 25 leaks are cleared. Verified per-file in isolation (the two
`test_routing_policy_enforcement.py` leaks only reproduce that way) and as one
10-file batch: 21 guard errors before, 0 after, with no new failures.

Two of the fixes are in production code, not the tests:

| Fix | Clears |
|-----|--------|
| `tools/security/ai_telemetry_logger.py` — `rollback()` on the failure path and `close()` in `finally`, on all four methods | both `test_routing_policy_enforcement.py` leaks |
| `tools/govcon/procurement_quote_compare.py::add_quote` — `rollback()` before the duplicate-quote early return | `test_procurement_quote_compare.py::test_add_quote_rejects_duplicate` |

Both were the same shape: a write fails, the handler returns a tidy error dict,
and the connection is abandoned mid-transaction holding a RESERVED lock on the
shared `data/icdev.db` until garbage collection. The caller cannot tell.

The rest are fixtures that returned a connection instead of yielding and closing
it, so the transaction the code under test left open outlived the test:
`test_aca_grading_integrity.py`, `test_aca_rank_recompute.py`,
`test_aca_step_asset_reconcile.py`, `test_aca_xp_ledger.py`,
`test_ato_compliance_dashboard.py` (`ato_db`, which `seeded_ato_db` builds on),
and `test_iqe_seed_queries.py` (five tests opened `sqlite3.connect(":memory:")`
inline; they now share a closing `mem_conn` fixture).

`test_autoresearch.py` and `test_mcp_instrumentation.py` no longer reproduce
their leak in isolation or in batch, before or after this change — they were
already cleared by work merged since the d2 sweep.

### Not fixed here

`test_procurement_quote_compare.py` has 3 pre-existing failures in
`TestAuditTrail`, unrelated to the leak and unchanged by it: `_audit()` inserts
`event_type`/`actor`/`project_id`/`session_id`, the test fixture's `audit_trail`
has none of them, and the INSERT is swallowed — so the tests fail reading back a
row that was never written. The module's event types (`quote.created`,
`procurement.created`, `igce.created`, …) are also absent from
`VALID_EVENT_TYPES`, so the same INSERT is rejected by the CHECK against the real
schema. Both are already carded: **swp-audit-01** (event-type reconciliation) and
**swp-scan-01** (INSERTs naming columns absent from the live schema). Fixing the
fixture alone would turn the tests green while production kept dropping the rows.
