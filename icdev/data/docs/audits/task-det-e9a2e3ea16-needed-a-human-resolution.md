<!-- CUI // SP-CTI -->
# task-det-e9a2e3ea16 — `needed_a_human` for rmf-ui-13: a human DID answer, twice at once, and the claim meant to stop that did not hold

- **Task:** task-det-e9a2e3ea16 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `e9a2e3ea160fdb67`)
- **Subject:** rmf-ui-13 — icdev#2034, 5 `pr_watcher.resume`, escalated at the cap
- **Date measured:** 2026-09-06, against the live PG board, `origin/main` and the
  `icdev-ai/icdev` forge

## Verdict

**Nothing is left to land on the subject, and both acceptance criteria already
hold.** #2034 merged at 2026-09-03T18:36:33Z (squash `436b19754`),
`kanban_tasks.rmf-ui-13` reads `done` (18:43:57Z, `completed_via_bypass=0`), and:

| | |
|---|---|
| derivation (`summarize_recovery` over `_recovery_rows()`, subject `rmf-ui-13`) | `[]` |
| `detector_findings.e9a2e3ea160fdb67` | `status=cleared`, `seen_count=4`, `card_count=1`, `cleared_at=2026-09-04 17:59:03` |
| newest attempt row (`rebase_failed`) | `2026-09-03 12:57:57` |
| `earliest_clear_at` (attempt + 24h) | `2026-09-04 12:57:57` — cleared at the first 6h reflex cycle after it |
| lease `kanban:task:rmf-ui-13` | reaped 2026-09-03 18:01:28 (`restore.reap_dead_lease.applied`); no keeper files under `.tmp/coordination/claims/` |

The squash carries every line of the repaired branch: the delivery proof
(`git diff --stat <branch-head> <squash> -- <files the branch changed>`, never
`--is-ancestor`, which is false by construction for a squash) is EMPTY between
`bb7ce0d7e` and `436b19754`. Nothing to land, nothing to release. This record is
the deliverable.

This card dispatched at 17:13:55Z on 09-06 — 47h after the finding cleared and
71h after the subject merged. Landing this as an ordinary PR is safe on both
grounds #2057 (`earliest_clear_at`) established: the window is long spent, and a
terminal card inside it would be HELD, not re-filed.

## What actually happened — a sibling conflict train, three landings, two failures each

