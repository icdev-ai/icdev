# TSR INTEL — seeded DBs, test-file inventory, and failure baseline (tsr-intel-01-d1)

Diagnostic only. Produced 2026-08-01 on branch `kanban/tsr-intel-01-d1`, a worktree off `origin/main`
at `d3478c079`. **No source or test file was modified.**

Answers three questions for the INTEL epic — the intelligence / analysis `tools.*` packages:

1. Do the three seed steps run clean in a fresh worktree?
2. Which `tests/` files exercise those packages?
3. Which of those files fail today, and how many failures each?

## 1. Databases seeded

A fresh worktree starts with no `data/*.db`, so this is a prerequisite, not a formality. All three
steps ran clean:

| # | command | result |
|---|---------|--------|
| 1 | `python tools/db/init_icdev_db.py` | OK — `data/icdev.db`, **525 tables**, 8 wf_templates + 3 wf_document_templates seeded |
| 2 | `python tools/studio/init_db.py` | OK — studio tables created (exit 0) |
| 3 | `python tools/db/migrations/311_studio_event_tables_rls_columns/up.py` | OK — `Migration 311 applied.` |

Both env pins are load-bearing and must be repeated by any follow-on task in this worktree:

```bash
export PYTHONPATH="C:\AI\ICDev\.tmp\worktrees\tsr-intel-01-d1"   # else: ModuleNotFoundError: No module named 'tools'
export ICDEV_STORAGE_BACKEND=sqlite                              # else the seed half-lands against PG
```

The ambient environment has `ICDEV_STORAGE_BACKEND=postgresql`; without the sqlite pin, step 1 does
not populate the local SQLite file the tests read. This worktree also has **no `.env`**, so nothing
supplies the pin implicitly.

## 2. Scope and selection

The task named 9 packages. **All 9 exist as named** — no substitutions or dropped scope, unlike the
NET slice.

```
tools/strategos (72 py)   tools/intelligence (8)   tools/research (13)
tools/innovation (13)     tools/creative (12)      tools/trading (3 + subpkgs)
tools/fathomdesk (14)     tools/simulation (15)    tools/ttx (12)
```

### Selection regex

```
(?<![\w.])(?:icdev\.)?tools\.(?:strategos|intelligence|research|innovation|creative|trading|fathomdesk|simulation|ttx)(?![\w])
```

Applied to every `*.py` under `tests/` (not just `test_*.py`, matching the CANV/NET slice
convention). Selection is by **what each file references, not by filename**. Note this needs
lookaround, so `rg` refuses it — it was run with Python's `re`.

The two guards matter and were verified against real false positives in this tree:

| guard | rejects |
|-------|---------|
| `(?<![\w.])` before `tools` | `tools.genesis.reflexes.research` (a Genesis reflex, not `tools/research`) |
| `(?![\w])` after the name | `tools.autoresearch` (a separate package) |

Spot-checked exclusions that *look* in-scope by filename but correctly are not:
`tests/test_research_nlp_extractor.py` (imports `tools.genesis.reflexes.research`),
`tests/test_autoresearch.py` (`tools.autoresearch`), `tests/test_network_intelligence.py`
(`tools.network` — NET scope), `tests/e2e_selenium/test_research.py` (drives the browser, imports
`tools.browser`). No `from tools import <pkg>` form exists anywhere under `tests/`.

### Results — 75 files

| # | package | files matching | of which a real `import` |
|---|---------|----------------|--------------------------|
| 1 | `tools/strategos` | 18 | 13 |
| 2 | `tools/trading` | 14 | 10 |
| 3 | `tools/ttx` | 10 | 8 |
| 4 | `tools/intelligence` | 8 | 7 |
| 5 | `tools/innovation` | 7 | 6 |
| 6 | `tools/research` | 6 | 4 |
| 7 | `tools/fathomdesk` | 6 | 6 |
| 8 | `tools/creative` | 5 | 3 |
| 9 | `tools/simulation` | 4 | 3 |

