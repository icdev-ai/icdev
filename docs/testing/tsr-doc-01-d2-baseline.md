# TSR DOC — clean-worktree failure baseline (tsr-doc-01-d2)

Diagnostic only. Produced 2026-08-01 on branch `kanban/tsr-doc-01-d2`, a worktree off `origin/main`
at `de2332135`. **No source or test file was modified** — this task measures, it does not fix.

Answers one question: of the 162 files in the corrected DOC slice
([`tsr-doc-01-slice.txt`](tsr-doc-01-slice.txt), produced by tsr-doc-01-d1), which fail today in a
freshly seeded worktree, and by how much?

Raw evidence, full console output per file: [`tsr-doc-01-d2-baseline_clean.log`](tsr-doc-01-d2-baseline_clean.log)
(106 KB; force-added because `.gitignore:68` excludes `*.log`).
Machine-readable: [`tsr-doc-01-d2-baseline.json`](tsr-doc-01-d2-baseline.json).

## 1. Databases seeded

A fresh worktree starts with no `data/*.db`. All three steps ran clean, reproducing the state
tsr-doc-01-d1 documented:

| # | command | exit | result |
|---|---------|------|--------|
| 1 | `python tools/db/init_icdev_db.py` | 0 | `data/icdev.db`, **525 tables**; 8 `wf_templates` + 3 `wf_document_templates` seeded |
| 2 | `python tools/studio/init_db.py` | 0 | 16 studio tables |
| 3 | `python tools/db/migrations/311_studio_event_tables_rls_columns/up.py` | 0 | `Migration 311 applied.` |

Final state: **541 tables** in `data/icdev.db`.

Both env pins are load-bearing and must be repeated by tsr-doc-01-d3 in this worktree — the ambient
environment carries `ICDEV_STORAGE_BACKEND=postgresql`, and an explicit env var beats `load_dotenv`:

```bash
export PYTHONPATH="C:\AI\ICDev\.tmp\worktrees\tsr-doc-01-d2"
export ICDEV_STORAGE_BACKEND=sqlite
unset ICDEV_PG_NO_FALLBACK ICDEV_DB_PATH
```

## 2. Run method

Each file ran in its **own** pytest invocation, so counts are per-file and one wedged file cannot
mask another:

```
python -m pytest <file> -q -rfE --timeout=120 -p no:cacheprovider
```

`-rfE` not `-rf`: `-rf` hides ERRORs, and collection/teardown errors are a live failure mode here
(one of the eight non-clean files is exactly that). `--timeout=120` guards the known
`test_production_audit.py`-style wedge; nothing in this slice hit it.

Runner: `.tmp/doc_baseline_runner.py` (scratch, not committed). Wall clock **616s** for 162 files.

## 3. Totals

| metric | value |
|--------|-------|
| files run | 162 |
| files clean (rc=0) | **154** |
| files failing | **8** (7 FAIL + 1 ERROR) |
| files collecting zero tests | 0 |
| individual failures | **16 failed + 1 error** |
| individual passes | 2539 |
| individual skips | 10 |

The DOC slice is in materially better shape than the sibling TSR slices: 95% of files are green, and
the whole slice's failure count (17) is under a third of INTEL's (60) across twice the files.

## 4. The eight non-clean files

| # | file | failed | errors | passed | observed error |
|---|------|--------|--------|--------|----------------|
| 1 | `tests/test_rted_conflict_detector.py` | 7 | 0 | 4 | `sqlite3.OperationalError: near "%": syntax error` — PG `%s` placeholders on a raw SQLite connection |
| 2 | `tests/test_dsyn_patch_mode.py` | 3 | 0 | 20 | `assert 0 >= 1` ×3 — `TestSuggestionNotifications`, no notification row inserted |
| 3 | `tests/docmod/test_regen_quality_gate.py` | 2 | 0 | 8 | `assert False is True` ×2 — uncited-regeneration block and force-override audit |
| 4 | `tests/browser/test_scope.py` | 1 | 0 | 51 | `assert "'agent_task_completed'" in <source text>` — event type absent from the audit-trail CHECK constraint |
| 5 | `tests/genesis_auto/test_extractors.py` | 1 | 0 | 6 (+10 skipped) | `AssertionError: Missing constant _YIELD_RICH` |
| 6 | `tests/test_dsyn_consistency.py` | 1 | 0 | 15 | `assert 0 >= 1` — consistency flag not emitted on large char delta |
| 7 | `tests/test_idr_multi_source.py` | 1 | 0 | 18 | `CoT should be called when evidence > 500 chars` |
| 8 | `tests/test_dic_techwriter.py` | 0 | **1** | 28 | `Failed: Transaction leak` at teardown of `test_import_from_docgen_valid_template_type_returns_500_or_doc_id` |

These are **observed errors, not diagnosed root causes** — attribution to a line is tsr-doc-01-d3's
job. Only #1 has an obvious, previously-catalogued shape (PG-dialect SQL reaching a raw `sqlite3`
connection); the rest are behavioural assertions that need reading before being called stale or real.

Note the shape of the failure distribution: no file is broadly broken. The worst offender still
passes 4 of 11, and six of the eight files fail a single test. This is drift at the margins, not a
subsystem that is down.

## 5. Slice overlap — DOC is nearly self-contained

| sibling slice | its size | shared with DOC |
|---------------|----------|-----------------|
| `tsr-core-01-slice.txt` | 134 | 11 |
| `tsr-comp-01-slice.txt` | 152 | 5 |
| `tsr-flow-01-slice.txt` | 78 | 4 |
| `tsr-intel-01-slice.txt` | 75 | 4 |
| `tsr-canv-01-slice.txt` | 274 | 2 |
| `tsr-net-01-slice.txt` | 131 | 2 |
| `tsr-dash-01-slice.txt` | 189 | 1 |

None of the eight failing files is shared with another TSR slice, so tsr-doc-01-d3 can be worked in
parallel with the other epics with no collision risk on the files it must touch.

## 6. Slowest files (for anyone sharding d3)

| file | seconds |
|------|---------|
| `tests/test_docgen.py` | 38.0 |
| `tests/govcon/test_past_performance_suggester.py` | 18.4 |
| `tests/test_dic_ingest_orchestrator.py` | 17.8 |
| `tests/test_dic_techwriter.py` | 15.8 |
| `tests/test_dic_ingest_job_anomaly_detection.py` | 14.6 |

All five pass except `test_dic_techwriter.py`.