The escalation's OWN reason is `resume cap reached (5/5) — manual intervention
required`; the card's quoted `injected resume context` is the fifth attempt's,
not the escalation's. Every attempt row is classified `merge_conflict`, and
every `rebase_failed` names the same commit: `Could not apply 62bc7cb2b`, the
branch's one real commit.

| UTC | Event | Δ from prior main landing |
|---|---|---|
| 11:09:04 | `62bc7cb2b` — the card's single commit, PR #2034 opened | |
| **11:37:53** | **rmf-ui-14 (#2033, `d1f68e095`) lands on main** | |
| 11:38:49 | `rebase_failed` | +56s |
| 11:38:49 | `resume` 1/5 | |
| 11:40:23 | `rebase_failed` | +150s |
| 11:49:13 | `resume` 2/5 | |
| 11:59:45 | `resume` 3/5 | |
| 12:09:02 | `sibling_conflict_warn` on #2035 naming #2034 and #2032 — the collision set, verbatim | |
| **12:09:06** | **rmf-ui-15 (#2035, `71b9d66e5`) lands on main** | |
| 12:09:33 | `rebase_failed` | +27s |
| 12:10:54 | `rebase_failed` | +108s |
| 12:10:55 | `resume` 4/5 | |
| 12:21:16 | `resume` 5/5 | |
| **12:21:59** | **`escalate` — resume cap reached (5/5)**, 43s after the fifth resume | |
| **12:55:42** | **claim-verif-33c9f4cd11 (#2036, `e3b3a9a6e`) lands on main** | |
| 12:56:39 | `rebase_failed` | +57s |
| 12:57:57 | `rebase_failed` — the newest attempt row; the 24h clock starts here | +135s |
| 17:56:11 / 17:58:05 | operator sets rmf-ui-13 `in_progress` by CLI to hold it while repairing by hand | |
| 17:56:14 / 17:58:21 | zombie reaper demotes it to `token_exhausted` 3s and 16s later ("heartbeat silent >2h" — the dead worker's silence, inherited by a hand-held task) | |
| 17:58:04 | operator's `--claim rmf-ui-13` takes the lease (pid 19000, session `local-52a377d68b83`) | |
| 18:01:10 | `520644f43` — first repair: merge `origin/main` at `e3b3a9a6e`, union in card order 13/14/15 | |
| **18:01:28** | **`restore.reap_dead_lease` reaps the operator's claim** — holder pid dead, task not heartbeating — 3m24s after it was taken, while the repair was in flight | |
| **18:08:20** | **rmf-ui-12 (#2032, `b2f9ec5cd`) lands on main** — the first repair is behind again | |
| 18:13:51 | `619c13a5f` — a SECOND session's repair: merge the main that contains rmf-ui-12 | |
| 18:15:58 | `bb7ce0d7e` — the second session merges the first session's parallel repair, keeping the superset | |
| **18:36:33** | **MERGED by the watcher's UNLINKED sweep** (`task_id: ""`, "unlinked PR, green and mergeable") — the task sat in `token_exhausted`, a status the linked sweep does not poll | |
| 18:37:04 | watcher reconciles rmf-ui-13 `pr_opened -> done`: "MERGED but the task was left in `token_exhausted` — nothing polls that status" | |
| 18:43:57 | `cli.py --set-status done --merge` finds the PR already merged; `done` re-recorded, every gate passed | |

Six `rebase_failed` rows, three distinct sibling landings, exactly two failures
per landing, 27–150s after each — the signature measured 12 of 12 on rmf-ui-16
and 10 of 10 on rmf-ui-08. The collision set is the one the
`sibling_conflict_warn` row names: `tools/dashboard/app.py` and its `icdev/`
mirror, `compliance.html` in both trees, `security_canvas/blueprint.py` in both
trees, the epic feature doc `docs/features/rmf-ui-compliance-route-migration.md`,
`tests/e2e/nav_intelligence_compliance.spec.ts`, plus (from the repair commit)
the `start.md` Pages line and the Compliance dropdown in both `base.html` copies.
REAL git conflicts, so no resume refund is due, and no LLM resume could author a
resolution the next sibling would not invalidate within minutes.

## The escalation was CORRECT, and the control check is positive

Three merge commits sit inside the squash — `520644f43`, `619c13a5f`,
`bb7ce0d7e`, all authored by CLI sessions between 18:01 and 18:16Z, 5h39m after
the escalation. `escalate` outranking the later `merge` is right here: a human
did the work. This is the third positive control after rmf-ui-08 (`73dcade27`)
and fni-api-01 (`8fc60b554`), against six subjects the watcher merged itself.

**But the human answered TWICE, in parallel, and that is the finding this
subject produced.** The first repair (`520644f43`, merging main at `e3b3a9a6e`)
and the second (`619c13a5f`, merging main at `b2f9ec5cd`, 12 minutes later) were
two sessions repairing one branch at once; the second had to merge the first as
a third commit and pick the superset on every shared line. The operator's
`--claim` was taken at 17:58:04 precisely to stop that, and the governed reaper
released it at 18:01:28 — correctly, by its own two-signal proof: the CLI's pid
had exited on the next line and nothing heartbeat for a hand-held task. That is
the incident **mfx-own-02** (#2069, `b48c42a15`, on main since 09-04) was written
against — `--claim` now hands its lease to a registered keeper that heartbeats
until `--release` — and CLAUDE.md's "a second session repaired the same branch at
14:01" is `520644f43` in local time. The claim did not hold because a shell is
not a service session; it holds now.

The zombie reaper's two demotions (3s and 16s after each manual `in_progress`)
are the same defect from the board's side: a task held by a human has no
heartbeat, so the reaper reads the dead worker's silence as the human's. It is
also why #2034 merged through the UNLINKED door — `token_exhausted` is not a
status the linked sweep polls, so the watcher saw a green unlinked PR, merged it,
and then reconciled the task with a hand-written reason. Not repaired here (the
watcher is a `protected_path`); recorded so the next reader does not misfile an
"unlinked" merge row as a PR nobody owned.

## What this CARD did for three days, and why it dispatched 71h late

| UTC | Card transition |
|---|---|
| 09-03 17:54:10 | dispatched — 7 minutes BEFORE the first repair commit, while the operator was mid-repair |
| 09-03 20:05–20:06 | zombie-reclaimed twice, token-retried twice |
| 09-03 20:10:23 | set `done` by CLI |
| 09-03 20:11:21 | scheduler demotes `done -> needs_decomposition`: "No git commits found on task branch" |
| 09-03 20:41:01 | `worktree-isolation-guard` parks it in `validating` — `git worktree add` failed under concurrent creation |
| 09-05 03:16:51 | requeued by hand |
| 09-05 05:59:04 | parked in `validating` again |
| 09-06 00:15:14 | requeued by hand ("stranded in validating — the terminal state no retry path can see") |
| 09-06 00:15:57 | parked in `validating` a third time |
| 09-06 15:26:37 | requeued by `kanban_requeue_reflex` — **kpr-stale-06** (`d7830f50a`), the fix this very row was the measured subject of |
| 09-06 15:27:36 | parked in `validating` a fourth time |
| 09-06 17:12:24 | requeued through `tools/kanban/requeue.py::requeue_task` (mfx-own-03, `ad38d7b31`) |
| 09-06 17:13:55 | dispatched — this session |

Four `worktree-isolation-guard` parks on one card, each on a transient
`git worktree add` failure, each leaving a `kanban/<id>` branch zero commits
ahead and a worktree directory that the orphan proof correctly refused to reap.
kpr-stale-06 named this row as its finding and now requeues that exact shape;
the fourth park happened AFTER its reflex requeued the third, so the guard's
underlying `git worktree add` race is still live and is not this card's to fix.

## What was NOT done, and why

- The detector, its 24h window and its `escalate`-outranks-`merge` rule are
  untouched: the verdict was correct, and an actuator never edits what it verifies.
- `tools/ci/pr_watcher.py` is a `protected_path`; the unlinked-door merge of a
  `token_exhausted` task's PR is recorded, not repaired.
- The local `rebase/rmf-ui-13` ref in the main checkout is the watcher's own
  rebase-attempt branch (head `bb7ce0d7e`, the merged branch head) and is left
  alone; the remote `kanban/rmf-ui-13` reads "ahead" forever because #2034 was
  squashed, which the delivery proof above answers.
- No `move done` POST from this session: `done` is merge-verified, and the
  pipeline closes the card once this record lands.

## Re-derive it

```
python - <<'PY'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'rmf-ui-13'])
PY
python -m tools.kanban.detector_findings --list --status cleared | grep rmf-ui-13
gh pr view 2034 --repo icdev-ai/icdev --json commits --jq '.commits[]|{oid:.oid[0:9],date:.committedDate,msg:.messageHeadline}'
git log origin/main --first-parent --format='%h %cI %s' --since=2026-09-03T10:00:00Z --until=2026-09-03T19:00:00Z
```
