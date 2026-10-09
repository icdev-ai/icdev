# Is `E2E (Playwright)` reliable enough to be REQUIRED? Surveyed; answer is NOT YET (crx-test-06)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/ci/e2e_flake_survey.py --json
python tools/ci/e2e_flake_survey.py --limit 100
python tools/ci/e2e_flake_survey.py --from-json runs.json       # offline replay
```

E2E showed 25/25 green and that number could not support a promotion. Until
crx-test-05 the job declared `needs: [test]`, so it ONLY ever ran on branches
whose unit suite had ALREADY passed and GitHub SKIPPED it on the rest: the
sample was drawn from the healthiest branches in the population, and the
unhealthy ones were absent from the DENOMINATOR -- the one place an absence is
invisible. Measured 2026-08-19 over 40 runs: 27 success, 0 failure, and 10
SKIPPED. Counting those 10 as "did not fail" is what turned selection bias
into evidence of reliability.
The population is split STRUCTURALLY, never by a cutoff date: a post-unblock
run carries an `E2E Shard k of N` job, which is a fact about the run itself and
cannot drift when somebody reruns an old branch.
FIVE outcomes, never merged: success | failure (the only flake signal) |
cancelled (an infrastructure event, a verdict about neither) | skipped (NEVER
RAN) | in_progress. `flake_rate` is None -- NEVER 0.0 -- when nothing was
exercised, because "measured clean" and "never measured" justify opposite
decisions. The biased pre-unblock block is REPORTED but its state is forced to
`biased_ineligible`, so it cannot print a verdict a reader would quote.
Promotion needs BOTH >=20 exercised post-unblock runs AND zero failures. A
failure does not stop being a failure because more runs are averaged over it --
never raise `min_runs` to clear a `blocked` verdict, and never lower it to
reach `supported` sooner. Claims: args/e2e_promotion.yaml.
REPORT ONLY, deliberately no --gate (kpr-fix-03: a survey with a --gate earns
itself a `|| true`). Exit 2 = the survey could not be produced, which is never
the same as a clean survey.
Promotion also needs `E2E (Playwright)` added to the hardcoded required-check
names in tools/dashboard/static/js/task_pipeline.js and
tools/kanban/seed_ahx_arr_clx.py, or the board's own "CI is green" predicate
disagrees with GitHub's.
