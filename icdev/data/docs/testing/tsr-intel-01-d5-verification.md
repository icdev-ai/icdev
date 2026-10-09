# TSR INTEL — fathomdesk / ttx slice, smoke test, ruff (tsr-intel-01-d5)

Produced 2026-08-02 on branch `kanban/tsr-intel-01-d5`, a worktree off `origin/main` at `4cf83e860`.

Closes the INTEL epic's fathomdesk + ttx surface. Two of the four fixes are **production defects the
tests caught**, not test drift — see §3.

## 1. Scope

The 16 files the d1 inventory attributes to `tools/fathomdesk` or `tools/ttx`
(`docs/testing/tsr-intel-01-inventory.json`), plus `tests/test_fathomdesk_trap_sweep_anomaly_detection.py`,
which is fathomdesk-named but selected under a different package. 17 files.

Selection is by what each file **references**, so it includes files whose only coupling is a
`mock.patch("tools.ttx…")` target (`test_lpx_team_spend.py`, `test_penta_gd_smoke.py`).

## 2. Baseline → after

Each file in its own pytest invocation (`-q -rfE --timeout=120 -p no:cacheprovider`), against a
worktree seeded per the d1 runbook.

| | files run | files failing | individual failures |
|---|---|---|---|
| baseline | 17 | **3** | 11 failed + 5 errors |
| after | 18 | **0** | 0 |

| file | baseline | cause |
|------|----------|-------|
| `tests/e2e_fathomdesk_trap.py` | 11 failed | raw `sqlite3` conn + `_trap_sweep()` signature drift |
| `tests/test_lpx_team_spend.py` | 5 errors | `migrate()` split its DDL inside a comment |
| `tests/test_penta_gd_schema.py` | 1 failed | stale dead-code assertion for a re-introduced feature |

The 18th file is `tests/test_fathomdesk_decision_memory_path.py`, added here.

## 3. Root causes

**(a) Two failure modes in `e2e_fathomdesk_trap.py` — test-side.**
`TestTrapEventWritePath` handed `_insert_trap_event` a bare `sqlite3.connect(":memory:")`. The reflex
authors PG SQL, so every INSERT raised `near "%": syntax error` inside its best-effort `except` and
the test asserted against a no-op it caused itself. Routed through `tests/_sql_compat.translating`.
Separately, `_trap_sweep` gained a `cfg` argument when its thresholds became hot-reloadable from
`args/ta_config.yaml`; the test still called `_trap_sweep(conn)`. The cfg is now pinned in the test
rather than loaded from the yaml, so the confidence and cooldown assertions survive a retune.

**(b) `apps/ai_gameday/db.py::migrate()` silently skipped tables — PRODUCTION.**
The DDL script was split with `_DDL.strip().split(";")`. The comment
`-- Migration 325 is the shared fix; these blocks keep the app self-bootstrapping` contains a
semicolon, so the split cut mid-comment: the remainder lost its `--` marker, was executed as SQL,
raised `near "these": syntax error`, and **every table declared after that point — `ttx_registrations`
among them — was never created**. Statements are now split with comment lines removed first.
`test_migrate_creates_every_ddl_table` asserts every `CREATE TABLE IF NOT EXISTS` in `_DDL` exists
after `migrate()`; it fails on the pre-fix code with the original syntax error.

**(c) `tools/fathomdesk/decision_memory._parse_entries` never returned an entry — PRODUCTION.**
`re.split` with a capture group returns `[preamble, header, body, header, body, …]`, so headers sit
at **odd** indices. The loop walked from index 0, reading the preamble as a header and every body as
the next one, so no header ever matched. Measured against the live log: 564 `## [` entries, **0
parsed**. `get_pending()` therefore always returned `[]`, `update_with_return()` could never resolve
a decision, and `get_past_context()` injected no lessons — the entire deferred-reflection loop was
inert. After the fix: 564/564. Found because the new path test read back what it had just written.

**(d) `test_penta_gd_schema.py::test_registration_module_deleted` was stale.**
`penta-gd-03` (`dbd1d17d5`) deleted `apps/ai_gameday/registration.py` as dead code and asserted it
stayed deleted. `gdx-reg-01` (`6cfb79e1e`) then re-introduced pre-session registration as a wired
feature — `blueprint.py` imports it and serves `/gameday/session/<id>/register`, with two dedicated
test files. The assertion was removed rather than the live module re-deleted.

## 4. Repo-file pollution, fixed

`decision_memory` writes to the **tracked** `data/fathomdesk_decisions.md`, so merely running
`test_analyst_panel_phaseC.py` appended fabricated decisions to a repo file — which the session
auto-commit hook then committed. The live log still carries rows reading `Test arbitration.`.
`FATHOMDESK_DECISIONS_PATH` now overrides the path (resolved per call, not at import), and the test
sets it via an autouse fixture. Verified: `git status` is clean of `data/` after a full slice run.