75 distinct files. Per-package counts sum to 78 because a file may exercise more than one package.
The gap between *matching* and *importing* is the patch-string-only set — files that name a package
solely as a `mock.patch("tools.…")` target. Those are still real couplings (they break when the
target module moves) but they will not surface an import-time error, so they are the wrong place to
look first when triaging collection failures.

### Overlap with sibling TSR slices — low

| sibling slice | its size | shared with INTEL |
|---------------|----------|-------------------|
| `tsr-dash-01-slice.txt` | 189 | 8 |
| `tsr-core-01-slice.txt` | 134 | 3 |
| `tsr-canv-01-slice.txt` | 274 | 2 |
| `tsr-flow-01-slice.txt` | 78 | 2 |
| `tsr-net-01-slice.txt` | 131 | 1 |
| `tsr-comp-01-slice.txt` | 152 | 1 |

**62 of 75 files are exclusive to INTEL** (`docs/testing/tsr-intel-01-exclusive.txt`). This slice can
be worked in parallel with the other TSR epics with little collision risk — a materially better
position than NET, where 78 of 131 files were shared with CANV. Only one of the 8 failing files
below (`test_nav_plat_01_simulated_data.py`) is shared, and it is shared with DASH.

## 3. Failure baseline

Each file was run in its **own** pytest invocation, so counts are per-file and one wedged file cannot
mask another:

```
python -m pytest <file> -q -rfE --timeout=120 -p no:cacheprovider
```

`-rfE` not `-rf`: `-rf` hides ERRORs, and collection errors are the expected failure mode in a
freshly seeded worktree. `--timeout=120` guards the known `test_production_audit.py`-style wedge.

### Totals

| metric | value |
|--------|-------|
| files run | 75 |
| files clean (rc=0) | 63 |
| **files failing** | **8** |
| files collecting zero tests (rc=5) | 4 |
| individual failures | **57 failed + 3 errors** |
| individual passes | 1144 |

### Failing files, by failure count

| # | file | failed | errors | passed | package | root cause |
|---|------|--------|--------|--------|---------|------------|
| 1 | `tests/test_threat_level_thresholds.py` | 16 | 0 | 3 | intelligence | conftest schema gap — `indicator_baselines` |
| 2 | `tests/test_simulation_engine.py` | 13 | 0 | 15 | simulation | conftest schema gap — `projects` |
| 3 | `tests/e2e_fathomdesk_trap.py` | 11 | 0 | 12 | fathomdesk | signature drift — `_trap_sweep()` gained a `cfg` arg |
| 4 | `tests/test_register_external_patterns.py` | 7 | 0 | 11 | innovation | stale expected-value assertions |
| 5 | `tests/test_nav_plat_01_simulated_data.py` | 5 | 2 | 3 | trading | `SqliteServerRefused` backend guard |
| 6 | `tests/test_portfolio_greeks.py` | 4 | 0 | 10 | trading | raw `sqlite3` conn + PG `%s` SQL |
| 7 | `tests/test_signal_decay.py` | 1 | 0 | 4 | trading | raw `sqlite3` conn + PG `%s` SQL |
| 8 | `tests/e2e_killswitch_widget.py` | 0 | 1 | 0 | trading | `ModuleNotFoundError: tools.trading.risk` |

Machine-readable, with per-file package attribution and every distinct failure reason:
`docs/testing/tsr-intel-01-baseline.json`.

### Root causes, traced to the line

These are traced, not inferred — each was reproduced individually.

**(a) `MINIMAL_ICDEV_SCHEMA` gap — 29 of the 57 failures (files 1 and 2).**
This is *not* a seeding gap. Both `indicator_baselines` and `projects` **are** present in the
freshly-seeded `data/icdev.db` (verified via `sqlite_master`), and `get_connection()` outside pytest
sees them. Under pytest the tests take the `icdev_db` fixture (`tests/conftest.py:3572`), which
builds a throwaway DB from `MINIMAL_ICDEV_SCHEMA` (`tests/conftest.py:56`) — and that string defines
neither table. Traced call path for file 1:

