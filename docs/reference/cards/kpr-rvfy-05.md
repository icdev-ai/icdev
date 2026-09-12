# A raw `gh pr merge` on a KANBAN-LINKED PR is refused (kpr-rvfy-05)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/hooks/fire_rate_survey.py --check gh_pr_merge_bypass --samples 10
python tools/hooks/fire_rate_survey.py --check gh_pr_merge_bypass --live-git
```

THE DOOR ALREADY EXISTED and nothing sent anyone to it. `tools/kanban/cli.py
--set-status <id> done --merge` routes through `tools/kanban/land.py`, whose
own comment says it "SATISFIES the done-gate instead of bypassing it": thirteen
checks -- pr_recorded, pr_readable, pr_open, base_is_default, mergeable,
ci_green, approved, no_changes_requested, no_sibling_conflict, draft_promoted,
enforced_done_gate, merge_requested, merge_confirmed -- and `done` is written
only after the forge CONFIRMS the merge, so `done` is a CONSEQUENCE of a landed
PR rather than an independent claim. A raw `gh pr merge` runs none of them.
Its ci_green is STRICTER than branch protection would be: it refuses a failed
check, checks STILL RUNNING, and an EMPTY rollup. That last one is the case --
on 2026-08-29 two of three self-hosted runners were dead and jobs QUEUED with
no red anywhere. Branch protection accepts a repo where no check ever
reported; this door does not. (Branch protection is also unavailable here:
403, Pro required on a private user-owned repo, and the trading domain means
icdev_ft stays private.)
MEASURED 2026-08-29: an operator session merged TWELVE PRs into icdev_ft with a
raw `gh pr merge`. Retroactively all twelve WERE green, on main and non-draft,
so no bad merge landed -- the exposure is that nothing would have stopped one,
and "the operator eyeballed the checks" is the `|| true` failure mode this file
already has a written rule against.
SCOPE IS THE WHOLE DESIGN: a LINKED PR only. Linkage is established three ways,
cheapest first -- the command names a `kanban/<id>` branch; it names NO PR and
HEAD is such a branch (the `cd <wt> &&` target, else the session's own
worktree, never os.getcwd()); or a PR NUMBER/URL resolves to such a head branch
through one bounded `gh pr view`. A branch literal is never looked up -- it IS
the head branch. An UNLINKED PR is ALWAYS allowed: it has no task row to mark
`done` and no board gate to satisfy, and this file's own worktree-first
workflow ends in `gh pr merge --merge` on a `feat/<slug>` branch.
SURVEYED BEFORE ARMING, over 111,968 real tool calls in 2,663 transcripts
(30 days, 2026-08-29). 723 calls invoke `gh pr merge` at all -- 0.65%, the HARD
upper bound, since the check can refuse nothing else. Replaying the real
predicate anchored on each call's own cwd: 113 REFUSED (0.1009%) and 610
ALLOWED. Every refusal is a raw merge on a `kanban/<id>` PR; the allowed set is
`fix/`, `feat/` and `$pr` loop variables. That is a sixteenth of the 1.63% this
file already calls refusing routine work, and below every armed check here bar
four. Re-derive with `--check gh_pr_merge_bypass`.
The replayed number is a LOWER bound and says why: a `cd <worktree> && gh pr
merge <n>` whose worktree has since been removed cannot resolve, so it replays
as allowed. Resolving every triggered selector against BOTH repos gives the
upper bound on linked merges, 339 (0.30%) -- still a fifth of 1.63%.
FAILS OPEN on every unknown: no `gh`, no network, an unexpanded `$pr`, an
unreadable HEAD. This guard narrows a door; it is not a boundary, and the
AUTOMATED path was never the hole (pr_watcher already refuses a non-READY PR,
and `land()` shells out directly rather than through a Bash tool call).
Kill switches, both auditable, NEVER a shell neutraliser:
  ICDEV_GH_PR_MERGE_GUARD=0          the whole check off
  ICDEV_GH_PR_MERGE_GUARD_OFFLINE=1  skip the forge lookup only; the offline
                                     signals still refuse