While there, the three connections `test_analyst_panel_phaseC.py` hands to
`risk_manager.get_connection` were routed through `tests/_sql_compat` as well — `risk_manager` picks
its placeholder from `conn._dialect` so nothing was breaking, but this clears the
`coherence_checker:test_db_isolation` gate for the file. Its setup and assertion connections stay
raw `sqlite3`: those are test-side reads, and converting them would be zero-effect churn.

## 5. FathomDesk smoke test

`python tools/testing/fathomdesk_smoke.py` against a live instance, authenticated from
`FATHOMDESK_TEST_EMAIL` / `FATHOMDESK_TEST_PASSWORD`.

| | failed | passed | warned |
|---|---|---|---|
| before | 5 | 35 | 4 |
| after | **1** | 34 | 9 |

Four of the five were the smoke test reporting its own conventions incorrectly:

- **Three `no_data` false positives.** The file documents `"no_data" status is accepted as valid
  (sweep hasn't run yet)`, but the required-top-level-key loop ran regardless, so `/api/value/
  fear-greed`, `/api/value/buffett-indicator` and `/api/market/breadth` were reported broken purely
  because their sweep had not run. The required list describes the *populated* shape; it is now
  skipped for a `no_data` body, which is reported as WARN rather than a silent PASS.
- **`/api/radar/latest` 404.** The route exists and answers `404 + {"error": "no_snapshot"}` for the
  same empty-data condition its six siblings express as `200 + {"status": "no_data"}`. The contract
  now accepts that specific body as a no-data WARN. The allowance is narrow: a 404 with any other
  body — including Flask's HTML 404 for a route that genuinely disappeared — still fails, because a
  non-JSON response cannot match.

The remaining failure, `GET /api/auth/me`, is **left failing on purpose — it is a true positive.**
It is unaffected by any file in this change set (the route's code is not in this repository), and it
reports a session-handling defect in the state of the locally running instance, latched at process
start. Suppressing it would mean silencing the one check that reports that condition. Details were
given to the operator directly rather than written down here; the fix is on the instance, not in this
branch.

Note for whoever runs this next: `check_pages` follows redirects and asserts only the final status,
so a page that bounces to `/login` still records PASS. Page results are not evidence of an
authenticated session; `GET /api/auth/me` is the only check that is — which is precisely why it must
not be relaxed to make a run look green. A fully green smoke run against this instance would be
evidence that the check had been weakened, not that the instance had been fixed.

## 6. Ruff

Clean on every file this task modified, and on the exact command CI runs:

```bash
python -m ruff check tools/ tests/ --select E,F,W \
  --ignore E402,E501,E701,E702,E721,E722,E731,E741,F404      # All checks passed!
```

`ruff check` also passes when the modified files are named explicitly, which is stricter — ruff
honours `.gitignore` during directory traversal, so a repo-wide run never reaches
`tools/trading/`.

## 7. Reproducing

```bash
cd <worktree>
export PYTHONPATH="<worktree>"
export ICDEV_STORAGE_BACKEND=sqlite          # else the seed half-lands against PG
python -m tools.db.init_icdev_db
python -m tools.studio.init_db
python tools/db/migrations/311_studio_event_tables_rls_columns/up.py

# per-file, not one invocation over all of them
for f in $(grep -E 'fathomdesk|ttx|penta_gd|analyst_panel|data_gateway|nav_plat|lpx_team|fga_gd03|signal_tuner|tenant_isolation' docs/testing/tsr-intel-01-slice.txt); do
  python -m pytest "$f" -q -rfE --timeout=120 -p no:cacheprovider
done

# smoke test — needs a running FathomDesk; use 127.0.0.1, not localhost
export FATHOMDESK_TEST_EMAIL=... FATHOMDESK_TEST_PASSWORD=...
python -m tools.testing.fathomdesk_smoke --url http://127.0.0.1:5100
```

`python -m tools.testing.fathomdesk_smoke`, not `python tools/testing/fathomdesk_smoke.py`: run by
path from a worktree, Python puts the *script's* directory on `sys.path` and the top-level `tools`
package falls through to the editable install rooted at the shared checkout.

## 8. RED evidence

A test that passes before and after a fix proves nothing, so each production fix was reverted
individually (`git checkout HEAD -- <file>`) and the tests re-run to confirm they actually catch it:

| reverted file | result | observed failure |
|---|---|---|
| `tools/fathomdesk/decision_memory.py` | 5 failed / 0 passed | `assert 0 == 1` — `get_pending()` returns `[]` |
| `apps/ai_gameday/db.py` | 4 failed / 2 passed | `sqlite3.OperationalError: near "these": syntax error` |

The DDL fault takes down **four** tests, not just the new one, because `migrate()` has no `try/except`
around `_DDL` — the malformed fragment aborts the whole migration, so `ttx_registrations` *and*
`ttx_formation_plan` (everything after the comment) are missing. Both files were then restored and
the slice re-run green.

The `_parse_entries` off-by-one was also measured directly against the live 564-entry log:
walking from index 0 matches **0** headers, from index 1 matches **564**.
