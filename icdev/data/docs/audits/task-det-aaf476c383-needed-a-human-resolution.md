<!-- CUI // SP-CTI -->
# task-det-aaf476c383 — `needed_a_human` finding for dwr-anchor-05, resolved

- **Task:** task-det-aaf476c383 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, seen 4x, `card_count` 1)
- **Subject:** dwr-anchor-05 — PR #2169, five `resume` cycles, escalated
- **Date measured:** 2026-09-09, against the live PG board (`icdev`, measured)
  and the forge

## Verdict

**The escalation was CORRECT and a human ANSWERED it — three times.** This is the
fourth POSITIVE control case in this card class (after rmf-ui-08, fni-api-01 and
rmf-ui-13): the branch carries three genuine two-parent merge commits of
`origin/main`, every one authored AFTER the 01:52:33Z escalation, and the watcher
merged the PR 15m30s after the last one. Nothing was outstanding by the time the
card dispatched.

Re-derived first, as the card demands:

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'dwr-anchor-05'])
EOF
# -> []
```

Corroborated by running the detector itself (`detector_findings.run_recovery`,
window 24h, 65 rows, 9 tasks attempted, 6 `needed_a_human` + 3 `recovered`):
`dwr-anchor-05` is **not among the six**. The six it does report are
dwr-collab-01, dwr-ws-02, dwr-ev-03, dwr-fid-02, dwr-fid-03 and dwr-anchor-04 —
each with its own card.

## Why it is no longer reported, and why that is not a fix

`summarize_recovery` gives `escalate` priority over any later `merge`, so an
entry leaves ONLY when its last `pr_watcher.resume`/`rebase` row falls outside the
24h window. The last resume is `2026-09-08T01:51:39.147668`, so the clear-by is
**2026-09-09T01:51:39Z**. The card dispatched at **02:03:58.847Z** — 12m19s past
it, the tightest margin this card class has recorded. Closing it is therefore safe
on both grounds #2057 established: the derivation is empty AND the terminal card
lands after `earliest_clear_at`, so `held_closed_early` does not fire and no `-r2`
is filed.

Nothing about the detector, its window or its threshold was touched.

## The ledger

`pr_watcher` rows naming the subject, lifetime: **112 `wait`, 5 `resume`,
1 `escalate`, 1 `sibling_conflict_warn`, 2 `merge`** — and no `rebase`, no
`rebase_failed`.

| at (UTC) | action | reason |
|---|---|---|
| 01:05:35 | `resume` | cycle 1 — prior injections: unmeasured (first injection) |
| 01:16:08 | `resume` | cycle 2 — prior injections: **undelivered** (1 unread) |
| 01:26:33 | `resume` | cycle 3 — **undelivered** (2 unread) |
| 01:40:49 | `resume` | cycle 4 — **undelivered** (3 unread) |
| 01:51:39 | `resume` | cycle 5 — **undelivered** (4 unread) |
| 01:52:33 | `escalate` | **resume cap reached (5/5) — manual intervention required; NONE of the 5 injection(s) were ever read (5 still unread in the queue)** |
| 07:06:02 | `sibling_conflict_warn` | shares source files with #2175, #2170 (classification already `done`) |
| 07:06:07 | `merge` | auto-merge ok |
| 07:07:02 | `merge` | PR already merged |

The cap escalation fired **54 seconds** after the fifth resume — the
`final_attempt_grace_seconds` shape kpr-watch-13 measured at p50 41s, well inside
the 600s `RESUME_COOLDOWN_SECONDS`.

The card's quoted `reason` is the last ATTEMPT's, not the escalation's. The
escalate row's own reason is the first on this board to state the delivery verdict
in the escalation itself — kpr-watch-13's clause is live and working.

## The five resumes were never read

```
python -m tools.ci.resume_delivery --task dwr-anchor-05 --json
  verdict: undelivered — 5 pr_watcher message(s) still unread in the queue
  pending: 5, receipted: 0
