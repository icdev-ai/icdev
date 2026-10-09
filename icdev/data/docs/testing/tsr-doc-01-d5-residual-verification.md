# TSR DOC — residual-failure re-verification and root-cause isolation (tsr-doc-01-d5)

Addendum to [`tsr-doc-01-d5-final-report.md`](tsr-doc-01-d5-final-report.md). Two things the
closing report left open are closed here:

1. Its residual counts were measured at `b680c4077`. They are **re-measured** below in a second,
   independently seeded clean worktree at `06fead3d7` (the d5 branch tip, which carries `main`
   through `df1c74c20`) to confirm they are properties of the tree and not of one run.
2. Its §1 named a sub-cause it explicitly did **not** isolate, and prescribed the run that would.
   That run was executed. **The hypothesis was wrong**; the real cause is isolated below and §1 has
   been corrected rather than left standing.

Measurement only — no source or test file was modified.

## Environment

Fresh worktree `C:/AI/.wt/tsrdoc-d5-r2` off `kanban/tsr-doc-01-d5` @ `06fead3d7`, seeded per the
epic's d1 recipe (an unseeded worktree hangs in `storage.py::execute` rather than failing):

```
ICDEV_STORAGE_BACKEND=sqlite
python tools/db/init_icdev_db.py                                        # rc=0, 527 tables
python tools/studio/init_db.py                                          # rc=0
python tools/db/migrations/311_studio_event_tables_rls_columns/up.py    # rc=0
```

Per-file invocation: `python -m pytest <file> -p no:randomly -q`.

## Residual counts — re-measured

| file | d2 baseline (`de2332135`) | d5 report (`b680c4077`) | this run (`06fead3d7`) | agrees |
|------|---------------------------|-------------------------|------------------------|--------|
| `tests/docmod/test_regen_quality_gate.py` | 8p/2f | 8p/2f | **8p/2f** | yes |
| `tests/genesis_auto/test_extractors.py` | 6p/1f | 6p/1f | **6p/1f** (10 skipped) | yes |
| `tests/test_idr_multi_source.py` | 18p/1f | 18p/1f | **18p/1f** | yes |
| `tests/test_dic_techwriter.py` | 28p/1**e** | 28p/1**e** | **28p/1e** | yes |
| `tests/test_dic_re_enrich_metadata.py` | 5p/0f | 5p/0f (1f in run 1) | **5p/0f** | yes |
| `tests/test_rted_conflict_detector.py` | 4p/7f | 11p/0f | **11p/0f** | yes |

All six agree test-for-test across three measurements and a 500+ commit window.

- The **reproducible after-state stands: 4 files, 4 failures + 1 error**, against a before-state of
  8 files / 16 failures + 1 error. Headline delta unchanged: **17 failing outcomes → 5**.
- `test_dic_re_enrich_metadata.py` passes a third time, confirming the d5 report's call that its
  single run-1 failure was transient and not a regression.
- The epic's only fix (`d23f8aa66`, PR #1230) still holds at the branch tip: 7 failed / 4 passed →
  **11 passed**.

Failing test identities are also unchanged — same two gate assertions, same
`test_extractors_constants`, same `TestCoTActivation::test_cot_called_when_evidence_rich`, same
`test_import_from_docgen_valid_template_type_returns_500_or_doc_id` error.

## The docmod gate: hypothesis retracted, cause isolated

### The prescribed discriminating run

```
python -m pytest tests/docmod/test_regen_quality_gate.py -p no:randomly \
    -o log_cli=true --log-cli-level=WARNING -q
→ 2 failed, 8 passed in 1.15s   —   no WARNING emitted
```

`doc_generator.py:753` logs `"doc_generator: quality_gate hook error: %s"` from its
`except Exception`. That line never appears, so **the hook does not raise and the swallow never
fires.** The d5 report's stated sub-cause is disproved.

A second disconfirmation was available without running anything:
`test_clean_regeneration_reaches_pending_review` asserts `out["quality_gate"]["blocked"] is False`
and passes. An empty `gate_report` — the report's other candidate — would have raised `KeyError`
there. The gate is called, and it populates its report.

### What actually happens

Tracing `evaluate_regeneration_quality`'s arguments in the failing test (out-of-tree pytest plugin,
nothing in the worktree touched):

```
[DBG] allowed_sources = {'c1'}
[DBG] section type = GeneratedSection
[DBG]   heading   = 'Overview'
[DBG]   abstained = True
[DBG]   content   = 'TLS 1.3 secures all endpoints.'
[DBG] section_dicts = []
[DBG] blocked = False reasons = []
[DBG] citation findings = []
```

The chain:

1. The test feeds an **uncited** section (`"TLS 1.3 secures all endpoints."`, no `[source:]` tag).
2. Confidence is derived from citations, so an uncited section lands below `_CONF_ABSTAIN`.
   `doc_generator.py:693` sets `abstained = True` and the section is excluded from `full_text`.
   The fixture's `fake_verify` returns `abstained=False`, so this flag is set by the generator, not
   by the test double.
3. `regen_quality_gate._section_dicts:69` drops every abstained section — deliberately, and asserted
   by the passing unit test `test_gate_skips_abstained_sections`.
4. The gate therefore evaluates an **empty** section list, finds no citation defect, and returns
   `blocked=False` correctly.
5. `regen_orchestrator` computes `blocked = bool(gate_report.get("blocked"))` → `False`, then
   `forced = blocked and force` → `False`. Both assertions fail, one step apart, from one cause.

### Why this matters

**`BLOCK_MISSING_CITATIONS` is unreachable through `regenerate_document`.** The condition that
would raise it — a section with no citations — is precisely the condition that makes the section
abstain first, and abstained sections are removed before the citation check runs. The two rules are
mutually exclusive by construction.

That also explains the misleading test signal the d5 report flagged: the five unit-level gate tests
pass while the integration tests fail, because the unit tests pass **plain dicts** with `abstained`
unset. Only that shape ever reaches the citation check. The unit suite cannot detect this defect.

This is TRUST-relevant — CLAUDE.md requires promote/export to be gated on citation defects — but it
is narrower than "the gate fails open". An abstained section is *excluded* from the persisted text
rather than published, so uncited prose does not silently reach the review queue by this path; what
is lost is the **blocking decision and its audit note**. A draft whose every section abstained is
persisted as ordinary `pending_review` with no record that the gate had anything to say.

The fix is a scoping decision, not a one-line patch:

- **(a)** let the gate see abstained sections and block a draft that abstained everything, or
- **(b)** have `regenerate_document` treat an all-abstained draft as a blocking outcome in its own
  right, leaving `_section_dicts` alone.

The failing tests encode (a). The code implements neither. Recommend carrying this to a follow-up
card with the option chosen explicitly — do not let a future session "fix the test".

## The other three residuals

Unchanged from the d5 report, re-confirmed here; causes are stated there and not repeated:

| file | outcome | cause (summary) |
|------|---------|-----------------|
| `tests/test_dic_techwriter.py` | 1 error | production connection leak — `api_import_from_docgen` (`blueprint.py:853-1053`) has no `conn.close()` and no `finally:` |
| `tests/genesis_auto/test_extractors.py` | 1 failed | asserts `_YIELD_RICH`, which exists in no file on `main`; `git log -S` finds it only on branches that never merged |
| `tests/test_idr_multi_source.py` | 1 failed | CoT activation threshold contract disagreement |
