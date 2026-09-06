<!-- CUI // SP-CTI -->
# task-det-1f22df3838 — `needed_a_human` for fni-api-01: the escalation was CORRECT, and the resume channel that should have prevented it has never delivered a message

- **Task:** task-det-1f22df3838 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `1f22df3838150f56`)
- **Subject:** fni-api-01 — icdev_ft#340, 5 `pr_watcher.resume`, escalated at the cap
- **Date measured:** 2026-09-05/06, against the live PG board, `origin/main` and
  the `icdev-ai/icdev_ft` forge

## Verdict

**Nothing is left to land on the subject.** icdev_ft#340 merged at
2026-09-05T16:56:39Z and `kanban_tasks.fni-api-01` reads `done` (16:56:40.38).
The lease is already released: `kanban:task:fni-api-01` was held by
`kanban-scheduler-2980` on a 3600s TTL from 15:28:25Z, pid 2980 is not running,
and the governed door agrees —
`restore_acts.py --apply reap_dead_lease --target fni-api-01 --dry-run` refuses
with *"no live lease — nothing to reap"*.

The derivation still reports the subject, and **must**: `summarize_recovery`
gives `escalate` priority over any later merge, and the entry leaves only when
the newest `pr_watcher.resume`/`rebase` row falls outside the 24h window.

| | |
|---|---|
| newest attempt row | `2026-09-05 16:31:27.802973` |
| `earliest_clear_at` (attempt + `detectors.recovery.window_hours` = 24) | **`2026-09-06 16:31:27`** |
| finding at time of writing | `status=active`, `seen_count=3`, `cleared_at=NULL` |

This card dispatched at 01:51:24Z on 09-06, i.e. **INSIDE** the window — unlike
rmf-ui-10/-16/-08 and flx-airgap-01, whose findings had already cleared. That is
safe here for the reason #2057 (`fb989f6ad`, `earliest_clear_at`) exists: a
terminal card before that instant is HELD (`held_closed_early`), not re-filed as
`-r2`. Landing this record as an ordinary PR is correct; **do not** take a `hold`
label or a `scheduled_at` deferral.

## What actually happened

| UTC | Event |
|---|---|
| 15:47:44 | PR #340 opened (`kanban/fni-api-01`, head `d9b5a1a31`) |
| 15:49:36 | `resume` 1/5 — `merge_conflict` |
| 15:59:49 | `resume` 2/5 |
| 16:10:12 | `resume` 3/5 |
| 16:20:58 | `resume` 4/5 |
| 16:25:07 | `wait` — `gh pr view failed: GraphQL: API rate limit already exceeded` |
| 16:31:27 | `resume` 5/5 |
| **16:32:39** | **`escalate` — "resume cap reached (5/5) — manual intervention required"**, 1m12s later |
| 16:48:29 | `8fc60b554` lands on the branch — a real merge resolution, 15m49s AFTER the escalation |
| 16:49–16:55 | `wait` × 6 — "CI still running" |
| 16:56:08 | `wait` — "CI green; awaiting approving review" |
| 16:56:39 | **MERGED** by `icdev-ai`, 23m59s after the escalation |

`8fc60b554` is the rmf-ui-08 control check answering positive — a genuine repair
commit inside the squash, not a rubber stamp:

