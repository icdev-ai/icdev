# TSR DOC — final verification and before/after comparison (tsr-doc-01-d5)

Closing report for the DOC epic. Produced 2026-08-02 on branch `kanban/tsr-doc-01-d5`, a fresh
worktree off `origin/main` at `b680c4077`. **No source or test file was modified to produce it** —
this task measures.

Compares the epic's exit state against the clean-worktree failure baseline established in
[`tsr-doc-01-d2-baseline.md`](tsr-doc-01-d2-baseline.md) (measured at `de2332135`).

## Headline

**The DOC slice went from 17 failing outcomes to 5.** Twelve of the baseline's seventeen were
resolved. The slice was run **twice** end to end; the second run is what establishes that the
remaining 5 are reproducible and that the one extra failure in run 1 is not.

| metric | before (`de2332135`) | after — run 1 | after — run 2 | delta (reproducible) |
|--------|---------------------:|--------------:|--------------:|---------------------:|
| files run | 162 | 162 | 162 | 0 |
| files clean (rc=0) | 154 | 157 | **158** | **+4** |
| files failing | 8 | 5 | **4** | **−4** |
| individual failed | 16 | 5 | **4** | **−12** |
| individual errors | 1 | 1 | **1** | 0 |
| individual passed | 2539 | 2550 | **2551** | +12 |
| individual skipped | 10 | 10 | 10 | 0 |

Selection was identical across all three — the same 162-file `tsr-doc-01-slice.txt`, with no file
present in one run and absent from another (verified, not assumed).

The single difference between the two runs is `tests/test_dic_re_enrich_metadata.py`, which failed
once at position 104/162 and passed on the re-run and in every isolated attempt. It is diagnosed in
§4 and **is not counted as a regression**; the reproducible after-state is **4 files, 4 failures +
1 error**.

**Only 7 of the 12 resolved failures belong to this epic.** The other 5 were fixed by unrelated work
that landed on `main` in the same window. The attribution table below states which is which rather
than letting the epic claim the whole delta.

## Before / after, per file

Every file whose state changed. The 157 files clean in both runs are omitted; all 162 were run.

| file | d2 (before) | run 1 | run 2 | outcome |
|------|-------------|-------|-------|---------|
| `tests/test_rted_conflict_detector.py` | 4p/7f | **11p/0f** | **11p/0f** | **fixed — this epic** |
| `tests/test_dsyn_patch_mode.py` | 20p/3f | **23p/0f** | **23p/0f** | fixed upstream |
| `tests/test_dsyn_consistency.py` | 15p/1f | **16p/0f** | **16p/0f** | fixed upstream |
| `tests/browser/test_scope.py` | 51p/1f | **52p/0f** | **52p/0f** | fixed upstream |
| `tests/docmod/test_regen_quality_gate.py` | 8p/2f | 8p/2f | 8p/2f | unchanged |
| `tests/genesis_auto/test_extractors.py` | 6p/1f | 6p/1f | 6p/1f | unchanged |
| `tests/test_idr_multi_source.py` | 18p/1f | 18p/1f | 18p/1f | unchanged |
| `tests/test_dic_techwriter.py` | 28p/1**e** | 28p/1**e** | 28p/1**e** | unchanged |
| `tests/test_dic_re_enrich_metadata.py` | 5p/0f | **4p/1f** | 5p/0f | transient — §4 |

The four unchanged files reproduce their baseline counts **exactly, test for test**, in both runs.
That is the
evidence that this measurement is comparable to d2's: four independent files agreeing on both pass
and fail counts across a 500+ commit window is not what a mis-seeded database produces by accident.

### Who fixed what

