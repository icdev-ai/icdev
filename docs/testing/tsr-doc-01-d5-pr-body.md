# PR body — tsr-doc-01-d5

Text required by the task's acceptance criteria ("PR body text generated with these numbers and
named pre-existing failures"). Reproduced verbatim in the PR for this branch.

---

## docs(tsr): DOC epic final verification — before/after counts (tsr-doc-01-d5)

Closing verification for the TSR **DOC** epic. Docs and measurement artifacts only — **no source or
test file is modified by this PR.**

The 162-file document-intelligence slice (`docs/testing/tsr-doc-01-slice.txt`) was re-run end to end
in a clean worktree off `origin/main` at `b680c4077`, seeded with the three steps the TSR card
mandates (543 tables), and compared against the clean-worktree baseline
`tsr-doc-01-d2-baseline.json` (`de2332135`).

**The slice was run twice.** The second run is what separates the reproducible remainder from a
transient.

A **third** measurement of the six files that changed or still fail was then taken in a second,
independently seeded clean worktree at the branch tip `06fead3d7` — see
[`tsr-doc-01-d5-residual-verification.md`](docs/testing/tsr-doc-01-d5-residual-verification.md). All
six agree test-for-test with both earlier runs, so the after-state below is a property of the tree
rather than of one run. That pass also isolated the docmod gate's root cause and **retracts** the
cause originally published for it.

### Before / after

| metric | before (`de2332135`) | run 1 | run 2 | delta |
|--------|---------------------:|------:|------:|------:|
| files run | 162 | 162 | 162 | 0 |
| files clean (rc=0) | 154 | 157 | **158** | **+4** |
| files failing | 8 | 5 | **4** | **−4** |
| individual failed | 16 | 5 | **4** | **−12** |
| individual errors | 1 | 1 | **1** | 0 |
| individual passed | 2539 | 2550 | **2551** | +12 |
| individual skipped | 10 | 10 | 10 | 0 |

**17 failing outcomes → 5.** Identical file selection in all three runs.

### Resolved — 12 failures, 4 files

