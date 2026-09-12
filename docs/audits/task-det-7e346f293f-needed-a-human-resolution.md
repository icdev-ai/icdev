<!-- CUI // SP-CTI -->
# task-det-7e346f293f — `needed_a_human` finding for qa-fail-5cacee65f1d03c8c, resolved

- **Task:** task-det-7e346f293f (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `7e346f293fa08a08`, `seen_count` 3,
  `card_count` 1)
- **Subject:** qa-fail-5cacee65f1d03c8c — PR #2231, **one** `resume` cycle,
  escalated on the kpr-watch-13 undelivered rule
- **Date measured:** 2026-09-12 13:0x–13:2xZ, against the live PG board
  (`icdev`, measured) and the forge

## Verdict

**The escalation was CORRECT, it fired EARLY BY DESIGN, and a human answered it
with a real defect fix.** Eighth POSITIVE control in this card class (after
rmf-ui-08, fni-api-01, rmf-ui-13, dwr-anchor-05/dwr-ev-01, dwr-ws-02,
dwr-fid-03, dwr-fid-02).

It is also the **sixth of nine lifetime `#2184` early-escalation rows**
(`action='pr_watcher.escalate' AND details LIKE '%resume undelivered after%'`):
dwr-fid-02, dwr-ev-03, dwr-ws-02, dwr-collab-01, autonomy-act-05, **this one**,
then xrv-run-01, xrv-route-02, xrv-cost-05 — the last three all inside the last
33 hours, and all three are the other subjects the derivation reports right now.
**The shape is the NORM for this class, not an anomaly**, and it now arrives in
batches; seven of the nine escalated on their FIRST cycle.

Nothing on the subject is outstanding: PR #2231 merged 2026-09-12T03:18:21Z
(merge commit `108efd883`, a real two-parent merge, not a squash) and
`qa-fail-5cacee65f1d03c8c` is `done` on the board (03:18:20.463191Z,
`verification_result: passed`, `completed_via_bypass: false`). No lease to
release — `agent_task_leases` and `agent_coordination` both hold zero rows for
the subject.

## Re-derivation, first, as the card demands

```
python - <<'PY'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'qa-fail-5cacee65f1d03c8c'])
PY
```

At 2026-09-12T13:05Z this **still reports the subject**, and must:

```
[{'task_id': 'qa-fail-5cacee65f1d03c8c', 'attempts': 1, 'kind': 'resume',
  'reason': 'resume enqueued; prior injections: unmeasured (first injection for
             this task -- nothing prior to judge)',
  'at': datetime(2026, 9, 11, 23, 4, 45, 632939),
  'escalated': True, 'merged': False, 'board_status': None,
  'outcome': 'needed_a_human', 'needs_attention': True}]
```

`summarize_recovery` gives `escalate` priority over any later merge, and the
entry only leaves when its last counted attempt row falls outside the 24h
window. Corroborated by the real detector (`detector_findings.run_recovery`),
which reports four subjects — `xrv-cost-05`, `xrv-route-02`, `xrv-run-01` and
this one — and puts this finding's

```
earliest_clear_at = 2026-09-12T23:04:45.632939+00:00
```

