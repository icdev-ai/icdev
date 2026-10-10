<!-- CUI // SP-CTI -->
# task-det-ba08a68b04 — `needed_a_human` finding for dwr-fid-02, resolved

- **Task:** task-det-ba08a68b04 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16; finding `ba08a68b04b345c9`, `seen_count` 3,
  `card_count` 1, filed 2026-09-08T14:21:33.706219Z)
- **Subject:** dwr-fid-02 — PR #2186, **two** `resume` cycles, escalated
- **Date measured:** 2026-09-09 02:4x–03:0xZ, against the live PG board
  (`icdev`, measured) and the forge

## Verdict

**The escalation was CORRECT, it fired EARLY BY DESIGN, and a human answered it
with a merge whose resolution the union rung could have produced mechanically.**

This is the **first-ever** firing of the kpr-watch-13 early-escalation shape
(#2184, `72765eec1`, merged 2026-09-08T10:10:28Z) — 8m10s after that change
landed. All four lifetime rows of the shape are from 2026-09-08 and this one is
the earliest:

| escalated at | subject | attempts |
|---|---|---|
| **10:18:38.328808Z** | **dwr-fid-02** | **2** |
| 10:22:27.855378Z | dwr-ev-03 | 3 |
| 11:09:19.282621Z | dwr-ws-02 | 1 |
| 23:56:57.310058Z | dwr-collab-01 | 1 |

Nothing on the subject is outstanding: PR #2186 merged 2026-09-08T20:33:53Z and
`dwr-fid-02` is `done` on the board (20:35:04.650638Z). No lease is held
(`restore_acts.py --apply reap_dead_lease --target dwr-fid-02 --dry-run` answers
"no live lease — nothing to reap").

## Re-derivation, first, as the card demands

The card's own snippet, run against the live board at 2026-09-09T02:45Z, **still
reports the subject**, and must:

```
[{'task_id': 'dwr-fid-02', 'attempts': 2, 'kind': 'resume',
  'reason': 'resume enqueued; prior injections: undelivered (1 pr_watcher
             message(s) still unread in the queue)',
  'at': datetime(2026, 9, 8, 10, 8, 13, 772322),
  'escalated': True, 'merged': True, 'board_status': None,
  'outcome': 'needed_a_human'}]
```

`merged` has flipped to `True` since the card's own evidence block was written,
and the outcome is unchanged. That is the rem-hyg-16 rule working: `escalate`
outranks a later `merge`, because that merge is the human the escalation asked
for, and counting it as a recovery is the inflation the detector exists to
refuse. **The detector, its threshold and its window are untouched by this card.**

`python -m tools.kanban.detector_findings --records` now dispositions it a
**record** and not a card (autonomy-act-04):

```
record  dwr-fid-02  active  a pr_watcher.merge at 2026-09-08T20:33:54.064394+00:00
        landed AFTER the escalation at 2026-09-08T10:18:38.328808+00:00,
        and the subject is `done`: nothing is left to land
```

It read `card` when it was FILED at 14:21:33Z, correctly — the merge came 6h12m
later.

## The ledger

139 `pr_watcher.*` rows carry `task_id: dwr-fid-02` (selected by
`action LIKE 'pr_watcher.%'` on `created_at`; note `audit_trail` has no
`timestamp` column and `event_type` is not the watcher's name):

| action | n |
|---|---|
| `wait` | 119 |
| `union_refused` | 7 |
| `rebase_failed` | 7 |
| `resume` | 2 |
| `escalate` | 1 |
| `sibling_conflict_warn` | 1 |
| `merge` | 2 |

Every one of the 7 `union_refused` rows carries the identical reason:

```
refused: files=['CLAUDE.md', 'icdev/data/claude_bootstrap/CLAUDE.md']
rules=[] verifiers=[] -- undeclared: CLAUDE.md matches no union_resolver.files entry
```

and each is followed within 90ms by the `rebase_failed` it caused.

The escalation's own reason — **read this, never the card title's last-attempt
reason**:

```
resume undelivered after 2 attempt(s) — 2 pr_watcher message(s) still unread in
the queue. Nothing is consuming this task's queue, so further injections cannot
be read; escalating now rather than spending the remaining 3 attempt(s) on it.
The repair is DELIVERY, not the branch.
```

`python -m tools.ci.resume_delivery --task dwr-fid-02` confirms it today:
`undelivered -- 2 pr_watcher message(s) still unread in the queue`.

## The cause: `union_refused` on `CLAUDE.md` — kpr-watch-14, ALREADY FILED

`kpr-watch-14` ("union_resolver refuses a whole set for one undeclared file;
CLAUDE.md is 55.6% of refusals") is on the board, `scheduled`, unclaimed. **This
card does not re-file it and does not fix it here**: `args/pr_watcher_config.yaml`
is a `protected_path`, so declaring `CLAUDE.md` needs the mfx-mrg-04 audited door,
and mfx-ci-04 requires the packaged bootstrap be regenerated in the same commit.
Folding either into a resolution record would stall this card behind a
protected-path merge.

Re-derived over the lifetime rows today: **35 of 65 `union_refused` rows (53.8%)
name `CLAUDE.md`**, across 4 of the 9 tasks that have ever hit the rung
(dwr-ev-03, dwr-fid-02, dwr-fid-03, mfx-own-04). This incident's 7 rows are
inside that count.

### The refusal was mechanically avoidable, and the proof is arithmetic

Branch base `13c0c1f4b`. **Both sides are pure appends:**

| ref | CLAUDE.md lines | vs base |
|---|---|---|
| base `13c0c1f4b` | 3534 | — |
| branch `00c385c01` | 3647 | +113, **0 deletions** |
| main `39d6655e6` | 3680 | +146, **0 deletions** |
| human merge `981844beb` | 3792 | — |

3534 + 113 + 146 = 3793 against the merge's 3792 (one blank line coalesced), and
the diffs settle it exactly:

```
git diff --numstat 00c385c01 981844beb -- CLAUDE.md   ->  145  0  CLAUDE.md
git diff --numstat 39d6655e6 981844beb -- CLAUDE.md   ->  112  0  CLAUDE.md
```

**Zero deletions from either parent.** The human's hand resolution kept both
blocks in full — which is precisely what the `keep_both_blocks` rule
`docs/features/*.md` already carries would have produced, unattended, 9h47m
earlier. The union rung refused it only because the path is not declared.

## It is NOT a conflict train, and the distinction matters

Only two CLAUDE.md landings sit in the window (times converted from the log's
`-04:00`):

| landed | commit | first following `rebase_failed` | lag |
|---|---|---|---|
| 09:57:19Z | `56780545b` (#2182, dwr-anchor-06) | 09:58:12.765Z | **53s** |
| 09:59:01Z | `583875149` (#2180, dwr-ev-02) | 10:00:26.659Z | **85s** |

Those two lags are the documented 33s–279s train signature. But there is **no
third landing** — the next CLAUDE.md commit on main is this PR's own at 20:32Z.
So `rebase_failed` rows 3–7 (10:02:14, 10:11:24, 10:13:11, 10:18:38, 10:20:12)
are **the same unresolvable conflict re-hit on every poll**, not fresh sibling
collisions. Reading seven rows as seven landings would overstate the epic's
churn; the branch simply could not be rebased at all until a human merged.

The single `sibling_conflict_warn` (20:33:49Z, naming #2187 and #2183 over
`{icdev/,}tools/document_intelligence/blueprint.py` and CLAUDE.md) fired **5
seconds before the merge** — serialisation working, not a cause.

## A human DID answer — SEVENTH positive control

```
981844beb2544832d66a27be4726774d979fa762
 parents: 00c385c01d24d6049e93cad37903ecc8ab4dc86c 39d6655e65e5f9e20e216a1e5ed67e5b420868f6
 author : Sovanna Larry Chuon <sovanna.chuon@gmail.com>
 date   : 2026-09-08T20:05:50Z
 subject: Merge origin/main into kanban/dwr-fid-02
```

A real two-parent merge, **9h47m12s after the 10:18:38Z escalation**; the watcher
merged #2186 at 20:33:53Z, **28m03s later**, and the board went `done` 71s after
that. So `escalate` outranking the later `merge` was correct here, exactly as it
was for rmf-ui-08, fni-api-01, rmf-ui-13, dwr-anchor-05, dwr-ev-01 and dwr-ws-02.

Do NOT conclude "nobody intervened" from a watcher `auto-merge ok` row —
`gh pr view 2186 --json commits` shows the merge commit, and #2186 landed as a
merge (`1fc9da025`) rather than a squash, so it is on main in full.

### The early escalation saved three sessions and cost nothing

Delivery is 100% undelivered board-wide (kpr-watch-13), so the two injections
this task spent were never read and the remaining three would not have been
either. The task would have reached 5/5 and escalated regardless, later. Against
a cause the LLM could not have fixed anyway — a rebase the watcher itself refuses
— stopping at 2 is strictly better. **"escalated after 2 attempt(s)" in a card
title is not an anomaly; read the escalate row's reason.**

## Why closing this card is safe

`earliest_clear_at` = the entry's `at` (2026-09-08T10:08:13.772322Z, the newest
counted attempt) + `window_hours` 24 = **2026-09-09T10:08:13.772322Z**. This card
dispatched at 02:03:58.847Z and is being closed ~7h22m BEFORE that instant, so
the derivation is NOT yet empty and the card is closed on the
`earliest_clear_at` ground alone — the same footing as dwr-ws-02
(task-det-82438c25bc) and dwr-fid-03 (task-det-73b8573563).

`fb989f6ad` (#2057, the `earliest_clear_at` hold) is verified an ancestor of
`origin/main`, so `detector_findings` will report `held_closed_early` and will
NOT file a `-r2`. The finding clears on the first `detector_findings_reflex`
cycle after 10:08:13Z.

No `scheduled_at` deferral and no `hold` label — an ordinary PR, confirmed safe
for this class since the fourth instance.

## Named, not fixed here

- **kpr-watch-14** — declare `CLAUDE.md` (+ its packaged bootstrap copy) in
  `union_resolver.files` with `keep_both_blocks`. Already on the board,
  `scheduled`, unclaimed. This incident is its strongest live evidence: a
  provably keep-both conflict that stalled a PR for nine hours and drew the
  board's first early escalation.
- **kpr-watch-13's delivery gap** — nothing drains
  `.tmp/kanban/messages/<task>.jsonl`. Live, filed, unclaimed.
- **autonomy-act-05** — the panel and the detector read different row sets
  (`recovery_summary.AUDIT_ACTIONS` is 8 values; `claims._recovery_rows` and
  `detector_findings.recovery_rows` each hard-code 4), so `rebase_failed` is
  invisible to the thing that files and clears cards. Here that understates
  dwr-fid-02 at 2 attempts where the panel counts the 7 `rebase_failed` rows too.
  Filed; widening changes what gets FILED, so it needs a replay before arming.