| file | before | after | fixed by |
|------|--------|-------|----------|
| `tests/test_rted_conflict_detector.py` | 4p/7f | 11p/0f | `d23f8aa66` — **this epic** (PR #1230, tsr-doc-01-d4) |
| `tests/test_dsyn_patch_mode.py` | 20p/3f | 23p/0f | `d27f8cad4` (PR #1165) — canvas fixtures |
| `tests/test_dsyn_consistency.py` | 15p/1f | 16p/0f | `d27f8cad4` (PR #1165) — canvas fixtures |
| `tests/browser/test_scope.py` | 51p/1f | 52p/0f | `b19067d89` — swp-audit-01 |

**Only 7 of the 12 belong to the DOC epic**; the other 5 were fixed by unrelated work that landed on
`main` in the same window. The epic's one fix commit is test-side (a hand-rolled `_FakeConn` shim
replaced with `tests/_sql_compat.connect`) — **no production code was modified to make a test pass
anywhere in this epic.**

### Pre-existing failures that remain — 4 files, 4 failures + 1 error

All four were already failing at the d2 baseline and reproduce their baseline counts exactly, test
for test, in both runs. None is a regression introduced by this epic.

| file | count | cause |
|------|-------|-------|
| `tests/docmod/test_regen_quality_gate.py` | 2 failed | **`BLOCK_MISSING_CITATIONS` is unreachable through `regenerate_document`.** An uncited section scores below `_CONF_ABSTAIN`, so `doc_generator.py:693` marks it `abstained=True`; `regen_quality_gate._section_dicts:69` then drops every abstained section, so the gate evaluates an empty list and correctly returns `blocked=False`. The condition that would trip the citation rule is the same condition that makes the section abstain first — the two rules are mutually exclusive by construction. The 5 unit-level gate tests pass because they hand it plain dicts with `abstained` unset, so the unit suite cannot detect this. **Compliance-relevant** (TRUST invariants), though narrower than "fails open": abstained prose is excluded from the persisted text, so what is lost is the blocking decision and its audit note, not containment. Isolated in [`tsr-doc-01-d5-residual-verification.md`](docs/testing/tsr-doc-01-d5-residual-verification.md), which also **retracts this row's earlier claim** that `doc_generator.py:753`'s `except Exception` swallowed the failure — the discriminating run emits no warning, so the hook never raises. |
| `tests/test_dic_techwriter.py` | 1 error | **Production connection leak.** `blueprint.py::api_import_from_docgen` (lines 853–1053) opens `conn = _conn()` with zero `conn.close()` calls and no `finally:` — every return path leaks. Surfaced only because SQLite exposes the uncommitted write transaction at teardown; on PostgreSQL this leaks a pooled connection per request. Not a test defect. |
| `tests/genesis_auto/test_extractors.py` | 1 failed | **Asserts a feature that never merged.** `_YIELD_RICH` exists in no file in the repo, and both trees (`tools/`, `icdev/tools/`) are byte-identical at 1541 lines, so it is not mirror drift. `git log -S` finds it only in `ce4e0b3e2` / `95851fea7` (aiify-opp-6059), **neither an ancestor of `origin/main`**. Do not fix by adding the constants. |
| `tests/test_idr_multi_source.py` | 1 failed | CoT not invoked on evidence > 500 chars — test and router disagree on a threshold contract. 18 of 19 tests in the file pass. |

### One transient — caused by a duplicate dispatch, not by the code

`tests/test_dic_re_enrich_metadata.py` failed once (4p/1f) at position 104/162 in run 1, and passed
(5p/0f) in run 2 at the same position in the same order, plus 1/1 isolated and 5/5 on three
consecutive file runs. Neither the test nor `re_enrich_metadata` has any commit since the baseline,
and ordering is not randomised.

**A second agent session was dispatched onto this same task, in this same worktree, and ran the same
slice against the same `data/icdev.db`.** It left `tsr-doc-01-d5-after.json` / `-delta.json`
untracked here, plus unrelated staged changes this session did not make.

| run | window |
|---|---|
| this session, run 1 | 21:11:54 → 21:31:57 |
| **other session** | **21:23:25 → 21:42:09** |
| this session, run 2 | 21:34:29 → 22:00:37 |

Run 1 hit the failing file at **21:25:46** — inside the other session's window. Run 2 hit it ~21:51,
after it closed, and passed. The fixture binds to the real `data/icdev.db` via `get_connection()`
(not `tmp_path`) and keys on a fixed `_DOC_ID`, with a teardown that `DELETE`s that row — so two
concurrent processes share one row and one deletes what the other is about to read.

**Classified as a cross-session test-isolation defect, not a regression, and not counted against the
after-state.**

### The duplicate dispatch independently corroborates this result

The other session's four reproducibly-failing files and counts are **identical to this report's**
(`regen_quality_gate` 8p/2f, `extractors` 6p/1f, `techwriter` 28p/1e, `idr_multi_source` 18p/1f) —
a separate process, separate invocation, same commit, same answer.

⚠️ **Harness note:** two sessions ran `tsr-doc-01-d5` concurrently in one worktree. This PR contains
only this session's artifacts; the other session's untracked files and staged changes were
deliberately left untouched.

### Also measured

- **d3's once-per-database mechanism reproduced — across sessions, not across runs.**
  `tests/govcon/test_past_performance_suggester.py` passed both of this session's runs but appeared
  in the other session's run at **1 failed + 8 errors**, d3's `cpmp_contracts.id` UNIQUE-constraint
  signature exactly. Loose end stated rather than smoothed over: `cpmp_contracts` does not exist in
  this worktree's `data/icdev.db` after both runs, so which database that fixture actually resolves
  to is unresolved and should be settled before relying on d3's probe.
- **The NET d5 environment confound does not apply.** The kanban dispatch environment exports every
  DOC-relevant canvas toggle (`ICDEV_DIC_ENABLED`, `ICDEV_DOCGEN_ENABLED`, `ICDEV_GOVCON_ENABLED`,
  `ICDEV_IDC_ENABLED`) as `true`, so no DOC file was measured against a switched-off canvas.
- **All 162 files were re-run**, not just the 8 that failed at baseline. That is how the transient
  was found; a failing-files-only re-run would have reported a clean −12 and missed it.

### Artifacts

| file | contents |
|------|----------|
| `docs/testing/tsr-doc-01-d5-final-report.md` | full report |
| `docs/testing/tsr-doc-01-d5-final.json` / `.log` | run 1, per-file counts + full console output |
| `docs/testing/tsr-doc-01-d5-rerun.json` / `.log` | run 2, stability probe |
| `docs/testing/tsr-doc-01-d5-comparison.json` | the d2→d5 diff, generated not hand-written |
| `docs/testing/tsr-doc-01-d5-pr-body.md` | this text |

### Recommended next cards

1. `test_regen_quality_gate.py` — make the citation gate fail **closed** (TRUST invariant), then fix
   the sub-cause underneath.
2. `api_import_from_docgen` — add `finally: conn.close()`. Production fix.
3. `test_dic_re_enrich_metadata.py` — bind the fixture to `tmp_path`, not the repo DB.
4. `genesis_auto/test_extractors.py` — adjudicate aiify-opp-6059; check the file's other nine
   generated `hasattr` assertions in the same pass.
5. `test_idr_multi_source.py` — decide the CoT threshold contract.
