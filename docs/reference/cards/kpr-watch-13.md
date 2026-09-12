# Did that resume REACH anything, or was a line just written? (kpr-watch-13)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.ci.resume_delivery --survey
python -m tools.ci.resume_delivery --survey --json
python -m tools.ci.resume_delivery --task kpr-dup-03            # one task's verdict
python -m tools.ci.resume_delivery --task kpr-dup-03 --json
```

Run it from the checkout pr_watcher runs in: the queue is PER-CHECKOUT
(hook_compat resolves it from its own file), so a worktree reads its own empty
.tmp and correctly reports `unmeasured` rather than a clean zero.

`pr_watcher._send_resume` calls `hook_compat.queue_message`, which APPENDS ONE
JSONL LINE to `.tmp/kanban/messages/<task-id>.jsonl` and returns. The audit row
said `injected resume context` -- a sentence about a file write phrased as a
sentence about an agent. MEASURED 2026-09-06 02:59 UTC on the live checkout and
the live PG board:
  187 queue files / 186 holding UNDRAINED `sender: pr_watcher` lines
  852 undrained pr_watcher messages
  850 lifetime `pr_watcher.resume` audit rows (2026-08-01 -> 2026-09-06)
A drain DELETES the file, so a drain is traceless and cannot be counted -- but
the count ON DISK MEETS OR EXCEEDS the count EVER RECORDED, so no recorded
resume has been drained. Both sides move on a live board (186/849/847 six hours
earlier); the INEQUALITY carries the argument. Quote a reading with its instant.
The only drain, `check_message_queue`, has one runtime call site --
tools/genesis/reflexes/kanban.py, inside `_dispatch_via_llm_router`'s
per-iteration loop -- which runs only WHILE A DISPATCH IS IN FLIGHT (by
definition not the state of a task whose PR is open and whose worker exited,
the only state a resume is sent in) and is the LLM-ROUTER FALLBACK; the primary
`claude_cli` adapter never calls it at all.

SO `max_resume_cycles_per_task: 5` WAS SPENDING FIVE ATTEMPTS THAT WERE NEVER
MADE, and `RESUME_COOLDOWN_SECONDS = 600` was spacing a write to a dead file.
Corroborated on fni-api-01 (icdev_ft#340): the branch head sha did not move
once across resumes 1-5; it moved at 16:48:29Z, 17m01s after the FIFTH resume
and 15m49s AFTER the escalation.

THREE VERDICTS, and `unmeasured` is NEVER folded into either other:
  undelivered  a `sender: pr_watcher` line for this task is STILL IN THE QUEUE.
               Proven unread. THE FINDING.
  delivered    a DRAIN RECEIPT records it. `check_message_queue` now appends one
               line to `.tmp/kanban/message_receipts/<task-id>.jsonl` naming what
               it drained, who drained it and when -- so from this change forward
               a drain STOPS being traceless. Best-effort: a receipt that cannot
               be written must never break the drain a running agent depends on.
  unmeasured   the FIRST injection for a task (nothing prior to judge); or the
               queue file is gone with NO receipt -- a pre-receipt reader, a
               `.tmp` sweep and a worktree's own empty `.tmp` are
               indistinguishable. NOT a clean bill of health.
`.tmp` is disposable BY DESIGN, so every way the evidence can go missing lands in
`unmeasured`. A sweep can make this say "I cannot tell"; it can never make it say
"delivered". `never_drained` is None -- NEVER False -- on a shortfall alone,
because "fewer on disk than recorded" is exactly what a sweep produces.
The verdict is re-derived from the FILESYSTEM and shares NO CODE with
`queue_message`, whose `{"queued": True}` only ever meant `write()` did not raise
-- one computation trusted twice is the defect, not the fix.

WHAT THE AUDIT ROW SAYS NOW. A `resume` row carries `delivery` /
`delivery_detail` and reads `resume enqueued; prior injections: undelivered (N
pr_watcher message(s) still unread in the queue)`. It never says `injected
resume context` again. The ESCALATION -- the one place the whole budget is
knowable, and the one place it is quoted to a human -- reads `resume cap reached
(5/5) - manual intervention required; NONE of the 5 injection(s) were ever read`.
`unaccounted` (pre-receipt residue) is reported BESIDE `delivered`, never inside
it. The HITL alert keeps the `resume cap reached (n/m) after <cause>.` prefix
tools/kanban/hitl_alert_view.py parses; the clause is appended AFTER it.

NO ACTUATOR, ON PURPOSE, and pinned by an AST test. Nothing here re-dispatches a
worker, promotes a wake, deletes a message, or reads
`max_resume_cycles_per_task` / `RESUME_COOLDOWN_SECONDS`. MORE UNDELIVERED
MESSAGES IS NOT MORE ATTEMPTS, and a delivery path that re-dispatches on every
resume is a dispatch-rate change owing its own fire-rate survey -- on this board
it would fire on 100% of resumes on day one. The 852 messages already on disk
are NOT deleted: they are the evidence.
`_emit_wake_events` is a SECOND, INDEPENDENT channel and stays unmeasured: its
own docstring says "an empty `promoted` is the normal case -- most events have no
listener". Measure `promoted` before relying on it.

SURVEYED BEFORE WIRING, over the WHOLE resume corpus replayed through the
SHIPPED predicate (never a second copy): 850 rows, 850 `undelivered`, 100.00%.
That is the MEASUREMENT, not a threshold -- it is total because the finding is
true of every message on the board, and it is why nothing here REFUSES anything.
Cost: 0.112 ms per probe (mean over 850) and 0.367 ms per receipt (mean over
200), against a ~45 s poll.

THE FIFTH ATTEMPT'S ONE-POLL GRACE IS A DECISION, NOT AN ACCIDENT OF BRANCH
ORDER. The cooldown spaces injections 1->5 and does NOT protect the fifth: the
escalate branch fires on the first poll where `cycle >= max_cycles`. Replaying
all 153 lifetime cap escalations (150 with a preceding resume row), FINAL resume
-> escalate: min 32.0s p50 40.9s p90 60.0s max 715.1s, 148/150 (98.7%) within
180s and 149/150 (99.3%) within the 600s cooldown -- inside the very interval
that constant was written to rule out. Its cost is BOUNDED, NOT PROVEN: 109/153
(71.2%) have a later `pr_watcher.merge`, median 238.2 min, so most are not a
fifth resume finishing late -- but 24 (15.7%) merge within an hour and 4 within
30 min. KEPT AS IS: holding the escalation for a full cooldown delays EVERY HITL
alert ~10 minutes to rescue an attempt that was never delivered at all, and
moving a threshold to quieten a symptom is forbidden. The repair is DELIVERY.
The cost is now RECORDED on every escalation as `final_attempt_grace_seconds`
instead of argued about once, so the next card re-derives it from a live series.
Report only, no --gate (kpr-fix-03). Exit 2 = the survey could not be produced,
which is never the same as a clean survey.
Survey: docs/audits/kpr-watch-13-resume-delivery-survey.md