```
tests/test_threat_level_thresholds.py:331  create_baseline(..., db_path=str(icdev_db))
  tools/threat_analysis/service.py:80      INSERT INTO indicator_baselines ...
    tools/db/storage.py:904                sqlite3.OperationalError: no such table
```

Fix is CLAUDE.md's new-tool checklist point 6 — add the DDL to `MINIMAL_ICDEV_SCHEMA`. Cheapest fix
in the slice by a wide margin: two table definitions clear roughly half the INTEL failures.

**(b) Raw `sqlite3` connection handed PG-dialect SQL — 5 failures (files 6 and 7).**
`tests/test_signal_decay.py:71` builds a fixture with `sqlite3.connect(":memory:")` and passes it
into `tools/trading/db.py`, whose SQL is PG-native:

```sql
SELECT * FROM ad_signals WHERE status=%s ORDER BY (composite_score * signal_decay_weight) DESC LIMIT %s
```

A raw `sqlite3` connection bypasses `StorageCursor`, so `translate_sql` never rewrites `%s` → `?`
and sqlite reports `near "%": syntax error`. Same remedy already applied on this branch for the GEN
and DASH slices (`0727e8467`, `329234796`): route the fixture through the translating connection
rather than editing the SQL.

**(c) `tools.trading.risk` does not exist (file 8).**
`tests/e2e_killswitch_widget.py` fails at collection. `tools/trading/` contains
`analysts, auth, dashboard, data, db.py, market_intel, news, options, oracle, signal_decay.py,
strategist, ta` — there is no `risk` module or package. This is a test for code that is not in the
tree, not a broken import path. Decide whether to write the module or retire the test; do not
"fix the import".

**(d) Files 3, 4, 5 are ordinary drift** — a changed function signature, stale hardcoded expected
values (`assert 11 == 10`, a hardcoded pattern-registry list), and a backend guard that refuses to
start the dashboard against SQLite. Each needs individual triage.

### The 4 rc=5 files are not failures

| file | why |
|------|-----|
| `tests/e2e_confluence_widget.py` | script-style driver: `class ServerThread` + `def main()`, zero `test_` functions |
| `tests/e2e_election_phase_widget.py` | same shape |
| `tests/e2e_exec_quality_widget.py` | same shape |
| `tests/trading/test_data_quality.py` | 1 test, skipped |

The first three are standalone e2e drivers meant to be executed directly, not collected by pytest.
They are correctly in the slice (they do reference in-scope packages) but they will never contribute
a pass or a failure. Counting them as failures would overstate the baseline by 3 files.

## Reproducing

```bash
cd C:/AI/ICDev/.tmp/worktrees/tsr-intel-01-d1
export PYTHONPATH="C:\AI\ICDev\.tmp\worktrees\tsr-intel-01-d1"
export ICDEV_STORAGE_BACKEND=sqlite
python tools/db/init_icdev_db.py
python tools/studio/init_db.py
python tools/db/migrations/311_studio_event_tables_rls_columns/up.py
while read f; do python -m pytest "$f" -q -rfE --timeout=120 -p no:cacheprovider; done \
  < docs/testing/tsr-intel-01-slice.txt
```

Run per-file, not `pytest $(cat slice.txt)`. A single invocation over all 75 lets one file's
module-level state leak into the next and makes per-file attribution guesswork.

## Artifacts

| file | contents |
|------|----------|
| `docs/testing/tsr-intel-01-slice.txt` | 75 file paths, one per line (LF) |
| `docs/testing/tsr-intel-01-exclusive.txt` | 62 paths not claimed by any sibling TSR slice |
| `docs/testing/tsr-intel-01-inventory.json` | per-file package attribution, import vs patch-string, regex, per-package counts |
| `docs/testing/tsr-intel-01-baseline.json` | per-file failed/error/passed/skipped counts, distinct failure reasons, exclusivity flag |
| `docs/testing/tsr-intel-01-baseline.md` | this document |
