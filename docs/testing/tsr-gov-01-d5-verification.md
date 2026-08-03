# TSR GOV — full-slice before/after verification (tsr-gov-01-d5)

Measurement task. **No test or source file was modified by this card**; the only artifact is
this document. Produced 2026-08-02.

- **before** = `629820ae1` — the base `tsr-gov-01-d1` triaged against, so its 165 is directly comparable
- **after**  = `b680c4077` — `origin/main` at time of measurement

Both arms are clean detached worktrees with a freshly seeded SQLite DB. Neither reuses the shared
checkout.

---

## Headline

| | files | tests | passed | failed |
|---|---:|---:|---:|---:|
| **before** `629820ae1` | 70 | 2,280 | 2,115 | **165** |
| **after** `b680c4077` | 70 | 2,280 | 2,127 | **153** |
| delta | 0 | 0 | +12 | **−12** |

**12 failures cleared, 0 new failures.** The before-only and after-only sets were compared
test-for-test: 12 entries are before-exclusive, and **the after-exclusive set is empty**. Nothing
regressed, and no failure merely moved from one test to another.

The before arm reproduces d1's measurement exactly — 165 failed / 2,115 passed, the same number
d1 recorded on the same commit. The baseline is therefore independently confirmed, not inherited.

---

## The slice is identical in both arms

The 70-file list was recomputed independently at each commit with the epic's import-keyed rule:

```bash
grep -rlE "(from|import) +(icdev\.)?tools\.(govcon|proposal_genesis|cpmp|win_loss|voc|pulse)\b" \
  --include="*.py" tests/
```

`diff` over the two sorted lists is empty — 70 files in both. No file entered or left the slice
between the two commits, so the count delta is a real pass/fail change and not a scope change.

---

## Attribution — only half the delta belongs to this epic

The −12 does **not** all come from TSR GOV. Attributed by bisecting the failing tests against the
commits that touch them:

| tests cleared | commit | epic |
|---:|---|---|
| 4 | `5bfeff616` — seed VOC tables so voc_engine writes land (`tsr-gov-01-d4`) | **TSR GOV** |
| 2 | `524ff95ff` — add win_loss + creative_feature_gaps to `MINIMAL_ICDEV_SCHEMA` (`tsr-gov-01-d2`) | **TSR GOV** |
| 6 | `b19067d89` — reconcile `audit_trail` event types so govcon writes land (`swp-audit-01`) | SWP |
| **12** | | |

The 6 `TestAuditTrail` failures in `test_procurement_quote_compare.py` and
`test_procurement_vehicles.py` are d1's **P3** — the conftest `audit_trail`/production schema
mismatch. d1 recommended scoping it as a dedicated task rather than folding it into a govcon fix;
that is what happened, under `swp-audit-01`, not under this epic. Counting those 6 as TSR GOV
output would overstate the epic by 100%.

**TSR GOV's own in-slice contribution is 6 failures.**

---

## d3's 96 fixes are real but sit outside the slice

`tsr-gov-01-d3` reports repairing 96 cpmp failures. That improvement does **not** appear in the
table above, which reads as a contradiction with d1's finding that "all 20 `test_gcpl_*` files and
`test_cpmp_portfolio_smoke.py` are green."

Both are correct; they measured different populations. All 7 files d3 touched are **outside the
70-file import-keyed slice** — verified by testing each against the slice list. They exercise cpmp
by rendering Jinja templates or by patching `sqlite3` directly, and never
`import tools.cpmp`, so the epic's import-keyed selection rule cannot reach them. The slice
contains 8 other `gcpl`/`cpmp` files, and those 8 were green in both arms, exactly as d1 said.

Measured directly on d3's 7 files, same two arms:

| | tests | passed | failed |
|---|---:|---:|---:|
| **before** `629820ae1` | 184 | 88 | **96** |
| **after** `b680c4077` | 185 | 185 | **0** |

**96 → 0, confirmed.** d3's headline number reproduces exactly. (185 vs 184 because d3 added a
test.)

### Generalisable finding

> An import-keyed slice under-counts a subsystem whose tests reach it through a template render
> or a patched DB handle rather than an import.

This is the same shape as the epic's known `tools/nav` problem (a filename-keyed subsystem an
import scan finds none of), arriving from the opposite direction. Any epic quoting a
"subsystem total" from the import grep alone is quoting a lower bound. **Combined, the GOV epic
cleared 102 failures — 6 in-slice, 96 out-of-slice.**

---

## Ruff

```
ruff check <70 slice files> tests/conftest.py tools/db/init_icdev_db.py \
           icdev/tools/db/init_icdev_db.py tests/test_init_icdev_db.py
All checks passed!
```

