# CUI // SP-CTI

# kpr-watch-19 — nothing drained the resume queue on the executor that runs

**911 undrained messages, 212 queue files, 0 receipts. No drain had ever
happened.** Measured 2026-09-12 on the live checkout and the live PG board.

## The defect, and why it was structural rather than statistical

kpr-watch-13 proved a `pr_watcher.resume` was never READ, and shipped the early
escalation that acts on it (#2184). It did not make delivery work.

The only consumer of `.tmp/kanban/messages/<task>.jsonl` was
`tools/genesis/reflexes/kanban.py:6668`, inside the text-only LLMRouter executor
loop. `grep -rl check_message_queue .claude/hooks/` returned nothing, and
`tools/agents/adapters/claude_cli.py` never mentioned a queue. Executor mix on
the board that day: **4,059 of 4,070 tasks (99.73%) `claude_cli`**, 11
`ollama_local`. The one drain site was unreachable for the executor that runs
essentially every task.

And even on the router path the drain is MID-RUN only, while every
`pr_watcher.resume` is enqueued POST-RUN — the watcher only resumes a task whose
PR is already open. For `qa-fail-5cacee65f1d03c8c`, dispatch produced #2231 at
22:45:33Z and the resume was written at 23:04:45Z, nineteen minutes later. So 0%
delivery was structural on BOTH executor paths.

## The second half: one queue, two processes, different checkouts

`MESSAGE_QUEUE_DIR` was `BASE_DIR / ".tmp" / "kanban" / "messages"` with
`BASE_DIR` a self-root (xit-decl-03) — it answers "which copy of this file am
I", and in a git worktree that is the worktree. `pr_watcher` runs in the main
checkout and enqueues there; a dispatched worker lives in a worktree and looked
there. Two directories, neither call erring.

MEASURED on task `aca-hyg-06-d4-d3`, the same afternoon, minutes apart:

```
main checkout : undelivered -- 5 pr_watcher message(s) still unread in the queue
a worktree    : unmeasured  -- queue empty and no drain receipt
```

A worker diagnosing its own undelivered resume was told there was nothing
pending. **Fixing only the consumer still misses, because the hook runs inside
the worktree.**

This is the same defect `tools/kanban/build_mode.py::_main_checkout` already
fixed for the Manual Build flag ("reported MANUAL from C:/AI/ICDev and AUTOMATIC
from a worktree of it, in the same minute"). Same argument, same fix.

## What was built

* `tools/airgap/hook_compat.py::message_queue_dir(anchor)` — the queue is the
  MAIN checkout's, whoever asks. Resolution: `ICDEV_MESSAGE_QUEUE_DIR`, then the
  main checkout the anchor is linked to, then the anchor itself. The resolver is
  `shared_checks.main_checkout` (promoted from `_main_checkout`), which reads the
  `gitdir:` line out of a linked worktree's `.git` FILE rather than shelling out
  — this is imported on hook paths, and a git subprocess per import is a cost
  they cannot justify.
* `tools/hooks/resume_inbox.py` — the drain seam the Claude Code hooks call, so
  the queue finally has a consumer on the path 99.73% of dispatches take.
* Two windows, because a resume can arrive in either. **`PostToolUse`** is
  mid-run: a message queued while the session lives is delivered within one tool
  call, as `hookSpecificOutput.additionalContext`. **`SessionStart`** is the
  task's NEXT session, and that is the window that pays for `pr_watcher`
  traffic, all of which is enqueued post-run.

Both windows were **verified to reach the model on this deployment before either
was wired** — a throwaway probe in a live session, not an inference from
documentation. That ordering matters: a delivery path assumed to work is the
same class of claim as an audit row that says `injected resume context`.

## What it deliberately does not do

`deliver()` returns the CONTENT and the caller must put it in front of the
model. A drain that recorded a receipt and dropped the words would be
kpr-watch-13's defect wearing this card's name. A FAILED drain reports
`delivered == 0` and sets `error`; it never leaves a receipt for words nobody
got. The receipt stays `check_message_queue`'s — one drain, one unlink, one
receipt.

**NO ACTUATOR.** Nothing re-dispatches a worker, blocks a `Stop` to hold a
session open, or extends a run to manufacture a window. More delivery attempts
is a dispatch-rate change and owes its own fire-rate survey first (exa-bench-05).
This delivers into windows that already exist.

`ICDEV_RESUME_INBOX=0` stands the inbox down **without draining** — a switch
that ate the message would be worse than the defect, because the queue would
then read `delivered`. It is deliberately NOT under `ICDEV_SESSION_START_HOOK`,
which quietens the memory index: losing a resume because somebody turned down a
context block is the silent-miss class this card exists to end.

## Cost

The per-tool-call fast path is one env read plus one stat. It re-spells the
queue path with stdlib only, because `import tools.*` costs ~137ms measured (40ms
bare interpreter, 177ms with the package shim) and the overwhelmingly common
answer is "no mail". Measured against the hook's existing ~200-230ms, the
addition is not distinguishable from run-to-run variance. The cost of that
bargain is a second spelling of the path; the mitigation is
`tests/ci/test_resume_inbox.py`, which asserts the two resolve to the same
directory — the same bargain the `_CAPTURE_TOOLS` literal in that file already
strikes with `observation_capture`.

## Proven, on a real task

Live session `kpr-watch-19`, 2026-09-12. A `pr_watcher` resume enqueued through
`queue_message` — the exact seam `pr_watcher._send_resume` calls — at 13:53:39Z
was delivered into that session's context on the next tool call, drained at
13:53:40Z, and `resume_delivery --task kpr-watch-19` then reported:

```
verdict   delivered
detail    1 pr_watcher message(s) drained, newest 2026-09-12T13:53:40.331113+00:00
pending   0      receipted  1
```

reported **identically from the worktree and from the main checkout** — the two
spellings that disagreed before. `never drained` moved `True -> False` and the
first drain receipts on this deployment exist.

## THE BACKLOG CANNOT DRAIN, AND THE REASON IS NOT THIS MECHANISM

The 911 were expected to shrink once a consumer existed. They did not, and the
measurement says why — **210 of the 211 tasks holding mail, and 910 of the 911
messages, are addressed to tasks whose status is `done`**; the remaining one
(`xrv-cost-05`) is `pr_opened`. The scheduler does not re-dispatch a `done` task,
so those messages have no future session to be delivered to. They are dead
letters, and delivery — the only sanctioned way the number may fall — cannot
reach them.

Nothing here reclassifies them. The detector, `summarize_recovery`, the
`args/pr_watcher_config.yaml` thresholds and the escalation text are untouched;
adding a "dead letter" bucket would make the headline read smaller without a
single extra message being read, which is the move this card's own acceptance
criteria forbid. The number stays 911 and stays honest.

**The unfixed half is at the WRITE side, not the read side:** `pr_watcher`
enqueues a resume for a task it has no way to reach, and nothing tells it so.
That is a separate card — it needs its own survey of how often a resume is
addressed to a terminal task, and a decision about what the watcher should do
instead — and it must not be smuggled in behind a delivery fix.

## Re-derive it

```
python -m tools.ci.resume_delivery --survey
python -m tools.ci.resume_delivery --task <task-id> --json   # same from a worktree
ls .tmp/kanban/messages/*.jsonl | wc -l
grep -ho '"sender": "pr_watcher"' .tmp/kanban/messages/*.jsonl | wc -l
ls .tmp/kanban/message_receipts/ | wc -l
```