i.e. the single `resume` row + 24h. **The card dispatched 12:44:01Z, ~10h21m
INSIDE its own window** — the fourth instance dispatched early (after
fni-api-01, dwr-ws-02, dwr-fid-02). Closing it is safe on the
`earliest_clear_at` ground alone: `fb989f6ad` (#2057) is verified an ancestor of
`origin/main`, so a terminal card before the clear instant is reported
`held_closed_early` and is not re-filed as a `-r2`.

## The watcher's ledger

63 `pr_watcher` rows for the subject (`action LIKE 'pr_watcher.%'`, selected on
`created_at`):

| action | n |
|---|---|
| `wait` | 59 |
| `resume` | 1 |
| `escalate` | 1 |
| `sibling_conflict_warn` | 1 |
| `behind_main_hold` | 1 |
| `merge` | **0** |

Zero `rebase`/`rebase_failed`/`union_refused`: **this was not a conflict train.**
The lone `sibling_conflict_warn` (03:18:18Z, sharing `CLAUDE.md` +
`icdev/data/claude_bootstrap/CLAUDE.md` with open PR #2241) and the
`behind_main_hold` (03:18:19Z, "17 commits behind main (limit 10)") both fired
**two seconds before the merge**, against a task already classified `done` —
artifacts of the final poll, not causes.

Escalate reason, verbatim, and it is the `#2184` shape:

> resume undelivered after 1 attempt(s) — 1 pr_watcher message(s) still unread
> in the queue. Nothing is consuming this task's queue, so further injections
> cannot be read; escalating now rather than spending the remaining 4
> attempt(s) on it. **The repair is DELIVERY, not the branch.**

So "escalated after 1 attempt(s)" in the card title is the designed behaviour,
not a premature alarm. The card quotes the last ATTEMPT's reason; read the
escalate row's own.

## What actually blocked the PR, and who fixed it

Three red checks at head `b24656dfd` — `Test`, `Test Gates`,
`Test Shard 4 of 4` (`Test` is an aggregator, so that is really two failures
plus a rollup). The repair is one commit:

- **`7320d1c37`** 2026-09-12T02:58:35Z, authored by a CLI session —
  *"fix(qa): rename the sampler's stop Event — it shadowed `Thread._stop` and
  broke `join()`"*. All three checks are green at that sha
  (`gh api repos/icdev-ai/icdev/commits/<sha>/check-runs` returns no non-success
  conclusion).

It landed **3h43m27s after the escalation** and the PR merged 19m46s later. This
is the **local-Python-3.14 / CI-3.11 class**: a `Thread` subclass binding an
`Event` to `self._stop` shadows `Thread._stop` and breaks `join()` on 3.11
while the local interpreter is happy — green on the author's host, red on the
runner. Like dwr-fid-03's host-dependent path comparison, it is a shape a
delivered resume would also have had to reproduce on the runner to see, so
"undelivered" is not the same as "the resume would have fixed it".

Delivery proof (the branch content really is on main, which `--is-ancestor`
cannot show for a merge whose branch ref still reads "ahead"):

```
git diff --stat 7320d1c37 108efd883 -- $(git diff --name-only \
  $(git merge-base 7320d1c37 108efd883^) 7320d1c37)     # empty
```

The merge landed `CLAUDE.md` (+55), `icdev/data/claude_bootstrap/CLAUDE.md`
(+55), `icdev/tools/testing/qa_agent_runner.py` (+554/-8),
`tests/test_qa_agent_runner.py` (+605) and `tools/testing/qa_agent_runner.py`
(+14, the shim).

**It was merged through the `cli.py --set-status done --merge` door, not by the
watcher** — `merged_by: icdev-ai`, board `done` 1.3s before GitHub's
`merged_at`, audit `change landed on main: qa-fail-5cacee65f1d03c8c`
(`source: kanban_merge_ledger`, `verification_result: passed`). Hence the zero
`pr_watcher.merge` rows, and hence the two measurements below.

## Two defects measured while re-deriving, both filed

### 1. Nothing drains the resume queue on the executor that actually runs (`kpr-watch-19`)

kpr-watch-13 measured that resumes are never read and shipped the **early
escalation** that acts on it; it did not make delivery work, and the reason is
structural rather than statistical:

- The only consumer of `.tmp/kanban/messages/<task>.jsonl` is
  `tools/genesis/reflexes/kanban.py:6668` — inside the **text-only LLMRouter
  executor loop**. `grep -rl check_message_queue .claude/hooks/` returns
  nothing, and `tools/agents/adapters/claude_cli.py` never mentions a queue.
- Executor mix on the board today: **4,059 of 4,070 tasks (99.73%) are
  `claude_cli`**, 11 `ollama_local`. So the drain site is unreachable for the
  executor that runs essentially every task.
- Even on the router path the drain is **mid-run only**, and every
  `pr_watcher.resume` is enqueued **post-run** (the watcher only resumes a task
  whose PR is already open). This subject is the example: dispatch produced
  #2231 at 22:45:33Z, the resume was written 23:04:45Z.
- Board-wide, 2026-09-12: **909 lifetime `pr_watcher.resume` rows, 911 undrained
  `pr_watcher` messages across 212 queue files, and 0 files in
  `.tmp/kanban/message_receipts/`.** No drain has ever happened.

```
ls .tmp/kanban/messages/*.jsonl | wc -l                                   # 212
grep -ho '"sender": "pr_watcher"' .tmp/kanban/messages/*.jsonl | wc -l    # 911
ls .tmp/kanban/message_receipts/ | wc -l                                  # 0
```

**A measurement trap found the same way:** `python -m tools.ci.resume_delivery
--task <id>` run **from a worktree** answers
`unmeasured -- queue empty and no drain receipt`, because
`hook_compat.MESSAGE_QUEUE_DIR` is `Path(__file__).resolve().parent.parent.parent
/ ".tmp" / "kanban" / "messages"` — the WORKTREE's `.tmp/`, which never holds the
file. The watcher enqueues into the main checkout. So a worker diagnosing its own
undelivered resume from its own worktree is told there is nothing pending while
the message sits unread in the main checkout's `.tmp/`. Read the file by absolute
path, or run the survey from the main checkout.

### 2. `dispositions()` reads only `pr_watcher.merge`, so 9 of 10 `card` verdicts are false (`autonomy-act-06`)

`python -m tools.kanban.detector_findings --records` dispositions this subject
**`card`** — "the escalation is the newer of the two rows" — i.e. *real work
left*, on a subject that merged and went `done` ten hours earlier. The cause:
autonomy-act-04's `record_only` test asks for a `pr_watcher.merge` row after the
escalate row, and a PR landed through the `--merge` door (or the Actions
auto-merge workflow, or a hand `gh pr merge`) writes none. Board status, which
is authoritative, is never consulted.

Measured over ALL 42 lifetime dispositioned recovery findings
(`dispositions(conn, window_hours=None)` → 32 `record_only`, 10 `card`,
76.2%): of the 10 `card` verdicts, **9 subjects are `done` on the board with
zero `pr_watcher.merge` rows** — qa-fail-5cacee65f1d03c8c, fni-api-01,
mfx-mrg-01, mfx-sib-03, kpr-stale-05, rmf-ui-13, qa-fail-b2537204d4a9b6dd,
qa-fail-84f92cebcf4fe498, task-det-920b4f1072. Only `xrv-cost-05` is genuinely
`pr_opened`. So the surface that exists to stop moot cards being dispatched is
wrong 90% of the time in the direction that dispatches them.

This is a card against the DISPOSITION SURFACE, not against
`summarize_recovery`, whose verdict remains correct: an `escalate` outranking a
later merge is exactly right here, because the merge came after a human's fix.
Neither the detector, its threshold nor its window was touched.

#### Resolved 2026-09-12 — "landed" has TWO doors

The first half of `card_disposition`'s conjunction asked one question —
*is there a `pr_watcher.merge` row newer than the newest `pr_watcher.escalate`
row?* — and only the watcher writes one. It now asks whether the subject
**landed on main** after that escalation, which either row can answer:

- `pr_watcher.merge` newer than the escalation (unchanged, `landed_via:
  pr_watcher.merge`), **or**
- a merge-LEDGER row newer than the escalation — `tools/idp/delivery_events`'
  `change landed on main: <task-id>`, whose payload carries
  `source: kanban_merge_ledger` (`landed_via: merge_ledger`).

Four properties were kept deliberately identical to the watcher half, because
each one is the thing that stops the widening from becoming an inflation:

1. **The ordering discipline.** A landing OLDER than the newest escalation
   reports `landed: False`, exactly as a pre-escalation `pr_watcher.merge`
   does — the watcher escalated about something that came after. Measured: all
   nine subjects land AFTER their escalation, so nothing rests on this being
   relaxed.
2. **The conjunction.** A landing alone is still not a record; the subject must
   also be CLOSED on the board, read through the one `CLOSED_STATUSES`
   declaration.
3. **Every unknown keeps the card.** No escalation to order against →
   `measurable: False` with `landed: None`, never `False`. An unreadable
   ledger degrades to `[]`, which means "no landing", which keeps the card.
4. **`source`, not `event_type`.** `deployment_initiated` is a shared
   vocabulary word any writer may use. The action prefix selects the rows and
   the payload's `source` confirms them, so another writer borrowing the prefix
   is not counted as a landing.

**Replay of the shipped predicate over all 42 lifetime findings**
(`python -m tools.kanban.detector_findings --records`, 55,130 watcher rows /
3,380 ledger rows):

| | record | card | % record |
|---|---|---|---|
| before | 32 | 10 | 76.2% |
| after | **41** | **1** | **97.6%** |

The 32 pre-existing records are unchanged and all still read
`landed_via: pr_watcher.merge` — the widening ADDED nine and re-classified
none. Per subject:

| subject | board | watcher merges | ledger rows | before | after | via |
|---|---|---|---|---|---|---|
| `qa-fail-5cacee65f1d03c8c` | done | 0 | 1 | card | record | merge_ledger |
| `fni-api-01` | done | 0 | 1 | card | record | merge_ledger |
| `mfx-mrg-01` | done | 0 | 1 | card | record | merge_ledger |
| `mfx-sib-03` | done | 0 | 1 | card | record | merge_ledger |
| `kpr-stale-05` | done | 0 | 1 | card | record | merge_ledger |
| `rmf-ui-13` | done | 0 | 1 | card | record | merge_ledger |
| `qa-fail-b2537204d4a9b6dd` | done | 0 | 1 | card | record | merge_ledger |
| `qa-fail-84f92cebcf4fe498` | done | 0 | 1 | card | record | merge_ledger |
| `task-det-920b4f1072` | done | 0 | 1 | card | record | merge_ledger |
| `xrv-cost-05` | pr_opened | 0 | 0 | card | **card** | — |

`xrv-cost-05` is the negative control and did not flip: it has no ledger row
and is still `pr_opened`, and its reason now names BOTH doors — *"the
escalation is the newer of the two rows; the merge ledger records no landing
for xrv-cost-05 after the escalation at …"* — so "no watcher merge" and "no
landing at all" stay distinguishable to a reader.

The one remaining `card` is the honest denominator. 97.6% is not a perfect
score and the surface is not claiming one; it is claiming that the single card
left is the single subject with work still in it.

`git diff` touches `tools/kanban/detector_findings.py` and its test only —
never `summarize_recovery`, never an `args/` threshold, never `window_hours`
(the four diff lines naming it are all inside the new `merge_ledger_rows`,
which takes it for the same LIFETIME-by-default reason `watcher_outcome_rows`
does).

## Why this card is closed, and how

Ordinary PR, no `hold` label, no `scheduled_at` deferral — the recipe confirmed
from the fourth instance onward. `fb989f6ad` verified an ancestor of
`origin/main` first, so `earliest_clear_at` holds this terminal card inside its
window (`held_closed_early`) rather than filing `task-det-7e346f293f-r2`. The
derivation will go empty of its own accord after 2026-09-12T23:04:45Z, and the
`detector_findings` row clears on the first `detector_findings_reflex` cycle
after that.

## Traps re-hit, for the next instance

- `audit_trail` has **`created_at`, not `timestamp`**, and watcher rows are
  selected by **`action LIKE 'pr_watcher.%'`**.
- `detector_findings` has `first_seen_at`/`last_seen_at`, and
  **`earliest_clear_at` is an in-memory `Finding` field, not a column**.
- `dispositions()` returns its rows under the key **`findings`** (not `rows`),
  and `window_hours=None` means LIFETIME.
- The card's quoted fingerprint `7e346f293fa08a08` is the `finding_id`; the
  `fingerprint` COLUMN reads `needed_a_human` and `detector` reads `recovery`
  (not `recovery_summary`). Query by `subject`.
