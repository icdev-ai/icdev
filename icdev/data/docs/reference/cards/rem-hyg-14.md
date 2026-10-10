# An ungated test that is RED FROM BIRTH, not only one that regressed (rem-hyg-14)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/ci/born_red_survey.py                    # human table
python tools/ci/born_red_survey.py --json
python tools/ci/born_red_survey.py --run 25           # measure never-observed files now
python tools/ci/born_red_survey.py --confirm 5        # run the top N at their landing commit
```

`ungated_test_drift.py` reports TRANSITIONS -- `was == 'pass' and now 'fail'`
-- so a file whose FIRST observation is a failure takes the `was is None`
branch, seeds a 'fail' baseline and is never mentioned again. A test broken
since the day it was written is structurally invisible to the ONE reflex
watching the ungated backlog: the reflex can only see a file FALL, and a file
that was never standing cannot fall. Measured --
tests/test_proposals_ptw_blackhat_api.py was 10/20 red from the day it landed
(2026-07-07) and stayed red for SIX WEEKS. `gate_promoter` (rem-tst-06) drains
the GREEN half of the backlog weekly; this measures the RED half, so those
files get fixed or deleted instead of sitting as debt nobody can see.
FIVE STATES, and only one is the finding:
  born_red         every observation of this file has been a failure
  regressed        observed passing once -- the drift reflex's half, and
                   deliberately NOT re-reported here
  history_unknown  failing, with no recorded first verdict (pre-migration rows)
  passing          gate_promoter's half
  unobserved       NOBODY HAS EVER RUN IT. Not a clean bill of health.
`unobserved` is the state that had to exist. The reflex samples 40 files every
six hours, so a sweep of the 1,701-file backlog takes over ten days; measured
2026-08-20 only 209 had been observed AT ALL, and folding the other 1,492 into
"no findings" is the exact reassurance this tool exists to refuse.
`born_red_count` is None, NEVER 0, on a deployment that has recorded nothing.
TWO DURATIONS, NEVER MERGED. `observed_red_days` is PROVEN (seen failing then,
not seen passing since); `file_age_days` is how long the file has EXISTED,
an UPPER BOUND on how long it can have been red. `red_days_basis` states which
one the rank used: confirmed_at_birth | file_age_upper_bound | observed_only |
refuted_at_birth.
`--confirm N` runs the top N at the commit that landed them on the default
branch. `--first-parent`, on purpose: the commit that ADDED the file sits on a
feature branch whose tree lacks whatever else merged that day -- which is
exactly how the measured example was green alone and red on main -- so the
LANDING MERGE is the tree the file actually had to work in. THREE outcomes and
the middle one is its own finding: `confirmed_born_red` | `passed_at_birth`
(it worked at landing and broke later, SILENTLY -- a regression the drift
reflex missed because it never observed the pass it would have fallen from) |
`birth_unrunnable` (pytest exit 2/3/4/5 on the old tree: a statement about that
checkout's dependencies, not about the test, counted as NEITHER).
MEASURED on the live board 2026-08-20 with `--confirm 3`: 2 CONFIRMED born red
(tests/govcon/test_past_performance_suggester.py since 2026-07-25;
tests/dashboard/test_home_tile_gating.py since 2026-08-02) and 1
`passed_at_birth` (tests/airgap/test_hook_compat_git_blocklist.py) -- the third
outcome earning its place on the first real run. Re-deriving the card's own
example at a3741bb11 returns "10 failed, 10 passed": the 10/20 claim, measured.
WRITES NOTHING. The reflex stays the ONLY writer of `ungated_test_baseline`;
a survey that seeded the table would make its own evidence and the next run
could not tell a reflex observation from its own. Its `first_status` /
`ever_passed` columns (migration 20260820231102) are that evidence, backfilled
ONLY for rows whose `first_seen` equals `last_checked` -- provably a single
observation, so its current verdict IS its first. Every other row stays NULL
and reports `history_unknown`. `ever_passed` is a LATCH: a later failure never
clears it, because that is what separates a regression from a file that has
never worked.
Report only, deliberately no `--gate` (kpr-fix-03). Exit 2 = the survey could
not be produced, which is never the same as a clean survey.
