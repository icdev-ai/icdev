# TSR COMP — clean-worktree vs shared-checkout failure baseline (tsr-comp-01-d1)

Diagnostic only. Produced 2026-07-31 on branch `kanban/tsr-comp-01-d1`, a worktree off `origin/main`
at `5766e4748`. No source or test file was modified.

Answers the question the COMP epic is blocked on: **of the failures in the compliance/security slice,
which are artefacts of a stale shared checkout and which are genuine defects?**

## Headline

**None of them are ambient state.** All 19 failing files fail *identically* in a freshly seeded clean
worktree and in the long-lived shared checkout at `C:\AI\ICDev`. There is no ambient-state category
to subtract, and no seeded-data category either — the DB seed changed nothing.

| category | files | meaning |
|----------|-------|---------|
| **real defect** | **19** | fails in both environments — fix the code or the test |
| ambient state | 0 | would mean: passes in the shared checkout only |
| setup issue | 0 | would mean: fails only for want of seeded DB state |
| shared-only failure | 0 | would mean: fails only in the shared checkout |
| not measured | 1 | `test_integrity_backdoor_quarantine.py` — see below |

152 files measured, 132 clean in both, 2668 passing tests, **130 failing tests across 19 files**.

**Consequence for the rest of the COMP epic: work it in a clean worktree.** The shared checkout's
1.8 GB populated `data/icdev.db` confers no advantage on this slice, and every remediation task can
be verified in isolation.

## Method

### Seed (clean worktree, run first)

```powershell
$env:ICDEV_STORAGE_BACKEND="sqlite"
python tools/db/init_icdev_db.py                                            # 550 tables
python tools/studio/init_db.py                                              # 0 created, 16 existing
python tools/db/migrations/311_studio_event_tables_rls_columns/up.py        # applied
```

The `ICDEV_STORAGE_BACKEND=sqlite` pin is required — without it the seed half-lands against an
unreachable PostgreSQL. (It is redundant for the *test* run itself: `tests/conftest.py:31` hard-sets
the same value unless `ICDEV_PYTEST_PG` is set.)

### Slice discovery

```powershell
Get-ChildItem -Path tests -Recurse -Filter *.py -File | Select-String -Pattern '(?<![\w.])(?:icdev\.)?tools\.(?:compliance|security|integrity|zta|devsecops|supply_chain|boundary|mbse|ai_governance|redaction)(?![\w])' -List
```

Regex verbatim — matches the canonical `icdev.tools.*` namespace and the legacy `tools.*` shim, and
matches inside `mock.patch("...")` target strings as well as import statements. The lookahead
`(?![\w])` is load-bearing: without it `tools.security` also matches `tools.security_context`, a
different module.

**152 files.** Full list: [`tsr-comp-01-slice.txt`](tsr-comp-01-slice.txt). Note that a non-recursive
`tests/*.py` glob finds only 129 — it misses 23 files under `tests/genesis_auto/`, `tests/security/`,
`tests/e2e/`, `tests/cortex/`, `tests/studio/`, `tests/http/` and `tests/testing/`. Recurse.

### Execution

Each environment ran the same 152 files, in chunks, with `--junit-xml`, `--tb=no`,
`--continue-on-collection-errors` and `--timeout=90 --timeout-method=thread`.

| environment | root | HEAD | working tree |
|-------------|------|------|--------------|
| clean worktree | `.tmp/worktrees/tsr-comp-01-d1` | `5766e4748` | clean, freshly seeded |
| shared checkout | `C:\AI\ICDev` | `de19786e7` | dirty, 1.8 GB `data/icdev.db` |

