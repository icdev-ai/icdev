<!-- CUI // SP-CTI -->
# task-det-82438c25bc — `needed_a_human` finding for dwr-ws-02, resolved

- **Task:** task-det-82438c25bc (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, `seen_count` 2, `card_count` 1)
- **Subject:** dwr-ws-02 — PR #2187, **one** `resume` cycle, escalated
- **Date measured:** 2026-09-09 02:2x–02:4xZ, against the live PG board
  (`icdev`, measured) and the forge

## Verdict

**The escalation was CORRECT, it fired EARLY BY DESIGN, and a human answered it
with two real defect fixes.** Fifth POSITIVE control in this card class (after
rmf-ui-08, fni-api-01, rmf-ui-13 and dwr-anchor-05) — and the first whose
escalation is the **new kpr-watch-13 shape**: the watcher gave up after ONE
attempt because it had PROVEN the resume was never read, rather than spending the
remaining four on a queue nobody drains.

Nothing on the subject is outstanding: PR #2187 merged 2026-09-08T21:06:17Z and
`dwr-ws-02` is `done` on the board (21:06:39Z).

## Re-derivation, first, as the card demands

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'dwr-ws-02'])
EOF
```

At 2026-09-09T02:23Z this **still reports the subject**, and must:

```
[{'task_id': 'dwr-ws-02', 'attempts': 1, 'kind': 'resume',
  'reason': 'resume enqueued; prior injections: unmeasured (first injection for
             this task -- nothing prior to judge)',
  'at': datetime(2026, 9, 8, 10, 58, 54, 81765),
  'escalated': True, 'merged': True, 'board_status': None,
  'outcome': 'needed_a_human'}]
```

Note `merged` has flipped to `True` since the card's own evidence block was
written — and the outcome is unchanged, which is the rem-hyg-16 rule working:
`escalate` outranks a later `merge`, because that merge is the human the
escalation asked for. It was, literally, here.

Corroborated by the detector itself (`run_recovery`, 24h window): six
`needed_a_human` subjects — dwr-collab-01, **dwr-ws-02**, dwr-ev-03, dwr-fid-02,
dwr-fid-03, dwr-anchor-04 — each with its own card.

## Why it is still reported, and why closing the card is safe anyway

`summarize_recovery` drops a task with **no ATTEMPT row** in the window
(`_ATTEMPT_KINDS` = resume / rebase / rebase_failed / ci_retrigger); `escalate`
and `merge` alone cannot hold an entry open. The only attempt row is the single
`resume` at `2026-09-08T10:58:54.081765`, so:

    clear-by = 2026-09-08T10:58:54Z + 24h = 2026-09-09T10:58:54Z

The card dispatched at **02:03:58.847Z**, ~8h55m INSIDE its own window — the same
shape as the tenth instance (fni-api-01), and unlike instances 4/5/6/8/11/12 the
derivation is **not** empty at close time.

Closing is nevertheless safe, on the one ground #2057 (`fb989f6ad`, verified an
ancestor of `origin/main`) established: a TERMINAL card while
`now < earliest_clear_at` is **held** (`held_closed_early`), not re-filed as
`-r2`. `run_recovery` sets `earliest_clear_at = at + window_hours`, i.e. the same
10:58:54Z.

**Do not defer this card with `scheduled_at`** — a runner-dispatched worker cannot
hold that way (the scheduler's post-run `_move_task` overwrites it), and the
`hold` label is defeated by the Actions auto-merge workflow. Landing an ordinary
PR is the confirmed-safe route, now for the sixth time.

## Under autonomy-act-04 this finding is, TODAY, a RECORD and not a card

```
merge_after_escalation(rows, 'dwr-ws-02')
-> {'measurable': True, 'superseded': True,
    'escalated_at': '2026-09-08T11:09:19.282621+00:00',
    'merged_at':    '2026-09-08T21:06:39.351207+00:00',
    'merge_reason': 'PR already merged',
    'reason': 'a pr_watcher.merge landed after the escalation'}
