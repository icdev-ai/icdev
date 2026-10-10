# A PR that IS eligible and STILL open — the merger has stalled (kpr-watch-02)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.ci.merge_stall                     # human table
python -m tools.ci.merge_stall --json
python -m tools.ci.merge_stall --gate              # exit 1 on an `alarm` ONLY
python -m tools.ci.merge_stall --survey            # re-derive the threshold from merge history
python -m tools.ci.merge_stall --stall-after 30    # override the threshold for one run
python -m tools.ci.merge_stall --no-record         # do not append observations
python -m tools.ci.merge_stall --from-json prs.json --default-branch main
```

`merge_readiness` explains every rung the ladder REFUSES on. This answers the
one case where it refuses NOTHING: a PR classified `ready` that is STILL open
on the next poll. Nothing is wrong with that PR — the actor should have merged
it and did not, which is an automation-liveness problem with a different repair.
Eligibility is asked by calling the SAME classify_merge_readiness with
`linked_urls=()`, so the `linked` short-circuit cannot hide the task path (where
3 of the 4 known causes live); ownership is carried apart as `door`. NO second
copy of the ladder — do not write one.
SEVERITY, not one "stuck" bucket: alarm (eligible, aged out, NOTHING explains
it) | outage (daemon down, or the forge refused this host's credentials —
reported with NO threshold and attributed ONCE to the fleet, never N times to N
innocent PRs) | by_design (sibling hold, enforced done-gate, landed hold,
protected path, auto-merge off, CI-still-running) | unmeasured | ok.
AGE has TWO sources, never merged and both always printed: `recorded` (the
append-only `pr_merge_eligibility_events`, written per TRANSITION of
(state, head_sha), so the newest row IS first-seen-ready — one indexed read, a
handful of rows a day) and `ci_estimate` (max statusCheckRollup completedAt, a
labelled PROXY that reads a PR whose hold cleared after green as instantly hours
old). A recorded row for a DIFFERENT head sha is refused: a force-push is a new
merge opportunity whose clock restarts. No source at all prints "?", never 0.
CAUSE ATTRIBUTION reuses `audit_trail` — 104,319 pr_watcher rows, 42,742 `wait`
rows already carrying the refusal's own reason. No new writer; the existing
record simply read. Patterns are DATA in args/merge_stall.yaml, every one taken
from a live row. FAIL-OPEN to `unattributed` — excusing a PR on missing evidence
is how an alarm goes quiet. Do NOT add a catch-all pattern.
SURVEYED BEFORE ARMING (150 merged PRs): the ENTIRE tail is attributed (n=30,
max 116.37 min — 17 done-gate, 12 sibling, 1 forge outage) while the
unattributed population (n=120) stops at 13.98. At 20 min an alarm that IGNORES
cause fires on 4.67% of routine merges; one that ATTRIBUTES first fires on
0.00%. That gap IS the design — CLAUDE.md already calls 1.63% grounds for
standing a check down. `stall_after_minutes: 20` not 15 (both 0.00%, but 15
leaves ONE minute of headroom above its own sample);
`by_design_stall_after_minutes: 180`, because a hold that can never escalate is
a category people stop reading. Re-measure with --survey; never raise a
threshold to quieten an alarm — an alarm here means the MERGER stopped.
READ-ONLY against the forge (only `gh pr list` / `gh auth status`, proven by
AST) and it writes exactly one table. pr_watcher records an observation each
poll beside its heartbeat: the heartbeat proves the WATCHER ran, this proves
what it was looking at.
