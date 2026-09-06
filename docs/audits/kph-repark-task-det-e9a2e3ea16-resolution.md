# kph-repark-task-det-e9a2e3ea16 — resolution

**Card:** `[REPARK] task-det-e9a2e3ea16: parked twice by worktree-isolation-guard within 24h`
**Filed by:** `kanban_stranded_reflex` (`tools/kanban/orphan_requeue.py`), which correctly refused to requeue a recurring park a third time.
**Resolved:** 2026-09-06. All times UTC.

---

## Verdict

The card's diagnosis was right and its prescribed remedy was right, in that
order. The park's cause was the fictional 30-second worktree-add budget that
#2146 (`4cbac49ea`, merged 16:17:04) removed — but at the moment this card was
worked, **only half the dispatching fleet was running that fix**. The genesis
daemon had been running pre-merge code since 05:44 and parked THIS card the
same way at 16:49, thirty-two minutes after the fix merged. The daemon was
restarted through the enumerated `restart_stale_daemon` act, and only then was
`task-det-e9a2e3ea16` requeued by hand. It dispatched at 17:13:55 into the
complete, clean checkout the killed add had left behind, without entering the
add path at all.

Nothing here raised the 30s budget and nothing retries inside the guard.

---

## The subject task

`task-det-e9a2e3ea16` is itself a `[NEEDED-A-HUMAN]` recovery card for
`rmf-ui-13` (PR #2034). That subject is closed:

| fact | value |
|---|---|
| `pr_watcher.escalate` row | 2026-09-03 12:21:59, classification `merge_conflict` |
| `rmf-ui-13` -> `done` | 2026-09-03 18:37:04 (pr_watcher reconciled #2034 MERGED); re-confirmed 18:43:57 via `cli --merge` |
| `detector_findings` row | detector `recovery`, subject `rmf-ui-13`, **`cleared` 2026-09-04 17:59:03**, seen_count 4, card_count 1 |

So the requeued worker has nothing to build; it lands the resolution record as
an ordinary PR, the pattern established across ten prior `task-det-*` records.
The finding cleared 19 hours before this requeue, so a terminal card cannot be
read as "did not hold" and no `-r2` is filed (#2057, `earliest_clear_at`).
Its `failure_count` was preserved at 3 by the requeue: an UNVERIFIED run would
land it in `needs_decomposition` again, which is exactly what happened on
2026-09-03 20:11 (`done -> needs_decomposition: No git commits found on task
branch`) — the worker must commit the record and open the PR, not POST `done`.

## The parks, verbatim from `kanban_status_transitions`

| when | transition | actor |
|---|---|---|
| 09-03 20:11:21 | `done -> needs_decomposition` | scheduler — *No git commits found on task branch* |
| 09-03 20:41:01 | `scheduled -> validating` | **worktree-isolation-guard** (park 1) |
| 09-05 05:59:04 | `scheduled -> validating` | **worktree-isolation-guard** (park 2) |
| 09-06 00:15:14 | `validating -> scheduled` | cli — *stranded in validating; requeued by hand* |
| 09-06 00:15:57 | `scheduled -> validating` | **worktree-isolation-guard** (park 3, 43s later) |
| 09-06 (reflex) | `validating -> scheduled` | kanban_requeue_reflex — `empty_checkout (kpr-stale-06)` |
| 09-06 15:27:36 | `scheduled -> validating` | **worktree-isolation-guard** (park 4) |
| 09-06 17:12:24 | `validating -> scheduled` | cli — this card's requeue |
| 09-06 17:13:55 | `scheduled -> in_progress` | scheduler — *dispatched: agent subprocess launched* |

Park 3's reflex line reads *"Refusing to remove …task-det-e9a2e3ea16: 84
entries but no .git — possibly a partial delete"* — a leftover from park 2's
killed add. Park 4's line is the textbook #2146 shape: `git worktree add …
timed out after 30 seconds`, and what it left on disk is a **complete, clean
checkout**:

```
.tmp/worktrees/task-det-e9a2e3ea16/.git   present (55-byte gitfile)
entries                                    79
tools/manifest.md                          present
git status --short                         0 lines
HEAD                                       0263b5876 (origin/main at the time)
git worktree list                          registered, branch kanban/task-det-e9a2e3ea16, 0 commits ahead
```

That is the defect #2146 measured — the parent `git` killed at 30s while its
checkout child finished the tree — reproduced on this task.

## The fix landed on half the fleet

`.logs/tools.genesis.code_reload.ndjson` holds exactly one line for the day:

```
16:19:20 code_reload: pulled 6 file(s) from origin/main
16:19:20 code changed on disk (1 file(s), e.g. kanban.py) — re-executing to pick it up
```

That was the **scheduler**: pid 16744 started 16:19:24, `agent_sessions.code_version`
`4cbac49ea`. Its add for THIS card at 17:01:39 reads *"Created worktree … in
13.5s (budget 30s)"* — the new log format, single-digit-to-low-teens seconds.

The **genesis daemon** (pid 12180, started 05:44:03) stayed on `f6adb15fa`,
nine changed files in its import closure including
`tools/genesis/reflexes/kanban.py`, and logged no reload decision at all. Its
own log proves it authored the 16:49 park of this card:

```
16:48:12  [GENESIS] Reflex 'kanban' starting
16:49:44  Worktree creation failed for kph-repark-task-det-e9a2e3ea16: … timed out after 30 seconds
16:50:37  [GENESIS] Reflex 'kanban' finished in 145156.3ms (success=False)
```

145 seconds is 30s to the kill plus ~115s blocked on the child's inherited
pipes — the pre-fix shape exactly. The message text is the pre-fix text too:
the shipped code logs *"exceeded its 30s budget after N.Ns and was KILLED"* and
cannot emit `timed out after 30 seconds` on that path.

### Why the daemon never reloaded, although its loop calls the reloader

`DaemonBase.run_forever` calls `code_reload.restart_if_code_changed` after
every cycle, so "the daemon does not self-update" is not a missing call. It is
the watch set. `code_reload.snapshot()` is taken once at loop start from
`sys.modules`, and `changed_files` counts only paths present in **both**
snapshots — its own comment: *"a path in `after` but not in `before` is NOT a
change. It is a lazy import."* Every `tools/genesis/reflexes/*.py` is imported
lazily on first dispatch, so a reflex is outside the daemon's watch set for the
life of the process, and a merged fix to any reflex goes live on the daemon
only on restart. The scheduler imports the kanban reflex at startup, which is
why it saw the change and the daemon did not. `daemon.py`'s
`_register_process_identity` docstring records the symptom ("the one supervised
process that does NOT self-update"); this is the mechanism.

That is a standing defect, not this card's to fix: filed as
**autonomy-id-06** (unclaimed) with this record as its evidence.

## What was done, in order

1. **Proved and applied the enumerated restart act** (autonomy-act-03):

   ```
   python tools/awareness/restore_acts.py --apply restart_stale_daemon --target tools.genesis.daemon --dry-run
   #   proven: true — genesis_daemon (pid 12180) imports 9 changed file(s); supervisor pid 22332 will restart it
   python tools/awareness/restore_acts.py --apply restart_stale_daemon --target tools.genesis.daemon
   #   audited: true (audit_trail id 141359), terminate_sent, confirmed: true, outcome: applied
   ```

   Replacement daemon: pid 22604, started 17:11:30, `code_version 4cbac49ea`.
   The registry row for 12180 reads `active` until it ages out of
   `SESSION_TTL_SECONDS`; the process is gone.

2. **Requeued the subject by hand**, after both dispatchers were on the fix:

   ```
   python tools/kanban/cli.py --requeue task-det-e9a2e3ea16 --requeue-status scheduled
   #   task-det-e9a2e3ea16: validating -> scheduled  (failure_count preserved at 3)
   ```

3. **Observed the dispatch** one scheduler interval later: 17:13:55
   `scheduled -> in_progress`, and NO worktree-add line for the task. The
   reflex found the path registered in `git worktree list --porcelain` and
   reused it. The leftover checkout was deliberately left in place for that
   reason: it is complete and clean, nothing on its branch is unlanded, and
   reusing it means the requeued dispatch never enters the add path at all.
   (Neither this card's fresh worktree nor the leftover carries a `.env`; the
   worker's environment comes from the dispatcher, so the two are equivalent.)

## Named, not acted on

- `restore_acts --plan` also proves `tools.ci.pr_watcher` (pid 36452, 3 changed
  files) and `tools.proposal_genesis.daemon` (pid 21636, 4 changed files) stale.
  Neither dispatches worktree adds, neither caused a park, and the watcher was
  mid-merge; they are the same act, one target each, for whoever owns them.
- 21,400 files / 513 MB per worktree with `playwright-report/` at 40.9% of it —
  #2146's own carry-forward. Untracking it is its own card.

## Re-derive

```bash
python tools/kanban/cli.py --show task-det-e9a2e3ea16
python -m tools.kanban.stranded_audit --json
grep -h '"ts": "2026-09-06T16:4[89]' .logs/tools.genesis.daemon.ndjson | grep kanban
grep -h "2026-09-06T1[67]" .logs/tools.genesis.code_reload.ndjson
grep -h "task-det-e9a2e3ea16\|kph-repark-task-det-e9a2e3ea16" .logs/tools.genesis.reflexes.kanban.ndjson | grep -i "worktree"
python tools/awareness/restore_acts.py --plan --json          # stale daemons, proven, with the supervisor's state
python -m pytest tests/kanban/test_worktree_add_budget_is_real.py -q
```
