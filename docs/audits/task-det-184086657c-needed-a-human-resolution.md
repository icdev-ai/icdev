<!-- CUI // SP-CTI -->
# task-det-184086657c — `needed_a_human` finding for flx-airgap-01, resolved

- **Task:** task-det-184086657c (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `184086657ceff3df`)
- **Subject:** flx-airgap-01 — PR #2088, rebased once by pr_watcher, escalated
- **Date measured:** 2026-09-05, against the live PG board

## Verdict

**Nothing is left to land, and no human ever answered the escalation.** PR #2088
merged at `2026-09-05T05:24:02Z` as `a4686cff0` — by the watcher itself
(`pr_watcher.merge`, "auto-merge ok"), **16m25s after it escalated**. `flx-airgap-01`
is `done` (05:25:02). The two commits on the branch are the worker session's own
(`00bb0b9d5`, `44f4f0b7d`); there is **no merge commit in the branch history**, so
the control-case check that rmf-ui-08 established comes back negative.

This is the eighth instance of this card class and the sixth confirmed
**PREMATURE** escalation. It is the first with a cause that is neither a
sibling-conflict train nor a stale mirror: **the watcher escalated ten seconds
before the CI it was looking for existed.**

## The measured cause: an empty rollup is not a workflow that never fired

`pr_watcher._ci_never_fired` (tools/ci/pr_watcher.py:1494) returns True when
`statusCheckRollup` is empty **and** the PR is older than
`ci_missing_grace_minutes` (15, args/pr_watcher_config.yaml:325). Two facts about
this deployment make that predicate fire on healthy CI:

1. **A workflow run exists for 32–84 seconds before its first check run appears
   in the rollup.** Measured on this PR's own three runs, from the GitHub API:

   | Run | created | first check run on that sha | gap |
   |---|---|---|---|
   | `33945860279` (00bb0b9d5) | 04:55:47 | *none* — cancelled 04:57:11 | ≥84s, zero check runs |
   | `33945915649` (00bb0b9d5) | 04:57:10 | 04:58:15 `Helm Lint` | 65s |
   | `33946363331` (44f4f0b7d) | 05:07:15 | 05:07:47 `Lint` | 32s |

   Against a `poll_interval_seconds: 30` watcher, a run in that gap is
   indistinguishable from a workflow that never fired.

2. **The grace period is anchored to the PR's `createdAt`, not to the head sha.**
   #2088 was created 04:39:09, so the 15-minute grace expired at 04:54:09 — and
   *every push after that had zero grace*, including the rebase force-push 95
   seconds later and the worker's own push 12 minutes later. The docstring says
   the grace "exists because a PR opened seconds ago legitimately has an empty
   rollup while GitHub queues the workflow"; that is exactly the condition it
   stops covering after the first 15 minutes of a PR's life.

### The retrigger made it worse, and then had nothing left to give

`max_ci_retriggers_per_pr: 1` is counted over the PR's **lifetime**
(`_ci_retrigger_attempts`, line 1489). The one retrigger was spent at 04:57:07 on
the *first* empty-rollup episode — a run that had been alive 80 seconds and whose
checks appeared 68 seconds later. The close/reopen created run `33945915649`,
which (via mfx-ci-02's `cancel-in-progress`) **cancelled the healthy run at
04:57:11 before it had materialised a single check run**. So the repair destroyed
a working run, restarted CI, and left the budget empty for the *second* episode —
which is the one that escalated.

### Timeline (UTC, from `audit_trail` and the GitHub API)

| When | Event |
|---|---|
| 04:39:09 | PR #2088 opened (head `9e46f65a6`); run `33945137453` created 04:39:11 |
| 04:40:11 → 04:54:19 | 14x `wait` — "CI still running" |
| 04:54:09 | **`ci_missing_grace_minutes` expires** — from here every empty rollup is eligible |
| 04:55:25 | `rebase_refund` — "forge reported CONFLICTING but the merge is clean" |
| 04:55:44 | `rebase` — force-pushed `00bb0b9d5`; run `33945860279` created 04:55:47 |
| 04:57:07 | `ci_retrigger` (1/1) — rollup empty at 80s; closes/reopens the PR |
| 04:57:11 | run `33945860279` **cancelled with zero check runs** by the reopen's run |
| 04:58:15 | first check run finally appears on `00bb0b9d5` — 68s after the retrigger |
| 05:06:27 | worker authors `44f4f0b7d`; pushed ~05:07:15 (run `33946363331` created) |
| **05:07:37.5** | **`escalate` — "CI never fired; re-trigger exhausted"** + HITL alert |
| **05:07:47** | **first check run (`Lint`) appears — 9.5s after the escalation** |
| 05:08:38 → 05:22:53 | 15x `wait` — "CI still running". The empty-rollup window was **under 61s wide** |
| 05:23:57 | `sibling_conflict_warn` — shares `.gitignore` with open PR #2084 (did not block) |
| **05:24:02** | **`merge` — "auto-merge ok"**; run `33946363331` concludes `success` 05:25:39 |
| 05:25:02 | `flx-airgap-01` → `done`; 05:25:03 second `merge` row ("PR already merged") |
| 06:03:51 | `detector_findings` row `184086657ceff3df` first seen |
| 17:30:48 | this card dispatched |

## Why the finding is still reported, and why that is correct

`summarize_recovery` gives `escalate` priority over any later `merge`, because a
merge after an escalation is normally the human the escalation asked for. The
entry leaves only when its last counted attempt row ages out of the 24h window:
the `rebase` at `04:55:44.713` ⇒ **2026-09-06 04:55:44Z**. That is also exactly
the `earliest_clear_at` the reflex stamps on the finding
(`recovery_findings`: last attempt + `window_hours`), so closing this card now is
**held, not re-filed** — the task-f05d2bc8d1 / `fb989f6ad` machinery, confirmed on
`origin/main`.

Measured at the time of writing: finding `status=active`, `seen_count=4`,
`last_seen_at=2026-09-05 18:43:31`, `cleared_at=NULL`.

## The class is rare, and it re-escalates while it holds

All 44,822 lifetime `pr_watcher.escalate` rows, by reason:

| n | reason |
|---|---|
| 44,779 | resume cap reached (5/5) — manual intervention required |
| **30** | **CI never fired; re-trigger exhausted** |
| 13 | PR is stale — exceeded max age |

The 30 span only four PRs — #1483 (11 rows), #1646 (6), #1651 (6) and #2088 (1) —
because the watcher re-escalates on every poll while the condition holds. #2088
escalated **once**, which is itself the evidence that the condition was a
sub-poll-interval artifact rather than a stuck workflow.

## Disposal

No repair is landed here. The fix belongs in `tools/ci/pr_watcher.py`, which is a
`protected_path` (kpr-watch-05) — a PR touching it is refused by both merge paths
by design and needs a human, so folding it into this record's PR would stall the
card rather than close it. Filed instead as **kpr-watch-12**, unclaimed, carrying
this measurement. Two candidate narrowings, neither surveyed yet:

- anchor the grace to the **head sha's** push time rather than the PR's
  `createdAt`, so every push gets the grace the docstring describes; and/or
- treat a workflow run that **exists** for the head sha as "CI fired", instead of
  inferring it from the check-run rollup alone.

The detector is **not** at fault and nothing about it was touched: it recorded the
watcher's own verdict faithfully, which is the rem-hyg-16 rule working as designed.