The shared checkout is 2 commits behind and therefore lacks `tests/_sql_compat.py` (merged in #1051).
**This confounder did not fire**: none of the 7 test files #1051 touched are in the COMP slice, so
zero measured files differ by code version between the two environments. The comparison is valid.

Per-file numbers: [`tsr-comp-01-triage.json`](tsr-comp-01-triage.json).

## The 19 real defects, by failure family

### Family 1 — `%s` placeholders on a raw `sqlite3` connection (6 files, 78 failures — 60% of the slice)

```
sqlite3.OperationalError: near "%": syntax error
```

| file | pass | fail |
|------|-----:|-----:|
| `tests/test_accountability_manager.py` | 4 | 21 |
| `tests/test_canvas_access_integration.py` | 1 | 16 |
| `tests/test_canvas_access.py` | 1 | 12 |
| `tests/test_group_manager.py` | 1 | 12 |
| `tests/test_poam_auto_generator.py` | 6 | 9 |
| `tests/test_ecr_dres.py` | 9 | 3 |

All six open a raw `sqlite3.connect(...)`, which bypasses `tools/db/storage.py::translate_sql`, so the
PostgreSQL-native `%s` placeholders in the code under test reach SQLite unrewritten.

**This is a solved problem.** It is the identical anti-pattern PR #1051 fixed for 8 other files by
adding the shared translating connection in `tests/_sql_compat.py`. None of these six import it.
Applying the existing helper should clear 78 of the 130 failures without touching runtime code.

### Family 2 — `AttributeError` on `*.event_emitter` (3 files, 12 failures)

`tests/test_dsyn_emit_compliance.py`, `test_dsyn_emit_devsecops.py`, `test_dsyn_emit_zig.py` each fail
4/4 patching an attribute that does not exist on `tools.compliance.event_emitter`,
`tools.devsecops.event_emitter` and `tools.security.zig.event_emitter` respectively. A monkeypatch
target has drifted from the module's real surface. Run `api_surface_extractor.py --file <module>`
before repairing.

### Family 3 — assorted, one root cause each (10 files, 40 failures)

| file | fail | signature |
|------|-----:|-----------|
| `tests/test_proposals_ptw_blackhat_api.py` | 10 | `TypeError: 'NoneType' object is not subscriptable` |
| `tests/test_xai_assessor.py` | 10 | `AssertionError: 'not_satisfied' != 'satisfied'` |
| `tests/test_zta_maturity_scorer.py` | 9 | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` |
| `tests/test_ecr_soc2.py` | 2 | `assert 0 >= 1` (collector populates no evidence) |
| `tests/test_integrity_monitor_reflex.py` | 2 | `assert 'somewhere/else/y.py' == 'y.py'` — `_rel_path` basename fallback |
| `tests/test_nav_misc_05_evidence_page.py` | 2 | `SqliteServerRefused: ICDEV dashboard refuses to start` |
| `tests/test_tenant_request_guard.py` | 2 | `TierAccessDenied: ... requires '<MagicMock ...>'` — unconfigured mock |
| `tests/test_ecr_tier.py` | 1 | `assert None == 'enterprise'` |
| `tests/test_security_context.py` | 1 | `assert {'CUI','PUBLIC','UNCLASSIFIED'} == {'CUI','PUBLIC'}` |
| `tests/test_specialist_consult.py` | 1 | `assert None == {'source': 'icdev_council', ...}` |

`test_nav_misc_05_evidence_page.py` deserves a note: it starts the dashboard, whose
`tools/db/backend_guard.py` refuses to start under `ICDEV_STORAGE_BACKEND=sqlite` — which
`conftest.py` unconditionally sets. The test cannot pass in the default suite as written. This is a
genuine defect, not an artefact of how this sweep was run.

`test_xai_assessor.py` is the one file whose counts differ at all between environments (10 fail in the
worktree, 9 in the shared checkout). One assertion is order- or data-sensitive; treat that single test
as flaky and the other 9 as solid.

## The one unmeasured file

`tests/e2e/test_integrity_backdoor_quarantine.py`

- **Clean worktree:** 5/5 error at fixture setup with the Family-1 signature,
  `sqlite3.OperationalError: near "%": syntax error`.
- **Shared checkout:** **hangs.** It survived a 60 s-per-test timeout and had to be killed at the
  chunk level. Isolating it one-file-at-a-time confirmed it is the sole hang — the other five files in
  its batch pass in under a second each.

`--timeout=90 --timeout-method=thread` does not bound this, which points at the hang being in
collection/import rather than inside a test body. Anything that runs the full `tests/` tree against the
shared checkout will stall here. Give it its own remediation task and a hard external timeout.

## Reproducing

The four sweeps took ~5 minutes wall-clock in total. Two traps cost time and are worth naming:

1. **pytest emitted no `file` attribute in the junit XML here.** Falling back to `classname` and
   treating it as a path inflates 129 files into 513 — `tests.test_foo.TestBar` becomes `TestBar.py`.
   Strip trailing CapWords segments off `classname` to recover the module.
2. **A chunk killed by an outer timeout looks exactly like a collection error** — both produce zero
   `<testcase>` elements. Conflating them silently reported 6 healthy files as broken. Track the
   timeout separately and re-run the chunk one file at a time; that is what isolated the hang above.

## Suggested follow-up tasks

| # | scope | files | est. failures cleared |
|---|-------|------:|----------------------:|
| 1 | Apply `tests/_sql_compat.py` to the Family-1 six | 6 | 78 |
| 2 | Repair `event_emitter` monkeypatch targets | 3 | 12 |
| 3 | Family-3 singles, one root cause each | 10 | 40 |
| 4 | Isolate the `test_integrity_backdoor_quarantine.py` collection hang | 1 | 5 |

Task 1 is the whole epic's leverage point and has a merged precedent to copy.