```

Re-derived from the filesystem, sharing no code with `queue_message`. So
`resume cap reached (5/5)` is five attempts **that were never made** — the
kpr-watch-13 mechanism, tenth-instance finding, reproduced exactly.

## Who actually repaired it, and what the cause was

Three two-parent merge commits on `kanban/dwr-anchor-05`, all by a CLI session
(`goagiq`), each merging a distinct `origin/main` landing:

| branch commit | at (UTC) | second parent | that landing (UTC) | lag |
|---|---|---|---|---|
| `f18713a01` | 05:44:54 | `6d3af5801` dwr-sect-02 (#2171) | 05:16:21 | 28m33s |
| `0d1313a55` | 06:13:20 | `8faf34233` inbox-wake-race (#2176) | 06:11:13 | **2m07s** |
| `c4f878af2` | 06:50:37 | `d97a3de4e` inbox-wake-race-2 (#2177) | 06:48:54 | **1m43s** |

Two of the three land within 2m07s of a sibling — the shared-file conflict train
signature (33s–279s, median 142s) measured 12 of 12 on the `rmf-ui-*` epic. Here
the epic is `dwr-*` and the collision set is named directly by the
`sibling_conflict_warn` payload:

```
shares source file(s) with open PR(s):
  #2175 [icdev/tools/document_intelligence/suggestion_store.py,
         tests/docmod/test_hitl_decision_wiring.py,
         tools/document_intelligence/suggestion_store.py]
  #2170 [icdev/tools/document_intelligence/blueprint.py,
         tools/document_intelligence/blueprint.py]
```

`dwr-sect-02` touched `tools/document_intelligence/blueprint.py` in both trees,
which is the same file #2170 (dwr-fid-01) held — so the first hand-merge was the
real conflict resolution and the next two were the train re-forming under it.
`fb0f1a551` (dwr-ev-01, #2173) landed at 07:02:08Z, after the third hand-merge,
and did NOT invalidate it: the PR merged at 07:06:07Z (`auto-merge ok`), 15m30s
after `c4f878af2`, and #2170 followed 43 seconds later.

So the escalation asked for a human and got one — `escalate` outranking the later
`merge` is CORRECT here, exactly as it was for rmf-ui-08. Do NOT read this class
as "nobody did anything" without checking the branch's own history: `gh pr view
2169 --json commits` shows all three merge commits even though the PR
squash-merged to the single parent `6acbe40e8`.

## State at close

| fact | value |
|---|---|
| subject board status | `done` (07:07:02.744Z, 55s after the merge) |
| PR #2169 | MERGED 07:06:07Z, squash `6acbe40e8` |
| lease | free — `restore_acts.py --apply reap_dead_lease --target dwr-anchor-05 --dry-run` → *"no live lease — nothing to reap"* |
| derivation | empty (both `_recovery_rows()` and `run_recovery`) |
| `detector_findings` row | `active`, seen 4, first seen 2026-09-08T02:19:03, last seen 2026-09-08T20:45:26 — clears on the first MEASURABLE `detector_findings_reflex` cycle after 01:51:39Z |

Nothing was outstanding, nothing was fixed here, and nothing needed to be: the
human work landed 20h before the card dispatched. This record is the artifact.

## Running tally for this card class

Twelve instances now. **Moot: 8** (rmf-ui-10, rmf-ui-16, rmf-ui-08*, flx-airgap-01,
fni-api-01*, rmf-ui-13*, mfx-sib-02, and this one*). **Real work outstanding: 4**
(mfx-sib-03, mfx-mrg-01, task-det-9a62ee81a7, qa-fail-6a87916931be3793).
Asterisked entries are POSITIVE CONTROLS — moot at dispatch *because a human had
already answered the escalation*, which is not the same as "the escalation was
wrong". Re-derive, then read the branch's own history, before concluding either.
