<!-- CUI // SP-CTI -->
# task-det-0196c59513 — `needed_a_human` finding for xrv-cost-05, resolved

- **Task:** task-det-0196c59513 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `0196c59513ef071e`, `seen_count` 2,
  `card_count` 1, `first_seen_at` 2026-09-12T13:11:36.524560)
- **Subject:** xrv-cost-05 — PR #2254, **21** `rebase_failed` rows paired with
  **20** `union_refused`, 1 `resume`, 5 `escalate`, 1 `ci_retrigger`
- **Date measured:** 2026-09-12 16:55–17:10Z, against the live PG board
  (`icdev`) and the forge

## Verdict

**The escalation was CORRECT, the watcher was structurally incapable of
repairing this branch, and a human answered it with the only merge that could
work.** Ninth POSITIVE control in this card class.

The cause is NOT a sibling conflict train, NOT a stale branch and NOT a
host-dependent path comparison — the three the card's own advice text names. It
is a **DELETE-vs-REWRITE collision over `CLAUDE.md`**, and the union rung
refused it *by design* under a guard that landed on `main` **3m38s before this
PR first hit it**.

Nothing on the subject is outstanding: PR #2254 merged 2026-09-12T15:53:30Z
(merge commit `c1df5981e`, a real two-parent merge, not a squash) and
`xrv-cost-05` is `done` on the board (15:53:30.812742Z). No lease to release —
`agent_task_leases` and `agent_coordination` both hold zero rows for the
subject.

## Re-derivation, first, as the card demands

```
python - <<'PY'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'xrv-cost-05'])
PY
```

At 2026-09-12T16:55Z this **still reports the subject**, and must:

```
[{'task_id': 'xrv-cost-05', 'attempts': 23, 'kind': 'rebase_failed',
  'reason': 'rebase onto origin/main hit conflicts: Could not apply f823d83e1...
             # feat(mcp): every tools/call leaves one audit row, and the two
             callers are counted apart (x',
  'at': '2026-09-12 14:45:20.478059',
  'escalated': True, 'merged': False, 'board_status': None,
  'outcome': 'needed_a_human', 'needs_attention': True}]
```

`summarize_recovery` gives `escalate` priority over any later merge, and the
entry only leaves when its last counted attempt row falls outside the 24h
window. **Clear-by = 2026-09-12T14:45:20.478059Z + 24h = 2026-09-13T14:45:20Z**;
the `detector_findings` row clears on the first `detector_findings_reflex` cycle
(6h, last run 14:27:17Z) at or after that instant. Nothing here was touched to
make it read `cleared`.

Two artifacts of the derivation snippet, neither a defect:

* `board_status: None` while the board reads `done` — the snippet passes no
  `task_status` map, so `needs_attention` (which is `escalated and status not in
  CLOSED_STATUSES`) cannot see the closure. The Home panel, which does pass it,
  reads this subject as **not** needing attention.
* `attempts` grew 19 → 23 between the card being filed and being worked. The
  watcher kept retrying a rebase it could never complete; see below.

## The actual cause

### Phase 1 — 10:52 to 12:27Z: an e2e spec, no declared rule

```
refused: files=['tests/e2e/cache_savings_spend_panel.spec.ts'] rules=[] verifiers=[]
 -- no declared rule resolves the hunk at line 45 (base 4 line(s), main 8, card 22;
    tried ['keep_both_blocks'])
```

A real content conflict in a file `args/pr_watcher_config.yaml` declares no union
rule for. Correct refusal.

### Phase 2 — 12:51 to 13:54Z: `main` trims CLAUDE.md, and the resolver DISCARDS the trim

