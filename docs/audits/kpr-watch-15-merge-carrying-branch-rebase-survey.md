# CUI // SP-CTI

# kpr-watch-15 — a `merge -s ours` supersede makes a branch unrebasable

**Measured 2026-09-09** on the live PG board (`icdev`) and the live git remote,
from `C:\AI\ICDev`. Every number below is re-derivable with
`python -m tools.ci.rebase_merge_survey`.

---

## 1. The defect

`pr_watcher._maybe_rebase` calls `rebase_recovery.rebase_and_push`, which runs a
default `git rebase origin/<base>`. **A default rebase does not preserve
merges.** A branch whose head is a `merge -s ours` supersede — the documented
recipe for "keep the rebased tree, discard the pre-rebase commit"
(memory `supersede-a-rejected-branch-with-merge-s-ours`) — is therefore
FLATTENED, and the commit the supersede deliberately discarded is REPLAYED
alongside its own replacement.

Proven on `dwr-ev-03` (PR #2179). Its remote-tracking reflog is the whole story
in three lines:

```
$ git reflog show --date=iso refs/remotes/origin/kanban/dwr-ev-03
d8d574a0d @{2026-09-08 20:04:14Z}: update by push   <- the human's rescue merge
234cea39c @{2026-09-08 07:58:04Z}: update by push   <- the `merge -s ours` supersede
acad8b4d1 @{2026-09-08 07:45:21Z}: update by push   <- the worker's commit
```

From 07:58Z the head was `234cea39c`, a two-parent merge whose tree is
byte-identical to its first parent `26c59984d` (verified: both trees are
`07350a911`) and whose second parent `acad8b4d1` is discarded. What a default
rebase is offered from that head:

```
$ git rev-list --no-merges --reverse 39d6655e6..234cea39c
acad8b4d1  feat(dic): redraft with my comments ... (dwr-ev-03)
26c59984d  feat(dic): redraft with my comments ... (dwr-ev-03)
```

**Two commits, where the branch intends one.** Replaying that pair against the
four bases the watcher actually saw shows both halves of the mechanism:

| base at the time | plain rebase stops on | conflicted files |
|---|---|---|
| `56780545b` | **`acad8b4d1`** — the DISCARDED commit | 8 |
| `583875149` | `26c59984d` | CLAUDE.md + bootstrap |
| `72765eec1` | `26c59984d` | CLAUDE.md + bootstrap |
| `39d6655e6` | `26c59984d` | CLAUDE.md + bootstrap + doc_detail.html ×2 |

The first row is the defect in its purest form: the rebase died on a commit the
branch had deliberately thrown away. The other three are the first copy applying
and the near-identical second colliding — which is why the squash that finally
landed (`238d58375`) carries the commit body **twice**.

---

## 2. Survey — all 649 lifetime `pr_watcher.rebase_failed` rows

`python -m tools.ci.rebase_merge_survey --classify`

| bucket | rows | share |
|---|---|---|
| **merge_carried** — the branch carried a merge commit at that instant | **62** | **9.55%** |
| linear | 475 | 73.19% |
| UNMEASURABLE — no reflog (110) / no push before the row (2) | 112 | 17.26% |

**Reconstruction: 536 of 536 measurable rows validated.** The head each row saw
comes from the remote-tracking reflog (`refs/remotes/origin/kanban/<id>`, newest
entry at or before the row's stamp), and each is checked against the row's own
audit reason: the commit `git rebase` said it "could not apply" must be
reachable from the reconstructed head. Every one is.

`UNMEASURABLE` is its own bucket and is never folded into `linear`: a deleted
branch has no reflog and cannot be asked, and reading it as clean would
understate the finding.

14 tasks: `rmf-ui-11` 11, `mfx-sib-03` 11, `dwr-ev-03` 10, `rmf-ui-03` 8,
`rmf-ui-10` 4, `exa-bench-05` 3, `dwr-anchor-04` 3, `hcx-live-02` 2,
`rmf-ui-07` 2, `rmf-ui-06` 2, `rmf-ui-09` 2, `dwr-fid-03` 2,
`cpmp-ec9ee676bf` 1, `dwr-ev-01` 1.

### 2a. Why the card's own 52 / 484 / 113 is superseded, and both are quoted

The card measured merge-carriage against each branch's head **today**. That
method is wrong in **both** directions, and this survey shows both:

* it **over-counts** — a human's own RESCUE merge, pushed after the failure, is
  a merge commit dated before *later* rows. `rmf-ui-05`, `rmf-ui-08` and
  `mfx-sib-02` are attributed 4 rows between them that were **linear** at the
  time;
* it **under-counts** — a later hand rebase makes the merge unreachable from
  today's head. `mfx-sib-03` (11 rows), `exa-bench-05` (3), `dwr-anchor-04` (3),
  `dwr-fid-03` (2) and `cpmp-ec9ee676bf` (1) are invisible to it entirely.

The head-based reading also cannot be validated: replaying it, the commit named
in the row's reason is reachable from the reconstructed head for only **7 of
52** rows. The reflog reading validates **536 of 536**. Both readings are
recorded here; only the validated one is used.

**8.0% (card) and 9.55% (this survey) are both minorities and neither is the
majority cause.** The ordinary shared-file conflict train is, and that is
`kpr-watch-14`'s. Nothing here changes it.

---

## 3. Fire-rate survey — what each candidate lever would have done

`python -m tools.ci.rebase_merge_survey --replay`

Each of the 62 merge-carrying rows is replayed in a scratch worktree against the
branch head **and the base** it actually had (34 distinct pairs). A CONTROL arm
runs beside the candidates.

| arm | integrated | already_current | conflict |
|---|---|---|---|
| **plain rebase (control, today)** | 0 | 0 | **62** |
| `--rebase-merges` (option A) | **3** (4.8%) | 5 | 54 |
| **merge the base IN (option C)** | **10** (16.1%) | 5 | 47 |
| detect and refuse (option B) | — | — | not attempted (62) |

**The control is faithful: the unchanged plain rebase reproduces 62 of 62
recorded failures.** Without that the comparison would be worthless.

`already_current` is asked identically of every arm — `git merge-base
--is-ancestor <base> <head>`, never off stdout, because `git merge` prints
"Already up to date" and `git rebase` does not, and reading it off stdout
credits the rebase arms with 5 clears no arm performed. Those 5 rows are a
branch that ALREADY contained its base: the forge's `phantom`, not a clear.

Per task. `already_current` is the SAME rows for both arms, by construction —
it is a property of the head/base pair, not of the arm:

| task | rows | A integrated | C integrated | already_current (both) |
|---|---|---|---|---|
| `rmf-ui-11` | 11 | 0 | 0 | 0 |
| `mfx-sib-03` | 11 | 3 | **9** | 2 |
| `dwr-ev-03` | 10 | 0 | **1** | 0 |
| `rmf-ui-03` | 8 | 0 | 0 | 0 |
| `rmf-ui-10` | 4 | 0 | 0 | 0 |
| `dwr-anchor-04` | 3 | 0 | 0 | 0 |
| `exa-bench-05` | 3 | 0 | 0 | 1 |
| `dwr-fid-03` | 2 | 0 | 0 | 0 |
| `hcx-live-02` | 2 | 0 | 0 | 0 |
| `rmf-ui-06` | 2 | 0 | 0 | 0 |
| `rmf-ui-07` | 2 | 0 | 0 | 0 |
| `rmf-ui-09` | 2 | 0 | 0 | 0 |
| `cpmp-ec9ee676bf` | 1 | 0 | 0 | 1 |
| `dwr-ev-01` | 1 | 0 | 0 | 1 |
| **total** | **62** | **3** | **10** | **5** |

`mfx-sib-03` alone accounts for 9 of C's 10, and `dwr-ev-03` — the case the card
was written from — for the tenth. `--replay --json` carries `rows_detail`, one
entry per row with its stamp, PR, head, base and every arm's outcome, because
"what would each lever have done" is a question about each recorded failure and
a per-task count cannot answer it.

### The cost side — the 193 recorded rebase SUCCESSES

`python -m tools.ci.rebase_merge_survey --successes`

| bucket | rows |
|---|---|
| merge_carried | **0** |
| linear | 111 |
| UNMEASURABLE (branch deleted, no reflog) | 82 |

**No rebase that has ever SUCCEEDED was on a merge-carrying branch.** So a lever
keyed on merge-carriage refuses nothing that works, over the 111 rows that can
be measured — the 82 that cannot are reported, not folded in. This is the
measurement the choice turns on: the fire rate of the new predicate against
routine work is **0.00% of 111**, far below the 1.63% CLAUDE.md already calls
refusing routine work.

The 82 are a **censored** sample and the censoring is one-directional: a
successful rebase FLATTENS the branch, so had any success been on a
merge-carrying branch its merge would be gone from the branch today. The reflog
is what makes the other 111 answerable at all, and it answers 0.

---

## 4. The decision, and why the other two were rejected

**Chosen: option C — when the branch head carries a merge commit, integrate by
merging `origin/<base>` IN rather than rebasing.**

* It clears **more than three times** what `--rebase-merges` clears (10 vs 3),
  measured, on the same 62 rows against the same bases.
* It is **exactly the repair the human performs** — `dwr-ev-03`'s rescue at
  20:04Z and `dwr-ev-01`'s `ce38e39fb` are both a `git merge origin/main`.
* It **preserves the supersede's intent** by construction: the `-s ours` head
  becomes a parent of the new merge, so the discarded commit stays discarded and
  its tree is never resurrected.
* Its blast radius is exactly the trigger condition. It can never make a linear
  branch non-linear (the branch already carried a merge), so **none of the 475
  linear rows changes**, and it is a NON-force push where the rebase path
  force-pushes.

**Option A, `--rebase-merges`, rejected on measurement.** A third of C's clear
rate, and it changes the git invocation for every one of the 475 linear rows to
no benefit. It also does not address the mechanism: replaying `234cea39c` with
`--rebase-merges` still stops on `26c59984d` with identical conflicted files,
because the discarded side is replayed as its own line before the merge is
re-created.

**Option B, detect and refuse, rejected because C strictly dominates it.** B
refuses everything C refuses and additionally strands the 10 rows C integrates
and the 5 it diagnoses. Its one merit — naming the real reason in the ledger —
C keeps: a `merge` attempt's audit reason reads
`merged origin/main into the branch and pushed (N commit(s))` or
`merge of origin/main into the branch hit conflicts: …`, so the ledger says
which door ran without a schema change.

### What did NOT change

* `max_rebase_attempts_per_task` and the per-base-era budget — untouched.
* The additive auto-resolver and the mfx-sib-03 union rung run on the merge path
  exactly as on the rebase path, with the same verification and the same
  clean-only refusal.
* The 475 linear rows, `_rebase_attempts`, and the `pr_watcher.rebase` /
  `rebase_failed` action names — an attempt is still an attempt, so the ledger
  the budget counts keeps its meaning and this survey stays re-derivable.
* `summarize_recovery`, its threshold and its window — this is a `pr_watcher`
  rebase defect, not a detector one.

### Residual, named rather than implied

* **47 of the 62 still conflict**, and they are the shared-file train:
  `.claude/commands/start.md`, `tools/dashboard/templates/base.html` and its
  `icdev/` mirror, `CLAUDE.md` and its bootstrap copy, `boundary_canvas/
  blueprint.py`. `rmf-ui-03/06/07/09/10/11` (37 rows) are one epic appending to
  the same six files. That is `kpr-watch-14`'s and `mfx-sib-02`'s territory and
  no rebase strategy reaches it.
* **The 112 unmeasurable failure rows and 82 unmeasurable successes** cannot be
  recovered: their branches are deleted and their reflogs pruned. They are
  reported, never estimated.
* The merge path leaves a merge commit on the branch. Landing is unaffected —
  `land.py` and the watcher merge `--merge`, the Actions workflow squashes — but
  a branch integrated this way will take the merge path again next time, by
  design and self-consistently.

---

## 5. Re-deriving every number

```bash
python -m tools.ci.rebase_merge_survey --classify          # the 62 / 475 / 112
python -m tools.ci.rebase_merge_survey --successes         # the 0 of 111 cost
python -m tools.ci.rebase_merge_survey --replay            # the three arms (minutes)
python -m tools.ci.rebase_merge_survey --replay --json
python -m pytest tests/kanban/test_rebase_merge_carrying_branch.py -q
```

Run it from the checkout `pr_watcher` runs in: the remote-tracking reflogs are
that repository's, and a fresh clone reports every row `unmeasurable_no_reflog`
rather than a clean zero.
