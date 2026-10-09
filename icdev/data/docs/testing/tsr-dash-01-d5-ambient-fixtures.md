# TSR DASH — ambient-DB-dependent fixtures (tsr-dash-01-d5)

Produced 2026-08-02 on branch `kanban/tsr-dash-01-d5`, a worktree off `origin/main` at
`74d3c5f73`, seeded per the tsr-dash-01-d1 recipe (`ICDEV_STORAGE_BACKEND=sqlite`,
543 tables, 16 `studio_*`).

## 0. Correction to the task premise

The card asks to fix "triaged category (a) failures". **There is no category (a) list.**
`docs/testing/tsr-dash-01-d2-triage.md` §3 says so in its own words: both arms were killed at
~17% of the slice and "the a/b split is not in this document". What d2 did deliver is the
five-shard decomposition, so this card starts from **shard 1** (26 files) and classifies by
mechanism rather than by a table that was never produced.

That turns out to matter, because the biggest non-hermetic group in shard 1 fails in *both*
arms — it would have been filed as category (b) "real defect" by the two-arm test and the
actual root cause (a fixture that no longer intercepts the code path) missed.

## 1. Baseline — shard 1, clean worktree

```bash
export ICDEV_STORAGE_BACKEND=sqlite
export PYTHONPATH=<worktree root>
python -m pytest $(cat docs/testing/tsr-dash-01-d2-shard1.txt) -q -rfE --timeout=60 \
    --continue-on-collection-errors -p no:cacheprovider
```

| | before | after |
|---|---|---|
| failed | **9** | **1** |
| passed | 342 | 351 |
| wall clock | 81 s | 49 s |

Net test count rises by 2 (33 → 35 in the file touched): one assertion was moved to the
function that still implements the rule, and two new cases were added — see §2.3.

## 2. `tests/dashboard/test_canvas_aggregator.py` — 8 failures, one root cause

### 2.1 The fixture stopped intercepting the code path

The file injects in-memory SQLite databases by patching
`canvas_aggregator._get_canvas_conn`. Three of the four public functions still read canvases
through it. The fourth does not: **cnr-cc-02 re-pointed `get_canvas_compliance_summary()` at
`tools/canvas_compliance/posture.py::compute_canvas_posture`**, which opens each canvas
through `posture._open_canvas_connection()` and the main DB through
`storage.get_connection()`. Neither seam was patched, so all seven
`TestGetCanvasComplianceSummary` tests were reading whatever `data/*.db` the checkout carried.

Measured directly, same commit, `compute_canvas_posture` only differing by `data/`:

| arm | rows | detail |
|---|---|---|
| clean worktree (this branch) | **9** | all scores 0 |
| populated (`tsr-dash-01-d2-pop`, full `data/` copy) | **11** | Security 97.7, Pipeline 69.0, Infra 93.8, Data 69.1, Boundary 62.1, Observability 88.2, Agentic AI 79.1, AI/ML 83.0, QDC 45.3, Migration 100.0, Zero Trust 100.0 |

The test asserts 7. Neither environment yields 7, which is why a two-arm diff would have
labelled this "(b) real": *both* arms fail. They fail for different reasons, and the count is a
pure function of how many canvas DBs happen to be openable — that is category (a)'s mechanism
with category (b)'s signature. **A test can be ambient-dependent and still fail everywhere.**

### 2.2 Fix

Added `_patch_posture()`, which patches both seams — `posture._open_canvas_connection`
(routing canvas *display name* → the same in-memory connections, since posture keys canvases by
name while the aggregator keys them by db filename) and `agg.get_connection` (an empty
in-memory DB, so the GovLift STIG rollup and the ZIG "Zero Trust" row are skipped for want of
their tables). Posture's other four canvases — Agentic AI, AI/ML, QDC, Migration — resolve to
`None` and are skipped, which is what pins the summary at a deterministic seven rows.

Two pieces of genuinely missing fixture data had to be supplied for posture to score anything:

- **`design_id`.** Posture scores JSON canvases with a latest-per-design correlated subquery
  (`WHERE created_at = (SELECT MAX(created_at) … WHERE a2.design_id = a1.design_id)`). `NULL`
  never equals itself in SQL, so an assessment row without a `design_id` is invisible to the
  aggregate and the canvas silently scores 0 — that is why
  `test_json_canvas_score_grade_and_counts` saw `0.0` where it wanted `75.0`. The builder now
  defaults one per row.