`ace4d4d9b` (PR #2259, **xrv-docs-02**) merged 2026-09-12T12:46:54Z with
`108 insertions, 4252 deletions` in `CLAUDE.md` — the essay move to
`docs/reference/cards/`. `CLAUDE.md` enters the refusal file list at **12:51:59Z,
5m05s later**, exactly the conflict-train signature this class is known for.

But read what the resolver DID with it:

```
refused: files=['CLAUDE.md', 'icdev/data/claude_bootstrap/CLAUDE.md',
                'tests/e2e/cache_savings_spend_panel.spec.ts']
 rules=['CLAUDE.md:other_side_when_empty@86',
        'icdev/data/claude_bootstrap/CLAUDE.md:derived_from:CLAUDE.md']
 ... no declared rule resolves the hunk at line 45
```

`CLAUDE.md:other_side_when_empty@86` **resolved**. `main`'s side of that hunk was
empty because `main` had just DELETED those lines; the card's side was not; the
pre-mfx-mrg-08 `_resolve_cluster` yielded to the non-empty side and so
**silently resurrected the 4252-line trim**, reporting success. The only reason
that resolution was never pushed is the *unrelated* e2e-spec conflict at line 45
aborting the whole rebase.

### Phase 3 — 13:56 to 14:45Z: mfx-mrg-08 lands and the same hunk refuses by name

`eb129880e` (PR #2262, **mfx-mrg-08**, "an empty side over a NON-EMPTY base is a
DELETION, not silence") merged 13:52:59Z, adding the `if not base_seg:` guard in
front of `RULE_OTHER_SIDE_WHEN_EMPTY`. From **13:56:37Z**, 3m38s later:

```
refused: files=['CLAUDE.md', 'icdev/data/claude_bootstrap/CLAUDE.md'] rules=[] verifiers=[]
 -- no declared rule resolves the hunk at line 86 (base 3905 line(s), main 0, card 3965;
    tried ['keep_both_blocks'])
 -- the base branch DELETED these lines and the card rewrote them
```

Three things move together at that instant and they are one event:

1. `rules` goes from listing `CLAUDE.md:other_side_when_empty@86` to `[]` —
   `CLAUDE.md` now refuses on its own hunk instead of being "resolved", so no
   rule name is accumulated before the raise.
2. The failing commit moves EARLIER in the branch, `3e1a72033` → `f823d83e1`.
   `f823d83e1` is the commit that appends this card's record to `CLAUDE.md`;
   pre-fix it applied cleanly *because of the discard*, and the rebase only died
   later on `3e1a72033`'s e2e spec.
3. The deletion clause appears, naming the shape in words.

**This is an unplanned live confirmation of mfx-mrg-08 on the largest deletion
on the board, minutes after it landed.** The guard is what turned a silent,
successful-looking resurrection of a 4252-line trim into a refusal a human could
see. No damage occurred — the rebase never completed, so nothing was pushed.

### Why no resume could ever have helped

`git rebase origin/main` replays `f823d83e1` onto the post-trim `main` and
reproduces the identical conflict every time, which is why 21 attempts produced
one message. The branch had no defect in it. The watcher's 5 `escalate` rows are
on two independent rungs — one at 08:50:57Z on the kpr-watch-13 undelivered rule
("Nothing is consuming" the resume queue, already carded as kpr-watch-19) and
four at 12:17:28 / 13:46:20 / 13:49:43 / 14:31:58Z reading "CI never fired;
re-trigger exhausted". One `rebase_failed` at 12:24:13Z is the known
`git worktree add` kill, not a conflict.

## The repair, and which door it came through

`8e6119ee3` — "merge: main into xrv-cost-05, resolving the CLAUDE.md trim
collision", authored by a CLI session at **15:32:43Z**, 47m23s after the last
watcher attempt. It merges `main` **INTO** the branch rather than rebasing onto
it, which is the only move that works here: a rebase discards the resolution and
replays the pre-trim commit forever.

PR #2254 merged 15:53:30Z, 20m47s later, through the
`cli.py --set-status done --merge` door — the watcher's own ledger for that
minute reads `wait / "enforced gate: awaiting ICDEV done-verification"` and there
is **no `pr_watcher.merge` row**. All watcher attempts precede the repair, so
none of the 21 `rebase_failed` rows is a post-repair artifact.

## `disposition: card` is a CADENCE ARTIFACT here, not a live work item

`python -m tools.kanban.detector_findings --records` reports:

```
"disposition": "card",
"reason": "the escalation is the newer of the two rows; the merge ledger records
           no landing for xrv-cost-05 after the escalation at 2026-09-12T14:31:58.722635+00:00"
```

Both doors read false, and both for benign reasons. `merge_after_escalation`
finds no `pr_watcher.merge` because the CLI door wrote none — exactly the gap
**autonomy-act-06** (merged 14:31:57Z, `c2f5e6113`) added `ledger_landing` to
close. But `ledger_landing` reads rows written by the **`idp_delivery_events`
reflex on a 6h cadence**, and that reflex last ran at **15:15:10Z — 38m20s
BEFORE this merge**. The newest ledger row on the board is `autonomy-act-06` at
14:31:58Z; nothing has been written since, including for PR #2271.

**So the second door lags the first by up to 6 hours, and xrv-cost-05 is the
first subject to exercise that lag** — autonomy-act-06's own docstring measured
nine subjects it would have flipped and named xrv-cost-05 as "the only one
genuinely still open", which was true when measured and stopped being true 1h21m
later. A `card` verdict within one reflex period of a non-watcher merge is not
evidence the subject is open; re-derive against the board and the forge. The
disposition should flip `card` → `record` on the ~21:15Z ledger cycle. This is a
reading note, not a defect claim — nothing here was changed.

## Disposition of this card

Landed as an ORDINARY PR, per the confirmed-safe path: `fb989f6ad` (#2057,
`earliest_clear_at` / `held_closed_early`) is verified an ancestor of
`origin/main`, so a terminal card before clear-by is HELD, not re-filed as
`-r2`. No `hold` label, no `scheduled_at` deferral. The detector, its window and
its threshold were not touched.

## What was NOT done, and why

* **No fix to the union resolver.** It behaved correctly on every one of the 20
  refusals. Phase 2's discard is the bug mfx-mrg-08 already fixed, on `main`
  since 13:52:59Z.
* **No union rule for `tests/e2e/*.spec.ts`.** Phase 1's hunk (base 4, main 8,
  card 22) is a genuine semantic conflict in test code, not an append train; a
  union rule there would resolve it wrongly and silently.
* **No card against `ledger_landing`'s cadence.** It is a documented 6h reflex
  doing what it says. Recorded above so the next reader of a `card` verdict does
  not mistake the lag for an open subject.