> Merge origin/main into fni-api-01: keep the LANDED orchestrator, delete the duplicate
>
> This branch was cut one commit before fni-ana-02 (#338) merged, and the card
> text it was built from names a module that has never existed — `orchestrator.py`.
> Following the card literally, the build wrote its OWN 224-line orchestrator
> while the same pipeline landed on main as `analyze.py` (288 lines, separately
> tested). […] Three real defects in the route surfaced by pointing it at the
> REAL function, none of which a test against the duplicate could have caught.

So the escalation was **correct**. The subject's cause is a *semantic* conflict —
two implementations of one rule, with the API wired to the untested one — which
no textual merge and no `git merge-tree` can see. `escalate` outranking the later
merge is right here, and this is not an inflated recovery.

## The finding this card actually produced

**`pr_watcher`'s resume channel has never delivered a message to an agent on this
deployment. All 847 resumes it has ever recorded are still sitting unread in
files on disk.**

`_send_resume` calls `hook_compat.queue_message`, which **appends a JSONL line to
`.tmp/kanban/messages/<task-id>.jsonl`** and returns. The audit row's
`reason: "injected resume context"` therefore records *that a line was written to
a file* — never that anything read it. The only drain is `check_message_queue`,
which **deletes the file**, and it has exactly one runtime call site:
`tools/genesis/reflexes/kanban.py:6292`, inside `_dispatch_via_llm_router`'s
per-iteration loop. Two consequences:

1. that loop only runs **while a dispatch is in flight**, which is by definition
   not the state of a task whose PR is already open and whose worker has exited —
   the only state in which the watcher ever sends a resume; and
2. it is the **LLM-router fallback** executor. The primary `claude_cli` adapter
   path never calls `check_message_queue` at all.

Measured on the live checkout 2026-09-06:

| | |
|---|---|
| queue files under `.tmp/kanban/messages/` | 186 |
| files holding UNDRAINED `sender: pr_watcher` messages | **185** |
| undrained `pr_watcher` messages | **849** |
| lifetime `pr_watcher.resume` audit rows (2026-08-01 → 09-05) | **847** |

Because a drained file is *deleted*, a drain leaves no trace — but the count on
disk **exceeds** the count ever recorded, so no recorded resume has been drained.
fni-api-01's own file holds all five, verbatim, right now.

Both sides of that inequality move on a live board — re-measured ~20 minutes
later the same evening the disk read **187 files / 850 messages** while the
watcher kept polling. Quote a reading with its instant, as this repo already
requires: one figure off a live board is not a measurement. The *inequality* is
what carries the argument, and every new resume only widens it.

The corroborating signal on this PR: `kanban/fni-api-01` carries exactly two
commits, `d9b5a1a31` (15:47:15, the original build) and `8fc60b554` (16:48:29,
the repair). **The head sha did not move once across resumes 1–5.** Five
attempts, zero pushes — consistent with nobody having read any of them.

What this does **not** say: `_emit_wake_events` is a second, independent channel
(`emit_pr_state` promotes a registered wake `pending -> due`), and its own
docstring notes that *"an empty `promoted` is the normal case — most events have
no listener"*. This survey measures the message queue, which is the channel
`_send_resume` writes and the one the `resume` audit row is emitted for.

### Secondary: the fifth attempt is granted a median of 41 seconds

`RESUME_COOLDOWN_SECONDS = 600` exists because of a measured defect — #1742/#1744
burned all five cycles in ~3 minutes at poll speed — and the constant's own
comment says *"No agent can read a message and push a fix in 45 seconds, so those
were not five attempts; they were one attempt and four wasted budget entries."*
The cooldown spaces resumes 1→5. **It does not protect the fifth.** The escalate
branch fires on the first poll where `cycle >= max_cycles`, i.e. one poll after
the final injection.

Replaying every `resume cap reached` escalation in `audit_trail` (153 lifetime;
146 with a preceding resume row), gap from the FINAL resume to the escalation:

```
min 32s   p50 41s   p90 60s   max 715s
within 180s: 145/146 (99.3%)   within the 600s cooldown: 145/146 (99.3%)
```

fni-api-01's 72s is unremarkable. So the last of five "attempts" is universally
declared spent inside the very interval the cooldown constant was written to rule
out — and on this PR the repair landed 17m01s after that injection.

How often that matters is **bounded, not proven**: 109 of 153 (71.2%) resume-cap
escalations have a `pr_watcher.merge` row afterwards, median **238.2 min**, so
most are not a fifth resume finishing late. But 24 (15.7%) merge within an hour
and 4 within 30 minutes. fni-api-01 merged 24 minutes after its escalation.

Re-derive:

```bash
# survey A/B: audit_trail, action IN ('pr_watcher.resume','pr_watcher.escalate','pr_watcher.merge')
python -m tools.ci.merge_readiness --json
# survey C: the undrained queue, from the repo root
ls .tmp/kanban/messages/*.jsonl | wc -l
grep -ho '"sender": "pr_watcher"' .tmp/kanban/messages/*.jsonl | wc -l
```

**Filed as `kpr-watch-13`** (seeded UNCLAIMED, carrying this survey). Not fixed
here: `tools/ci/pr_watcher.py` is a `protected_path` (kpr-watch-05), the repair
spans the watcher, `hook_compat` and the dispatch path, and it needs its own
fire-rate survey before anything is armed. Folding it into this record would
stall the card behind a PR the watcher can never merge — the mfx-sib-03 lesson.

## Acceptance criteria

| Criterion | State |
|---|---|
| the derivation no longer reports `fni-api-01` | **not yet** — clears at `2026-09-06 16:31:27`, then the first `detector_findings_reflex` cycle (6h) after it |
| `detector_findings.1f22df3838150f56` reads `cleared` | follows automatically from the above |

Both are time, not work. The subject is delivered, the lease is free, and the
cause is recorded. Closing this card inside the window is safe on `fb989f6ad`'s
`earliest_clear_at`; if a `-r2` appears anyway, the daemon is running a tree
older than `fb989f6ad` — restart it, and **never** edit the detector, its window
or its threshold.