- **`cat1_findings` / `cat2_findings` / `cat3_findings`.** Boundary's open-finding count comes
  from these columns, not from `findings_json`. Without them *both* the primary query and its
  fallback raise, the outer `except Exception: pass` swallows it, and the whole Boundary canvas
  vanishes from the result rather than reporting an error.

**Verification that the ambient dependence is gone:** with all eleven `data/*canvas*.db` files
moved aside, the file still passes 35/35 (1.85 s). Before the change the summary tests read
those files directly.

### 2.3 Contract changes asserted, not papered over

cnr-cc-02 dropped behaviour the old tests asserted. Rather than delete the assertions, they are
re-pinned to what the code now does:

| old assertion | now |
|---|---|
| `grade` from the assessment row | always `""` — posture is a numeric view; asserted explicitly |
| `last_assessed` from the assessment row | always `""`; asserted explicitly |
| missing canvas → row with `available=False` | canvas is **omitted**; `test_missing_db_canvas_is_omitted` asserts absence + a row count of 5 |
| empty canvas → `latest_score is None` | scores 100.0 (Security/Network/Pipeline) or 0.0 (Infra/Data/Boundary/Observability); the exact split is pinned so a silent rule change stays visible |
| Network open/closed from findings | from `SUM(passed)`/`SUM(failed)`; the findings fallback path gets its own new test |
| accepted_risk / false_positive count as closed | moved to `TestGetCanvasFindingCounts`, the only place that still implements the rule |

`test_result_is_cached_on_second_call` was **vacuously green**: it counted calls to
`_get_canvas_conn`, which this function no longer makes, so `0 == 0` passed while asserting
nothing. It now counts `compute_canvas_posture` calls.

### 2.4 A second, unrelated fixture bug in the same file

`test_counts_events_across_canvases` was failing `0 == 3`. `get_canvas_activity_trend` binds its
cutoff with a PostgreSQL `%s` placeholder; the fixture handed it a bare `sqlite3` connection,
which raises `near "%": syntax error` inside the aggregator's best-effort `except` — the test
was asserting against a no-op it had caused itself. `_make_conn()` now returns
`tests._sql_compat.translating(...)`, the same translation the runtime applies.
`unclosable=True` is required because `compute_canvas_posture` closes every canvas connection in
a `finally` block and an in-memory database dies with its connection.

## 3. Not fixed — `tests/slides/test_blueprint.py` (1 failure), a production defect

`TestTemplateFillRoutes::test_fill_end_to_end_creates_deck` fails on

```
sqlite3.IntegrityError: CHECK constraint failed: deck_type IN ('executive_overview',
'canvas_deep_dive','govcon_proposal','compliance_briefing','weekly_status','custom',
'general_presentation','pitch_deck')
```

This is not a fixture gap. `tools/slides/blueprint.py:797` persists `deck_type="template_fill"`,
and `"template_fill"` is **not in `tools/slides/constants.py::DECK_TYPES`**, from which
`CHECK_DECK_TYPE` and therefore `tools/slides/db/init_db.py` derive. The template-fill route
cannot persist a deck against a correctly-created schema; it only appears to work on a database
whose `slides_decks` predates the constraint — which is exactly why a clean checkout surfaces it
and the shared checkout does not.

Left for a separate card because the fix is a production/schema change, outside this card's
"at most 2-3 test files" scope, and it needs its own DDL-parity verification:

1. add `"template_fill"` to `DECK_TYPES` in **both** `tools/slides/constants.py` and
   `icdev/tools/slides/constants.py` (new DBs then pick it up via `CHECK_DECK_TYPE`);
2. a slides migration expanding the hardcoded list in
   `tools/slides/db/migrations/002_rich_slides.sql:26` for existing databases — note that DDL
   hardcodes the vocabulary instead of deriving it, which is what let the two drift.

**Do not "fix" this by changing the test's expected `deck_type`.** The assertion is correct;
the schema is wrong.

## 4. Shard 5 — not measured

Shard 5 (43 files) was launched and hit the 600 s cap inside `tests/test_intake_api.py`, whose
`chat_app` fixture shells out to a DB-init subprocess that does not return on a cold worktree.
Shards 2–5 remain unmeasured on a clean checkout.
