# A `merge -s ours` supersede made the branch UNREBASABLE (kpr-watch-15)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.ci.rebase_merge_survey --classify        # every rebase FAILURE, bucketed
python -m tools.ci.rebase_merge_survey --successes       # the COST side: every rebase SUCCESS
python -m tools.ci.rebase_merge_survey --replay          # each candidate lever, against a CONTROL arm
python -m pytest tests/kanban/test_rebase_merge_carrying_branch.py -q
```

`rebase_recovery.rebase_and_push` ran a default `git rebase`, which does NOT
preserve merges. A branch whose head is a `merge -s ours` supersede -- THE
DOCUMENTED RECIPE for "keep the rebased tree, discard the pre-rebase commit"
-- is FLATTENED, and the commit that supersede deliberately discarded is
REPLAYED alongside its own replacement. PROVEN on `dwr-ev-03` (PR #2179):
from 07:58Z its head was `234cea39c`, whose tree is byte-identical to its
first parent `26c59984d` while `acad8b4d1` is discarded, and
`rev-list --no-merges 39d6655e6..234cea39c` offers BOTH -- two commits where
the branch intends one. Ten `rebase_failed` rows followed and the squash that
landed carries the commit body TWICE. Against the earliest base the rebase
died on `acad8b4d1` ITSELF: a commit the branch had thrown away.
NOW: when the branch head carries a merge commit, the base is MERGED IN
instead. The additive auto-resolver, the mfx-sib-03 union rung, its verifiers
and the clean-only refusal are UNCHANGED and run on both paths; the verdict
carries `strategy` (rebase|merge) and `carried_merges`, and the audit reason
says which door ran, so no schema changed and `_rebase_attempts` still counts
what it always counted.
IT CAN NEVER MAKE A LINEAR BRANCH NON-LINEAR -- the trigger IS that the branch
already carries a merge -- so the 475 linear rows are untouched by
construction, and it is a NON-force push where the rebase path force-pushes.
`branch_carries_merge` returns `[]` for MEASURED linear and **None** for
unmeasurable, never merged: an unreadable history falls through to the
unchanged rebase, because reading it as linear is the direction that
force-pushes a flattened branch.
SURVEYED BEFORE SHIPPING, all 649 lifetime `pr_watcher.rebase_failed` rows,
each replayed against the head AND base it actually had (2026-09-09):
  62 merge_carried (9.55%) | 475 linear (73.19%) | 112 UNMEASURABLE (17.26%)
THE HEAD COMES FROM THE REMOTE-TRACKING REFLOG, NOT FROM TODAY'S HEAD, and
that is the whole reliability of the survey. Today's head over-counts (a
human's own RESCUE merge, pushed AFTER the failure, reads as the cause) AND
under-counts (a later hand rebase makes the merge unreachable): it attributes
4 rows to rmf-ui-05/08 and mfx-sib-02 that were linear at the time, and misses
mfx-sib-03's 11 entirely. Each reconstruction is VALIDATED against the commit
the row's own reason says `git rebase` could not apply -- head-based validates
7 of 52, reflog-based 536 of 536. UNMEASURABLE is its own bucket and is never
folded into `linear`: a deleted branch has no reflog and cannot be asked.
THREE LEVERS REPLAYED OVER THE 62, and the CONTROL is what makes it sound --
the unchanged plain rebase reproduces 62 of 62 recorded failures:
  plain (today)     0 integrated                  62 conflict
  --rebase-merges   3 integrated   5 already_current   54 conflict
  merge the base IN 10 integrated  5 already_current   47 conflict   <- SHIPPED
  detect and refuse  --  attempts nothing, clears nothing
`already_current` is asked identically of every arm (`merge-base
--is-ancestor`) and NEVER off stdout: `git merge` prints "Already up to date"
and `git rebase` does not, so reading stdout credits the rebase arms with 5
clears no arm performed. Those 5 are a branch that ALREADY contains its base
-- the forge's `phantom` -- and the verdict says `already_current` with
`pushed` False, because reporting it as an integration claims an act that did
not happen.
THE COST SIDE, and it is why this needed no threshold: of the 193 recorded
`pr_watcher.rebase` SUCCESSES, **0 of the 111 measurable ones were on a
merge-carrying branch** (82 unmeasurable, reported not folded in). The new
predicate refuses nothing that has ever worked -- 0.00%, against the 1.63%
this file already calls refusing routine work.
`--rebase-merges` was REJECTED ON MEASUREMENT: a third of the clear rate, it
changes the invocation for all 475 linear rows to no benefit, and it does not
address the mechanism (replaying 234cea39c with it still stops on 26c59984d
with identical files). "Detect and refuse" was rejected because merging
DOMINATES it -- it refuses everything refuse refuses and additionally clears
10 rows and diagnoses 5.
47 OF THE 62 STILL CONFLICT and they are NOT this defect: the shared-file
train on .claude/commands/start.md, base.html + its mirror, CLAUDE.md + its
bootstrap copy and boundary_canvas/blueprint.py (rmf-ui-03/06/07/09/10/11 are
37 of them, one epic appending to six files). That is kpr-watch-14's and
mfx-sib-02's, and no rebase strategy reaches it. NOT CHANGED, and named:
`max_rebase_attempts_per_task` and the per-base-era budget; the
`pr_watcher.rebase`/`rebase_failed` action names (so the budget's ledger and
this survey both keep their meaning); `summarize_recovery`, its threshold and
its window. Survey:
docs/audits/kpr-watch-15-merge-carrying-branch-rebase-survey.md