```

The card was **correct when filed**, and the rule was not bypassed: the finding
was first seen `14:21:33.706Z`, nearly seven hours BEFORE the `21:06:39Z` merge
row existed. At that instant the escalation was genuinely unanswered and the
conjunction autonomy-act-04 requires (a merge NEWER than the newest escalate AND
the subject closed) was false. The disposition is re-derived every run precisely
because a stored verdict about an ORDER goes stale the moment either row gains a
successor — which is what happened here, in the seven hours between filing and
dispatch.

## What actually held the PR — and it was NOT a conflict

Ledger for dwr-ws-02, 173 `pr_watcher` rows: **153 `wait`, 17
`sibling_conflict_warn`, 1 `resume`, 1 `escalate`, 1 `merge`**. Zero
`rebase`/`rebase_failed`. Classification on the resume and the escalate is
`ci_failed`, and the resume context names the checks:

    Failing checks: Test Shard 2 of 4, Test Shard 4 of 4, Test

Two independent real defects, both on the branch, both requiring a human:

1. **`f913ca3f4` 20:09:59Z — a stale `args/` <-> `icdev/data/args/` MIRROR.**
   `test_registry_yaml_and_loader_are_mirrored` (Test Shard 2): the branch added
   its Workspace nav link and the `dic.suggestions` entry to
   `args/component_registry.yaml` and not to the packaged copy, so `icdev init`
   would have scaffolded a registry without the page the card adds. The same class
   the `tools/` mirror gate catches, on the DATA side. (This is the mirror the
   mfx-sib-03 record names as ungated by `mirror_parity`; here it *was* caught, by
   a test in CI, ~9h after the push — not at commit time.)

2. **`56f56547e` 20:37:20Z — an ORDER DEPENDENCE, green alone and red in-suite.**
   Test Shard 4 failed `assert '/document-intelligence/workspace' in {}` — an
   EMPTY rule map — while the file passed standalone. `tools.dashboard.app` builds
   itself once at import and gates the DIC blueprint on `ICDEV_DIC_ENABLED`;
   `os.environ.setdefault` cannot override a value another test already set, and
   an app imported earlier in the same shard is already built. Alone the test was
   the first importer and set the variable itself; in a shard it was not. Repaired
   by registering `dic_bp` on a fresh Flask app. This is the defect
   `isolation_run.py` exists for, in the direction that tool cannot see — the file
   was green ALONE and red IN-SUITE.

The watcher merged 28m57s after the second fix. The 17 `sibling_conflict_warn`
rows (all naming PR #2183 over
`{icdev/,}tools/document_intelligence/blueprint.py`) fired at 20:53–21:05Z, i.e.
AFTER both repairs, and are the merge-serialisation rung doing its job, not a
cause.

## The escalation shape is NEW, and this is its first full measurement

`72765eec1` / PR **#2184** ("stop spending the resume budget on a queue nobody is
draining") merged **2026-09-08T10:10:28Z**. Its clause reads, on this card's own
escalate row:

    resume undelivered after 1 attempt(s) — 1 pr_watcher message(s) still unread
    in the queue. Nothing is consuming this task's queue, so further injections
    cannot be read; escalating now rather than spending the remaining 4
    attempt(s) on it. The repair is DELIVERY, not the branch.

Measured over **all 44,835 lifetime `pr_watcher.escalate` rows**: exactly **4**
carry this shape, all on 2026-09-08, all after 10:18Z, on four tasks —
dwr-fid-02 (2 attempts, 10:18:38Z, the first ever), dwr-ev-03 (3, 10:22:27Z),
**dwr-ws-02** (1, 11:09:19Z) and dwr-collab-01 (1, 23:56:57Z). All four are among
the six `needed_a_human` subjects the detector reports right now; the other two
(dwr-fid-03, dwr-anchor-04) carry the older `resume cap reached (5/5)` shape.

`resume_delivery --task dwr-ws-02` independently agrees: `undelivered — 1
pr_watcher message(s) still unread in the queue`. The message is deliberately NOT
drained: kpr-watch-13 is report-only by design and the messages on disk are the
evidence.

**The honest reading of the day's spike.** 2026-09-08 recorded 9 `recovery`
findings, the highest single day on the board (09-06: 2, 09-05: 5, 09-04: 7,
09-03: 4). The new clause did **not** create a new population: delivery is 100%
undelivered board-wide, so each of those four would have run to `5/5` and
escalated with `resume cap reached … NONE of the 5 injection(s) were ever read`
regardless. What changed is WHEN — an escalation that used to arrive after five
cycles and four cooldowns now arrives after one to three, so on the day the clause
ships, escalations that would have been spread over hours arrive together. It is a
rate shift, not an inflation, and the trade is four LLM sessions saved per task
against a card filed sooner. Do not read the 9 as evidence the clause is wrong.

## What was NOT done, and why

- **Nothing was changed in the detector, its threshold or its window.** The
  finding is correct and its verdict is correct.
- **The undelivered message was not drained.** kpr-watch-13 has no actuator on
  purpose (pinned by an AST test); draining it by hand would destroy the evidence
  and make the next reading of `resume_delivery` a fabricated `delivered`.
- **The `args/` <-> `icdev/data/args/` mirror is still not gated at commit time.**
  `mfx-ci-01`'s pre-commit hook covers `tools/<pkg>` <-> `icdev/tools/<pkg>` pairs
  only; this data mirror is caught by a CI test, which is a ~9h feedback loop
  rather than a sub-second one. Extending the hook is a separate card owing its
  own fire-rate survey (how many commits touch `args/` at all, and how many of
  those legitimately touch only one side), not a drive-by here.
- **No lease to release.** `restore_acts.py --apply reap_dead_lease --target
  dwr-ws-02 --dry-run` answers "no live lease — nothing to reap".

## Disposition

Land this record as an ORDINARY PR. The finding clears on the first
`detector_findings_reflex` cycle after 2026-09-09T10:58:54Z; until then the
terminal card is held by `earliest_clear_at` and no `-r2` is filed.
