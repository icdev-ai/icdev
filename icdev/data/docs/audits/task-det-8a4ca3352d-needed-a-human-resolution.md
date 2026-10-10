<!-- CUI // SP-CTI -->
# task-det-8a4ca3352d — `needed_a_human` for rmf-ui-08: the escalation was CORRECT, and a human answered it

- **Task:** task-det-8a4ca3352d (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `8a4ca3352d749668`)
- **Subject:** rmf-ui-08 — PR #2051, 5 `pr_watcher.resume`, escalated at the cap
- **Date measured:** 2026-09-05, against the live PG board and `origin/main`

## Verdict

Nothing is left to land. Both acceptance criteria were already true when this
card dispatched:

| Criterion | Measured 2026-09-05 |
|---|---|
| the derivation no longer reports `rmf-ui-08` | `[]` — 87 `pr_watcher.*` rows in the 24h window, none for this subject |
| `detector_findings.8a4ca3352d749668` reads `cleared` | `status=cleared`, `cleared_at=2026-09-05 06:03:51` |

The finding cleared **11h27m before the card dispatched** (17:30:48). Same shape
as rmf-ui-10 (#2114) and rmf-ui-16 (#2117); landing this record as an ordinary
PR is safe because `fb989f6ad` (#2057, `earliest_clear_at`) is on main, so a
terminal card inside the window is *held*, not re-filed as `-r2`.

The card quotes *5 attempt(s)*; the row now reads **4**. Neither is wrong — the
count is re-derived over a rolling 24h window and falls as rows age out, while
the fingerprint is the OUTCOME (`needed_a_human`). The escalation's own reason
records the true figure: `resume cap reached (5/5)`.

## This one is NOT "nobody fixed it"

The two sibling records in this class both concluded the escalation was
premature and that no human ever intervened. **rmf-ui-08 is the opposite case,
and it is the class's control:** the escalation was correct, and the repair the
detector asked for is a real commit.

`73dcade27b926846e22589a1d3a645b98734d29c`, inside #2051's squash:

> Merge origin/main into the card branch after a sibling squash-merged: union on
> the sibling-append lines
>
> Sibling cards of one epic append to the same lines … Resolved as the UNION,
> forward-only on migrated hrefs, an empty side taken whole; mirrors
> byte-identical; the canvas page tests pass together.

Authored 2026-09-04 11:28:29Z by a CLI session (`session_01Uzib9pjZei8YkiqGRLpVg9`)
— exactly the prescribed repair for this cause class: *merge main IN, union the
hunks*. The watcher merged #2051 17m08s later.

| When (UTC) | Event |
|---|---|
| 09-03 23:41:11 | first `rebase_failed` — 1m51s after #2045 (rmf-ui-06) landed |
| 09-03 23:41 → 09-04 02:01 | 9 `rebase_failed`, 5 `resume`, all naming the same commit `3ebeaa39d` |
| 09-04 02:02:40 | `pr_watcher.escalate` — "resume cap reached (5/5) — manual intervention required" |
| **09-04 11:28:29** | **human merges origin/main into the branch, unions the sibling-append lines (`73dcade27`)** |
| 09-04 11:29:34 | 10th `rebase_failed` — a POST-REPAIR ARTIFACT, see below |
| 09-04 11:45:36 | watcher merges #2051 as `d47d6f087` |
| 09-04 11:58:57 | this finding first seen — 13m20s AFTER the merge |

### The 10th `rebase_failed` is not evidence the repair failed

At 11:29:34, 65 seconds after the hand-merge, the watcher logged another
`rebase_failed` naming the same commit. That is expected and means nothing: the
branch head was now a **merge commit of `origin/main`**, and `git rebase
origin/main` replays `3ebeaa39d` onto the base it had just been merged with,
reproducing the identical conflict. A reader counting `rebase_failed` rows would
conclude the human repair did not hold. It did — the PR merged 16 minutes later.

## The cause, measured: a shared-file conflict train

Correlating every `pr_watcher.rebase_failed` for rmf-ui-08 against
`git log origin/main --first-parent` in the same window:

| `rebase_failed` (UTC) | prior main landing | delta |
|---|---|---|
| 09-03 23:41:11 | `319809d81` #2045 rmf-ui-06 | 1m51s |
| 09-04 01:30:17 | `193bf37a2` #2047 rmf-ui-03 | 9m07s |
| 09-04 01:31:27 | `193bf37a2` #2047 rmf-ui-03 | 10m17s |
| 09-04 01:47:03 | `86e5f65a9` #2050 rmf-ui-11 | **0m20s** |
| 09-04 01:48:37 | `86e5f65a9` #2050 rmf-ui-11 | 1m54s |
| 09-04 01:55:35 | `0a5c93e67` #2042 rmf-wp-02 | 0m55s |
| 09-04 01:56:40 | `0a5c93e67` #2042 rmf-wp-02 | 2m00s |
| 09-04 02:00:10 | `fb989f6ad` #2057 task-f05d2bc8d1 | 0m55s |
| 09-04 02:01:11 | `fb989f6ad` #2057 task-f05d2bc8d1 | 1m56s |
| 09-04 11:29:34 | `e96bdc09a` #2052 rmf-ui-16 | 93m08s (post-repair artifact) |

**6 distinct prior landings, 7 of 10 rows within 5 minutes, median 115s, min 20s
— and exactly TWO failures per landing for the tight pairs.** The same signature
the rmf-ui-16 record measured 12/12.

File overlap between #2051's 17 files and each sibling landing:

| sibling | overlapping files |
|---|---|
| #2050 rmf-ui-11 | 11 |
| #2046 rmf-ui-09 | 10 |
| #2047 rmf-ui-03 / #2048 rmf-ui-10 / #2052 rmf-ui-16 | 9 |
| #2045 rmf-ui-06 | 8 |

Always the same set: `tools/dashboard/app.py` + its `icdev/` mirror,
`tools/dashboard/templates/base.html` + mirror, `boundary_canvas/blueprint.py` +
mirror, the `.claude/commands/start.md` Pages line, `tests/e2e/nav_intelligence_compliance.spec.ts`,
and the epic's shared feature doc. These are REAL git conflicts, so no resume
refund is due (`_resume_cycle` refunds phantoms only), and an LLM resume cannot
win: every resolution it authors is invalidated by the next sibling landing
seconds later. **Five resumes were spent on a race, not a defect.**

## A compounding finding: #2051 merged with 5 red E2E checks that were not its fault

The watcher's merge reason:

> auto-merge ok; ignored non-required failing check(s): E2E (Playwright),
> E2E Shard 1 of 4, E2E Shard 2 of 4, E2E Shard 3 of 4, E2E Shard 4 of 4

Those five were **inherited from main**, not produced by rmf-ui-08. #2052's
squash (`e96bdc09a`, 09:56:26Z) truncated the `compliance (Compliance Hub) page
loads` test in `tests/e2e/key_pages_smoke.spec.ts` — dropping its assertion and
closing `});` — leaving the file unparseable, so every Playwright shard failed at
COLLECTION. Brace balance across the refs:

| ref | `{` | `}` | balanced |
|---|---|---|---|
| `3ebeaa39d` rmf-ui-08's own commit | 42 | 42 | **YES** |
| `e96bdc09a` #2052 rmf-ui-16 | 52 | 51 | NO |
| `73dcade27` branch head after hand-merge | 52 | 51 | NO |
| `d47d6f087` #2051 merged | 52 | 51 | NO |
| `a72192653` #2060 | 50 | 50 | **YES** |

(Brace balance is a proxy; the conclusive evidence is #2060's own diff, which
restores the missing `expect(resp.status()).toBeLessThan(400); });`.)

rmf-ui-08's own commit was clean; the breakage entered with #2052 and was
repaired 22m24s after #2051 landed, by #2060 ("close the compliance-hub test the
rmf-ui-16 merge truncated — every Playwright shard on main fails at collection").
This is the incident CLAUDE.md's mfx-ci-01 block already names, recorded here
from the tree rather than quoted: its survey's two fires are "#2052 and the #2051
merge that landed on top of it".

## The systemic fix is already carded — this record does not duplicate it

Three open PRs address this cause class directly, and none needs a fourth:

- **#2070 mfx-sib-03** — pr_watcher gains a union rung for declared
  append-shaped files *before* it escalates a real conflict. This is the fix that
  would have saved all five resumes here.
- **#2091 mfx-sib-02** — the `start.md` Pages line and the Compliance
  active-path list become DERIVED, removing two of the collision lines outright.
- **#2082 mfx-sib-01** — dispatch admission refuses a second card of an epic
  whose declared artifacts collide, which is what serialises the train.
- **autonomy-act-04** (seeded) objects to card SEEDING in
  `detector_findings.py` for a finding already `cleared` with the subject `done`.

## What this record does NOT claim

`summarize_recovery`'s verdict is **correct** and was not touched. `escalate`
outranking a later `merge` is the rem-hyg-16 design: for rmf-ui-08 a human
genuinely was required, so counting the subsequent merge as an autonomous
recovery would have been the exact inflation the detector exists to refuse. No
detector, threshold or window was edited.

The board state is consistent: `rmf-ui-08` reads `done`, the deliverable is on
`origin/main` at `d47d6f087`, and no coordination lease is outstanding for it.