Clean on all 74 files — the 70-file slice plus every source file the GOV epic touched.

**Run against the before arm as well, and it was already clean there.** No lint cleanup was
required or performed by this card; the epic introduced no lint regressions. Reporting this as a
cleanup would claim work that did not need doing.

---

## The remaining 153, named

Every remaining failure is accounted for. None is new, and none is a product bug in govcon
business logic.

| file | failures | d1 cause | why it is still open |
|---|---:|---|---|
| `tests/test_ski_roles_lifecycle.py` | 33 | P5 | ~68 `pm-*` / `addyosmani-*` `SKILL.md` files the role YAML and tests both reference were never committed, plus missing `listen_topics`. A content/decision gap — it cannot be closed by editing tests, and deleting the assertions is the failure mode the project card warns against. |
| `tests/test_govcon_capabilities.py` | 33 | P2 | Fixture stubs the RBAC decorator but not the later ABAC layer, so requests still return `ABAC_DENIED`. Needs an ABAC policy/context in the fixture — a different fix from P1, deliberately not batched with it. |
| `tests/test_govcon_auto_compliance_api.py` | 22 | P1 | Bare-Flask fixture with no session. |
| `tests/test_proposals_detail_extract_requirements.py` | 19 | P1 | Same fixture shape. |
| `tests/test_proposals_detail_map_capabilities.py` | 18 | P1 | Same fixture shape. |
| `tests/test_govcon_bid_recommendation_api.py` | 15 | P1 | Same fixture shape. |
| `tests/test_proposals_ptw_blackhat_api.py` | 10 | P1 | Same fixture shape. |
| `tests/test_pma_credential_reflex.py` | 3 | — | The one item d1 could not attribute to P1–P5; `result["success"]` is `False` from the reflex's own return value. Still needs its own short investigation. |
| **total** | **153** | | |

P1 accounts for 84 of the 153 across five files that share one fixture shape — a bare app with the
blueprint registered and no authenticated session. d1 verified by `git show` at two commits that
the routes gained their decorator *after* these tests were written: **the tests are stale and the
product is correct.** They currently assert that a protected endpoint is reachable without
credentials, so "fixing" them by relaxing the product would invert the intent. Fix the fixtures.

None of P1, P2, or P5 was in this card's scope, which was measurement.

---

## Method

Both arms, identical invocation:

```bash
git worktree add --detach <path> <commit>
PYTHONPATH=<path> ICDEV_STORAGE_BACKEND=sqlite python tools/db/init_icdev_db.py
PYTHONPATH=<path> ICDEV_STORAGE_BACKEND=sqlite \
  python -m pytest $(cat slice.txt) -p no:randomly --timeout=300 -q --tb=line -rA
```

before 881.9 s (525 tables seeded); after 890.0 s (527 tables — the +2 are `voc_documents` and
`voc_job_statements`, added by d4). Both exited cleanly: no collection errors, no timeouts, no
module-scope aborts, and all 2,280 collected tests reported an outcome in both arms — which is
what licenses the set comparison.

### Two environment hazards that would have silently corrupted this measurement

Both were present in the ambient shell and had to be overridden per-arm:

- **`PYTHONPATH=C:\AI\ICDev`** — points at the *shared checkout*. Left alone, both arms would have
  imported the shared tree's `tools/` and `tests/conftest.py` regardless of which commit was
  checked out, and the two arms would have returned the same number for the wrong reason.
- **`ICDEV_STORAGE_BACKEND=postgresql`** (with `ICDEV_DATABASE_URL` and `ICDEV_PG_NO_FALLBACK=1`) —
  `init_icdev_db.py` would have seeded Postgres, not the worktree's SQLite file, leaving both arms
  running against one shared database.

`-rf` was deliberately not used: it hides ERROR outcomes, and a collection error silently dropping
a file would look like a reduced failure count.

### Confidence and limits

- The −12 and the empty after-exclusive set are exact, from a test-for-test set comparison of
  2,280 outcomes per arm.
- Attribution of the 12 to three commits is by inspection of which commit touches the failing
  tests' fixtures, corroborated by the seeded-table delta (`voc_*` present only in the after arm).
- **`after` is `origin/main`, not "before plus the GOV commits."** Main absorbed unrelated work
  between the two commits, which is exactly how the 6 `swp-audit-01` fixes entered the delta. The
  attribution table above is what separates epic output from ambient main drift; the raw −12 does
  not.
- Per d1, this slice has zero ambient-DB-dependent tests, so no populated-checkout arm was run.
  That conclusion was re-confirmed here in passing: the before arm reproduced 165 on a freshly
  seeded DB, matching d1's populated-checkout number.
