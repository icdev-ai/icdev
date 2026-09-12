# CUI // SP-CTI

# task-det-7e346f293f — the NEEDED-A-HUMAN resolution, and what it derived

**Subject:** `qa-fail-5cacee65f1d03c8c`, PR #2231.
**Derived card:** `kpr-watch-19` — nothing drains the resume queue on the
executor that runs.
**Recorded:** 2026-09-12.

This is the derivation record the `kpr-watch-19` card cites. The card essay is
`docs/reference/cards/kpr-watch-19.md`; this file carries the measurements the
card was derived FROM, so a reader can check the reasoning rather than the
conclusion.

## 1. What was being resolved

`task-det-7e346f293f` was a detector finding against `qa-fail-5cacee65f1d03c8c`,
whose PR #2231 had gone to `NEEDED A HUMAN` after its resume budget emptied.
kpr-watch-13 had already established that a `pr_watcher.resume` audit row is a
sentence about a FILE WRITE phrased as a sentence about an agent, and had
shipped `tools/ci/resume_delivery.py` to make the difference measurable, plus
the early escalation that acts on an `undelivered` verdict (#2184).

The question this resolution asked was the next one: **now that we can measure
delivery, does anything actually deliver?**

## 2. The measurement

Taken on the live checkout and the live PG board, 2026-09-12.

```
ls .tmp/kanban/messages/*.jsonl | wc -l                                   212
grep -ho '"sender": "pr_watcher"' .tmp/kanban/messages/*.jsonl | wc -l    911
ls .tmp/kanban/message_receipts/ | wc -l                                    0
lifetime pr_watcher.resume audit rows                                     909
```

911 undrained messages on disk against 909 resumes ever recorded. A drain
DELETES the file, so a drain leaves no trace and cannot be counted directly —
but a count on disk that meets or exceeds every resume ever written proves none
was drained. **Zero receipts. No drain has ever happened on this deployment.**

## 3. Why — two independent causes, both structural

### 3.1 The one consumer is on the wrong executor

The only caller of `hook_compat.check_message_queue` is
`tools/genesis/reflexes/kanban.py:6668`, inside the text-only LLMRouter executor
loop.

```
grep -rl check_message_queue .claude/hooks/        -> (nothing)
tools/agents/adapters/claude_cli.py                -> never mentions a queue
```

Executor mix on the board:

| executor | tasks | share |
|---|---:|---:|
| `claude_cli` | 4,059 | 99.73% |
| `ollama_local` | 11 | 0.27% |

The drain site is unreachable for the executor that runs essentially every task.

### 3.2 The drain is mid-run; the traffic is post-run

`pr_watcher` only resumes a task whose PR is already OPEN, so every
`pr_watcher.resume` is enqueued after the dispatch that produced the PR has
finished. The subject task is the worked example:

| event | time (UTC) |
|---|---|
| dispatch produces PR #2231 | 22:45:33Z |
| `pr_watcher.resume` written | 23:04:45Z |
| gap | **19m 12s** |

So even on the router path, a mid-run-only drain would have delivered none of
this traffic. 0% delivery was structural on BOTH paths, not a scheduling
accident.

## 4. The measurement trap found alongside it

`python -m tools.ci.resume_delivery --task <id>` run FROM A WORKTREE answered
`unmeasured -- queue empty and no drain receipt`, because
`hook_compat.MESSAGE_QUEUE_DIR` was
`Path(__file__).resolve().parent.parent.parent / ".tmp" / "kanban" / "messages"`
— a self-root (xit-decl-03), which in a worktree is the WORKTREE, while the
watcher enqueues into the main checkout.

Reproduced on `aca-hyg-06-d4-d3`, minutes apart:

```
main checkout : undelivered -- 5 pr_watcher message(s) still unread in the queue
a worktree    : unmeasured  -- queue empty and no drain receipt
```

A worker diagnosing its own undelivered resume, from its own worktree, was told
there was nothing pending.

Resolving from `__file__` is correct for an in-repo tool. The defect is that the
queue is a PER-CHECKOUT path shared between two processes with different
checkouts — the same shape `tools/kanban/build_mode.py::_main_checkout` had
already fixed for the Manual Build flag.

Confirming the split had only ever cost reads, never writes: `find` over
`.tmp/worktrees` for `*/.tmp/kanban/messages` returned **0 directories**. Nothing
had ever enqueued into a worktree — consistent with "no consumer ever ran".

## 5. What was rejected

* **Deleting the queue, or trimming it.** Makes the gap read smaller with no
  message read. Explicitly out of bounds.
* **Quietening the escalation, or moving a threshold** in
  `args/pr_watcher_config.yaml`. Same objection.
* **A `Stop`-hook block to hold a session open** so a post-run resume has
  something to land in. This is an ACTUATOR: it changes how long sessions run
  and therefore the dispatch rate, and this repo's standing rule (exa-bench-05)
  is that arming such a thing owes a fire-rate survey FIRST. Delivery was built
  into windows that already exist; if the post-run window proves insufficient in
  practice, that survey is the next card, not a line in this one.
* **A "dead letter" bucket in the survey** — see §7.

## 6. What was built

See `docs/reference/cards/kpr-watch-19.md`. In one line: the queue is anchored
to the main checkout for every reader and writer, and `PostToolUse` /
`SessionStart` hooks drain it for `ICDEV_DISPATCH_TASK_ID` and put the words in
front of the model.

Verified live on task `kpr-watch-19`: a resume enqueued at 13:53:39Z through the
same `queue_message` seam `_send_resume` calls was in the session's context on
the next tool call, and `resume_delivery --task kpr-watch-19` reported
`delivered`, `receipted 1` — **identically from the worktree and from the main
checkout**.

## 7. The finding that changes the premise

The 911 were expected to shrink once a consumer existed. They cannot, and this
is the measurement that says so:

| status of the task holding the mail | tasks | messages |
|---|---:|---:|
| `done` | 210 | 910 |
| `pr_opened` | 1 | 1 |

**99.9% of the backlog is addressed to tasks that have finished.** The scheduler
does not re-dispatch a `done` task, so there is no future session for those
messages to be delivered to. Delivery — the only sanctioned way the number may
fall — cannot reach them, and deletion is forbidden. The number stays 911.

Nothing was reclassified to make that read better. Adding a `dead_letter`
verdict to `resume_delivery.survey()` would drop the headline from 911 to 1
without a single extra message being read; that is the move the acceptance
criteria forbid, and the same move CLAUDE.md forbids for every census ceiling in
the repo.

**The unfixed half is at the WRITE side.** `pr_watcher` enqueues a resume for a
task it has no way to reach, and nothing tells it so — which also means its
`max_resume_cycles_per_task` budget is being spent against tasks where no cycle
could ever land. That deserves its own card: it needs a survey of how often a
resume is addressed to a terminal task, and a decision about what the watcher
should do instead (skip, escalate immediately, or resume the PR rather than the
task). It must not be smuggled in behind a delivery fix.

## 8. Re-derive every number here

```
python -m tools.ci.resume_delivery --survey
python -m tools.ci.resume_delivery --task <task-id> --json
ls .tmp/kanban/messages/*.jsonl | wc -l
grep -ho '"sender": "pr_watcher"' .tmp/kanban/messages/*.jsonl | wc -l
ls .tmp/kanban/message_receipts/ | wc -l
```