| commit | file(s) | failures | epic |
|--------|---------|---------:|------|
| `d23f8aa66` (PR #1230, tsr-doc-01-d4) | `test_rted_conflict_detector.py` | 7 | **DOC** |
| `d27f8cad4` (PR #1165) | `test_dsyn_patch_mode.py`, `test_dsyn_consistency.py` | 4 | canvas fixtures |
| `b19067d89` (swp-audit-01) | `tests/browser/test_scope.py` | 1 | SWP audit |

`d23f8aa66` is the epic's only fix commit and it is test-side: a hand-rolled `_FakeConn` shim
replaced with `tests/_sql_compat.connect`. **No production code was modified to make a test pass
anywhere in this epic** — worth recording, because that is the failure mode the TSR card exists to
prevent.

`b19067d89` is the more interesting of the two upstream fixes. `test_scope.py` had asserted the
literal `'agent_task_completed'` appeared in `init_icdev_db.py`'s source text; the CHECK constraint
is now *generated* from `audit_logger.VALID_EVENT_TYPES` through an `@@AUDIT_EVENT_TYPES@@`
placeholder, so the literals are no longer in the file. The test now asserts against the substituted
`SCHEMA_SQL`. The d2 baseline's reading — "event type absent from the CHECK constraint" — described
a schema that was correct by construction.

## Remaining failures — all of them, with cause

Four files fail reproducibly (4 failures + 1 error); a fifth failed once and is covered in §4.

### 1. `tests/docmod/test_regen_quality_gate.py` — 2 failed. A gate that fails open.

```
test_uncited_regeneration_blocked_and_withheld   assert out["blocked"] is True   → assert False is True
test_force_override_promotes_and_audits          assert out["forced"] is True    → assert False is True
```

Both failing tests feed the generator an **uncited** section (`"TLS 1.3 secures all endpoints."`,
no `[source:]` tag) and expect the regeneration quality gate to block it. The cited-section test
(`test_clean_regeneration_reaches_pending_review`) passes, as do all five unit-level gate tests at
lines 120–172 — including `test_gate_blocks_uncited_section`.

**So `evaluate_regeneration_quality` is correct in isolation; the integration path does not block.**
`regen_orchestrator.regenerate_document` builds a `_gate` closure and passes it as
`generate_document(..., quality_gate=_gate)`; the closure populates `gate_report`, and the
orchestrator computes `blocked = bool(gate_report.get("blocked"))`. An empty `gate_report` yields
exactly the observed `False`.

The sub-cause was left un-isolated in the first pass of this report, with the broad
`except Exception` at `doc_generator.py:753` named as the likely culprit. **That hypothesis is
wrong and is retracted here.** The discriminating run it called for
(`-o log_cli=true --log-cli-level=WARNING`) was executed in
[`tsr-doc-01-d5-residual-verification.md`](tsr-doc-01-d5-residual-verification.md): **no warning is
emitted**, so the hook never raises and the swallow never fires. `gate_report` is not empty either —
`test_clean_regeneration_reaches_pending_review` dereferences `out["quality_gate"]["blocked"]` and
passes, which an empty dict could not do.

The gate runs cleanly and returns `blocked=False` **on merit**. Tracing its arguments shows why:

```
[DBG] section type = GeneratedSection
[DBG]   heading   = 'Overview'
[DBG]   abstained = True          <-- set by doc_generator, not by the fixture
[DBG]   content   = 'TLS 1.3 secures all endpoints.'
[DBG] section_dicts = []          <-- gate sees ZERO sections
[DBG] blocked = False reasons = []
```

An uncited section derives a confidence below `_CONF_ABSTAIN`, so `doc_generator.py:693` marks it
`abstained=True` and drops it from `full_text`. `regen_quality_gate._section_dicts` then skips every
abstained section by design (`_section_dicts:69`, asserted by `test_gate_skips_abstained_sections`).
The gate is handed an empty list, finds no citation defect in it, and correctly reports
`blocked=False`. `test_force_override_promotes_and_audits` fails for the same reason one step later:
`forced = blocked and force`, and `blocked` is already `False`.

**The two rules are mutually exclusive by construction.** The condition that would trigger
`BLOCK_MISSING_CITATIONS` — a section with no citations — is exactly the condition that makes the
section abstain first. So `BLOCK_MISSING_CITATIONS` is **unreachable** through
`regenerate_document`; it fires only in the gate's own unit tests, which pass plain dicts with
`abstained` unset. The unit tests pass and the integration tests fail because they are exercising
two different section shapes, and only one of them can ever reach the citation check.

**This is compliance-relevant and should be the epic's next card.** CLAUDE.md's TRUST invariants
require promote/export to be gated on citation defects. The gate does not fail open by swallowing an
error — it fails open by never being asked the question. Note that the abstention path is not
silently unsafe on its own (an abstained section is excluded from `full_text` rather than published),
so the fix is a scoping decision, not a one-line patch: either the gate must see abstained sections
and block on a draft that abstained everything, or `regenerate_document` must treat an
all-abstained draft as a blocking outcome in its own right. The failing tests encode the first
reading; the code implements neither.

### 2. `tests/test_dic_techwriter.py` — 1 error. A production connection leak.

```
Transaction leak: ...::test_import_from_docgen_valid_template_type_returns_500_or_doc_id finished
with 1 SQLite connection(s) holding an uncommitted write transaction.
Opened at:  blueprint.py:906 in api_import_from_docgen
            blueprint.py:203 in _conn
            storage.py:1725 in get_connection
```

Not a test defect. `tools/document_intelligence/blueprint.py::api_import_from_docgen` spans lines
853–1053 and opens `conn = _conn()` inside a `try:` that contains **zero `conn.close()` calls and no
`finally:` block** — verified by parsing the function body, not by eye. Every one of its return
paths (including the three `except` branches) leaks the connection.

The 28 other tests in the file pass; the leak guard is what surfaces it, and only because SQLite
makes an uncommitted write transaction visible at teardown. **On PostgreSQL — the primary backend —
this leaks a pooled connection per request** with no test to catch it. The fix is a `finally:
conn.close()`, and it is a production fix, not a test edit.

### 3. `tests/genesis_auto/test_extractors.py` — 1 failed. Asserts a feature that never merged.

```
assert hasattr(mod, "_YIELD_RICH"), "Missing constant _YIELD_RICH"
```

`_YIELD_RICH` does not exist anywhere in the repository — the only occurrence of the string outside
this test is the d4 report quoting the failure. It is not mirror drift: `tools/` and
`icdev/tools/document_intelligence/extractors.py` are byte-identical at 1541 lines and neither has it.

`git log -S` finds it in exactly two commits, `ce4e0b3e2` and `95851fea7` — both titled
*"feat(aiify-opp-6059): DIC extraction-quality anomaly detection"*, and **neither is an ancestor of
`origin/main`**. They live on `irad/feature` and `kanban/aiify-rm-a3344-phase-{64,101}`. The commit
*added* `_YIELD_RICH`, `_YIELD_SPARSE`, `_ANOMALY_STDEV_K` and the `_classify_yield` band logic; the
test that asserts them is on `main`, the feature is not.

This is the [[tests-for-never-merged-code]] shape. **Do not "fix" it by adding the constants** —
that would import an unmerged feature through the back door. The decision is whether the
anomaly-detection work should land or the assertion should go, and it belongs to whoever owns
aiify-opp-6059. The same file's other nine `hasattr` assertions are from the same generated batch
and should be checked together.

### 4. `tests/test_idr_multi_source.py` — 1 failed. Routing contract disagreement.

```
TestCoTActivation::test_cot_called_when_evidence_rich
assert len(cot_calls) > 0, "CoT should be called when evidence > 500 chars"
→ assert 0 > 0
```

Chain-of-Thought is not invoked on evidence over 500 characters. Unchanged from baseline, and 18 of
the file's 19 tests pass. Test and router disagree on a threshold contract; read
`tools/document_intelligence` IDR routing and decide which is authoritative before editing either.
Not diagnosed further here.

### 5. `tests/test_dic_re_enrich_metadata.py` — 1 failed. The one new failure, and it does not reproduce.

See §4. Reported as a stability finding, not a regression.

## 4. The new failure — order-dependent, not a code regression

`test_skip_identifiers_flag` failed at position **104/162** of the first full run:

```
result = ingest.re_enrich_metadata(_DOC_ID, extract_identifiers=False, extract_correspondence=False)
>       assert result is not None
E       assert None is not None
```

What was ruled out, and how:

| check | result |
|---|---|
| the test alone | **passes** (1 passed) |
| the whole file, 3 consecutive runs after the slice | **passes 5/5 every time** |
| **the whole 162-file slice, re-run in the same order** | **passes 5/5** (§5) |
| test file changed since d2 (`de2332135..HEAD`) | **no commits** |
| `re_enrich_metadata` changed since d2 | **no commits** |
| test ordering randomised | no — collection order is stable, no `pytest-randomly` |

`re_enrich_metadata` has exactly **one** `return None` path — `if not row: return None` after
`SELECT doc_id, filename FROM dic_documents WHERE doc_id = %s`. There is no swallowed exception that
could also produce `None`. So at that moment the document row genuinely was not visible, despite the
fixture having `INSERT OR REPLACE`d and committed it in setup.

### The cause: a second session running this same task in this same worktree

Mid-verification, `git status` in this worktree showed staged changes nobody in this session made
(forge_academy edits, a deleted migration, a deleted test) and two untracked artifacts —
`tsr-doc-01-d5-after.json` and `tsr-doc-01-d5-delta.json` — that this session did not write. A
second agent session was dispatched onto **the same task, in the same worktree**, and ran the same
162-file slice against the same `data/icdev.db`. The timing is decisive:

| run | window |
|---|---|
| this session, run 1 | 21:11:54 → 21:31:57 |
| **the other session** | **21:23:25 → 21:42:09** |
| this session, run 2 | 21:34:29 → 22:00:37 |

Run 1 reached `test_dic_re_enrich_metadata.py` (position 104/162) at **21:25:46** — two minutes and
twenty-one seconds *inside* the other session's window. Run 2 reached the same file at roughly
21:51, after that window closed, and it passed.

The mechanism follows directly. The fixture binds to the **real** `data/icdev.db` through
`get_connection()` rather than `tmp_path`, and keys its row on a **fixed** `_DOC_ID =
"dic_doc_re_enrich_test"`. Its teardown issues `DELETE FROM dic_documents WHERE doc_id = ?`. Two
processes running that file concurrently share one row: the other session's teardown deleted the row
this session's test was about to read, and `re_enrich_metadata` correctly reported "doc not found".

**Conclusion: a cross-session race on a shared database row — not a regression, and not an
intra-slice ordering effect.** The underlying defect is real and worth a card (the fixture should
bind to `tmp_path` like `tests/conftest.py::icdev_db` does), but it is a test-isolation defect, not a
DOC subsystem defect, and no DOC fix will make it go away.

### The other session independently corroborates the result

Its run is the closest thing to a replication this epic will get — a separate process, separate
invocation, same commit. Its four reproducibly-failing files and counts are **identical to this
report's**:

| file | this session (both runs) | other session |
|---|---|---|
| `tests/docmod/test_regen_quality_gate.py` | 8p/2f | 8p/2f |
| `tests/genesis_auto/test_extractors.py` | 6p/1f | 6p/1f |
| `tests/test_dic_techwriter.py` | 28p/1e | 28p/1e |
| `tests/test_idr_multi_source.py` | 18p/1f | 18p/1f |

Its run additionally shows `tests/govcon/test_past_performance_suggester.py` at **1 failed + 8
errors** — see §5, where that number matters.

## 5. Stability probe — a second full run against the same database

d3 §5.3 recommended running a slice twice against one database, because a fixture that writes fixed
primary keys through `get_connection()` passes the first time and fails on every run after. This
report does it: the 162 files were run a second time, in the same order, against the database the
first run left behind.

Result: **158 clean, 4 failed + 1 error, 2551 passed** (1568s, against run 1's 1202s).

Two findings.

**1. No file that passed run 1 failed run 2** — but this probe was confounded and should not be
read as clean. The concurrent third run described in §4 interleaved with both, so "two sequential
runs against one database" is not actually what was measured.

`tests/govcon/test_past_performance_suggester.py` — the file d3 described as passing "exactly once
per database" — **passed both of this session's runs**, yet appeared in the other session's run at
**1 failed + 8 errors**, which is d3's `cpmp_contracts.id` UNIQUE-constraint signature exactly. So
d3's mechanism is real and reproduced here; it fired **across two concurrent sessions** rather than
across two sequential runs. Its fixture writes fixed primary keys `c1`/`c2`/`c3` through
`get_connection()` with four `conn.commit()` calls and no cleanup, precisely as d3 documented.

One loose end, stated rather than smoothed over: `cpmp_contracts` **does not exist in this
worktree's `data/icdev.db`** when queried after both runs, so it is not established that those
writes persist to the repo database rather than to some per-process path. Whoever cards this should
determine which database that fixture actually resolves to before relying on either d3's probe or
this observation.

**2. The one difference between this session's two runs is `test_dic_re_enrich_metadata.py`**, and
§4 attributes it to the concurrent session rather than to run order. The direction also rules out
self-pollution: a fixture that poisons its own database would fail the *second* run, not the first.

The 4 files failing in run 2 are the same 4 that failed in run 1 minus that file, with identical
counts. **That is the number this report stands behind.**

## Method

Identical to d2 so the numbers compare. Fresh worktree, seeded with the three steps the TSR card
mandates:

```bash
export PYTHONPATH='C:\AI\ICDev\.tmp\worktrees\tsr-doc-01-d5'
export ICDEV_STORAGE_BACKEND=sqlite       # the kanban environment exports postgresql; an explicit
unset ICDEV_PG_NO_FALLBACK ICDEV_DB_PATH  #   env var beats load_dotenv, so both must be re-pinned
python tools/db/init_icdev_db.py                                      # 527 tables
python tools/studio/init_db.py                                        # studio tables
python tools/db/migrations/311_studio_event_tables_rls_columns/up.py   # Migration 311
```

**543 tables**, against d2's 541. The two extra tables arrived with `main` between the baselines;
they are additive and no DOC-slice file references them.

One pytest process per file, so counts are per-file and one wedged file cannot mask another:

```
python -m pytest <file> -q -rfE --timeout=120 -p no:cacheprovider
```

`-rfE` not `-rf` — `-rf` hides ERRORs, and this slice's single ERROR
(`test_dic_techwriter.py`) is the one that surfaced a production connection leak.

Runner: `.tmp/doc_final_runner.py` (scratch, not committed). Wall clock ~40 min for 162 files,
against d2's 616s; the box was running six concurrent kanban sessions, which affects duration but
not outcomes.

### Environment confound — checked, and absent

[tsr-net-01-d5](tsr-net-01-d5-final-report.md) found a NET file measured as regressed only because the
kanban dispatch environment exported `ICDEV_NETWORK_ENABLED=false` underneath it. That was checked
here: the dispatch environment exports **every** DOC-relevant canvas toggle as `true`
(`ICDEV_DIC_ENABLED`, `ICDEV_DOCGEN_ENABLED`, `ICDEV_GOVCON_ENABLED`, `ICDEV_IDC_ENABLED`), so no
DOC file is measured against a switched-off canvas. The confound does not apply to this slice.

### Scope measured

**All 162 files**, not just the 8 that failed at baseline. The NET d5 report re-ran only its failing
files and named that as its one gap; this report closes it — which is how the new
`test_dic_re_enrich_metadata.py` failure was found at all. A failing-files-only re-run would have
reported a clean −12 and missed it.

### On the "stash / unstash" step in the card

There were no local changes to stash: every DOC fix reached `main` through a PR before this card ran
and the worktree is clean at `b680c4077`. `before` is taken from `tsr-doc-01-d2-baseline.json`, a
measured artifact committed to the repo. `git stash` would also have been the wrong instrument — it
is shared across all worktrees of a repository and several kanban sessions run concurrently against
this one.

## Acceptance

| criterion | status |
|---|---|
| Final results file shows before/after counts | §Headline + per-file table, generated from JSON |
| Remaining failures documented with cause | all 5 named in §Remaining failures |
| PR body text generated with these numbers and named pre-existing failures | [`tsr-doc-01-d5-pr-body.md`](tsr-doc-01-d5-pr-body.md) |

## Artifacts

| file | contents |
|------|----------|
| `docs/testing/tsr-doc-01-d5-final-report.md` | this document |
| `docs/testing/tsr-doc-01-d5-final.json` | per-file after counts, return codes, durations |
| `docs/testing/tsr-doc-01-d5-final.log` | full console output, all 162 files |
| `docs/testing/tsr-doc-01-d5-rerun.json` | second full run, stability probe (§5) |
| `docs/testing/tsr-doc-01-d5-comparison.json` | the d2→d5 diff, generated not hand-written |
| `docs/testing/tsr-doc-01-d5-pr-body.md` | PR body text required by the acceptance criteria |

## Recommended next cards

1. **`test_regen_quality_gate.py` — the citation gate fails open.** Compliance-relevant (TRUST
   invariant). Make `doc_generator.py:762` fail closed, then fix the sub-cause underneath.
2. **`api_import_from_docgen` connection leak** — a `finally: conn.close()` in
   `blueprint.py:853–1053`. Production fix; leaks a pooled connection per request on PostgreSQL.
3. **`test_dic_re_enrich_metadata.py` fixture isolation** — bind to `tmp_path`, not the repo DB.
   Epic-independent; the same shape as the d3 `test_past_performance_suggester.py` finding.
4. **`genesis_auto/test_extractors.py`** — adjudicate aiify-opp-6059: land the feature or drop the
   assertions. Check the file's other nine generated `hasattr` assertions in the same pass.
5. **`test_idr_multi_source.py`** — one assertion; decide the CoT threshold contract.
