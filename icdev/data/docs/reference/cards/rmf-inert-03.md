# A reflex that is GREEN while it can reach 3 of 11 subjects (rmf-inert-03)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.genesis.reflexes.canvas_reassess --coverage        # covered / uncovered BY NAME; touches no database
python -m tools.genesis.reflexes.canvas_reassess --dry-run         # the sweep against the live canvases; WRITES NOTHING
python -m tools.genesis.reflexes.canvas_reassess --starvation      # designs skipped over budget on EVERY recorded run
python tools/genesis/daemon.py --reflex canvas_reassess --json     # one real cycle, through the daemon
```

MEASURED 2026-09-07. `genesis_reflex_state` for canvas_reassess: 17 runs, 17
successes, 0 failures, last_metric_value 25.0 -- perfect health -- while the
Compliance Posture widget carried SIX canvases 51-89 days stale. BOTH NUMBERS
WERE RIGHT: the reflex-level liveness (reflex-level-liveness-does-not-prove-
act-level-liveness) was green while the ACT reached three of the widget's
eleven canvases. `_INSERTS` covered observability/boundary/infra; Security was
excluded by a code comment no reader of the widget could see; Network,
Pipeline, Data, Agentic AI, AI/ML, QDC and Migration were not in the registry
at all, so NOTHING could ever refresh them. The live ages matched exactly.
THREE DEFECTS, and the order they were fixed in is the order the card demanded:
 1. THE REFLEX REPORTS ITS OWN COVERAGE, against the SURFACE's list
    (`posture.surface_rows()`, never a copy): `coverage.covered` and
    `coverage.uncovered` BY NAME, each uncovered row carrying the reason it is
    not written (`canvas_reassess.UNCOVERED`). A widget row with NO decision is
    `undecided`, an ERROR, and a failed run -- the silence the card refuses.
    This shipped first and stands on its own.
 2. THE REPORT WAS NEVER PERSISTED. `daemon.run_reflex_impl` records
    `result["details"]` and nothing else, and this reflex never set that key, so
    all 17 runs recorded `{}` -- `skipped_over_budget`, `by_canvas`, `errors`,
    every field the module "reported", went nowhere. It now rides under
    `details` (the claim_verifier_reflex idiom). `--starvation` reads it back
    and is UNMEASURABLE over the pre-fix rows, never "nothing starved".
 3. THE BUDGET WAS SATURATED BY ORDER, NOT SIZE. Infra had grown 84 -> 154
    designs and 8 had NEVER been assessed after 17 saturated runs: each daily
    cohort of 25 re-stales together a week later and, under `ORDER BY d.id` per
    canvas, reaches the budget ahead of a never-assessed design whose id sorts
    after it. The budget is now spent OLDEST-FIRST ACROSS canvases
    (`(newest IS NULL) DESC, newest ASC` -- identical on PG and SQLite, where a
    bare ASC disagrees about NULLs). `DEFAULT_MAX_PER_RUN` stays 25 and is
    pinned by test. Live dry run after the change: all 8 never-assessed Infra
    designs and all 6 Data designs inside the budget; 3 eight-day re-stalers
    deferred BY NAME to the next run.
WRITABLE, PER CANVAS, AND WHY NOT -- every verdict is in `UNCOVERED`:
  Data        ADDED. Six columns, the same the canvas's own route writes; the
              engine spells its score `risk_score`. 6 designs, all from 2026-06-09.
  Security    stays out: scored from risk_score/posture_grade over a wider column
              set, design-id engine. Excluded since rem-hyg-11, now VISIBLE.
  Network /   no design/assessment pair: one row per CHECK, no writer column, and
  Pipeline    the posture SUMs passed/failed over EVERY row -- a scheduled row
              joins the denominator forever instead of replacing a stale one.
  Agentic AI  aadc_assessments has NO assessment_type column: a scheduled row is
              indistinguishable from a review. The event-driven aadc_compliance
              reflex already writes it.
  AI/ML       no assessment_type either (framework_id says WHAT, not who), and
              run_assessment persists its own untagged row.
  QDC         the newest row also carries uqs_score, derived by the ROUTE from
              qdc_gate_results and rendered off the newest row; a scheduled row
              would show UQS 0.0. 153 designs, 148 never assessed -- named, not
              fabricated.
  Migration   scored from assessment_type='validation' rows ONLY, so a
              scheduled row moves the AGE of a score it cannot move -- manufactured
              freshness by construction. 0 designs.
  GovLift / Zero Trust / AI-ify   not canvases; no design/engine pair to re-run.
DO NOT MANUFACTURE FRESHNESS. A scheduled re-derivation over the same inputs is a
newer timestamp on the same evidence, so the SURFACE now says who wrote the newest
row: `last_assessed_source` (scheduled | canvas | None) and `last_reviewed` (the
newest NON-scheduled row) beside `last_assessed`, from ONE spelling of the type
(`posture.SCHEDULED_ASSESSMENT_TYPE`, which the reflex imports). None is never
filled in from `last_assessed` -- a table with no writer column would then report
every refresh as a review. The widget marks a scheduled row and titles it with the
last review's age. FOUND ON THE WAY: `_ASSESSED_AT` named `created_at` for
nc_/pc_compliance_checks, columns those tables have never had (DDL and the live
catalogue agree), so Network and Pipeline rendered a score with NO age -- which on
this widget reads as fresh. Now `ran_at`.
