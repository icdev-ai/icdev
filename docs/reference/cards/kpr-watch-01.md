# Which open PRs are awaiting merge, and WHY is each one not merging? (kpr-watch-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.ci.merge_readiness --json          # every open PR, task-linked or not
python -m tools.ci.merge_readiness                 # human table
python -m tools.ci.merge_readiness --state awaiting_ci --state conflicting
python -m tools.ci.merge_readiness --from-json prs.json --default-branch main
python -m tools.ci.merge_readiness --state behind_main       # the stale ones
python -m tools.ci.merge_readiness --no-measure-behind       # skip the /compare calls
python -m tools.ci.merge_readiness --group                   # bucketed by state
python tools/kanban/cli.py --awaiting-merge                  # same view, board CLI
python tools/kanban/cli.py --awaiting-merge --merge-state behind_main --json
```

UI: Home (/) -> "Awaiting Merge"   API: GET /api/merge-readiness (no POST sibling)
READ-ONLY: it never merges, pushes, un-drafts or closes. `pr_watcher`'s unlinked
sweep and this report read the SAME pure function,
classify_merge_readiness(pr, *, default_branch, linked_urls, behind_by) ->
(state, reason), so the report can never describe a merge policy the merger
does not have. Do NOT write a second copy of the ladder. States: merged |
linked | draft | held_label | wrong_base | conflicting | no_checks |
ci_failed | awaiting_ci | changes_requested | behind_main | ready.
`no_checks` (empty rollup) is never merged into `awaiting_ci`, and
mergeable=UNKNOWN reports a different REASON from CONFLICTING. Exit 2 = the
report could not be produced, never an empty table.

`behind_main` (kpr-stale-02) is THE SAFETY HOLE this ladder was missing.
`mergeable` answers only "does this collide TEXTUALLY", and GitHub reports
MERGEABLE for a branch arbitrarily far behind main — so the CONFLICTING
interlock caught only the colliding subset and the rest merged CLEANLY,
re-applying their diff over a tree that had moved on (#1651: -38/+26 on
rest_v1.py, 36 commits behind). Do NOT key this on `mergeStateStatus` alone:
it is BEHIND only where the base branch has `required_status_checks.strict`,
which this repo does NOT — measured 2026-08-18, it reads CLEAN for a branch
217 commits behind. The forge verdict is the belt; the measured count from
`measure_behind_by` (the /compare endpoint, NOT a local git rev-list, which
understates staleness whenever origin/main is itself stale) is the check.
Threshold `max_behind_commits: 10` in args/pr_watcher_config.yaml, surveyed:
over 120 merged PRs the routine population tops out at 8 behind at merge
(p50 1, p90 5, p95 6), so it fires on 0 of them and on #1651.
Measured LAST BY THE MERGER, and only for a PR every cheaper rung already
passed — it is the one rung that costs a forge round-trip. UNMEASURED is
`None` and prints "?", NEVER 0, and is FAIL-OPEN: a forge that cannot answer
must not freeze the pipeline. The repair differs by door — a task-linked
kanban/<id> branch goes to the existing `_maybe_rebase` path (same ownership
refusal, same per-base-era budget) and raises a HITL alert when that
declines; an UNLINKED PR is reported and LEFT ALONE, because the sweep never
pushes.

THE REPORT MEASURES EVERY OPEN PR — the merger's rung is NOT the report's
(rem-hyg-12). `collect_report` used to `/compare` only the urls the ladder
had already called `ready`, which is right for the MERGER (a non-ready PR
will not merge, so the count cannot change its verdict) and WRONG for the
human report, which is what somebody reads BEFORE deciding to un-draft or
merge something. Measured 2026-08-20: #1850 sat in AWAITING MERGE as a
`draft`, MERGEABLE, mergeStateStatus=CLEAN, 13 commits behind main, with a
diff against main of +97/-1691 — one un-draft away from deleting
`posture.py` (rem-hyg-09), `cortex/metrics.py` (ctx-obs-03) and
`kanban_project_sync.py` (rem-hyg-08). #1845 was `linked` and 16 behind,
which is why its red-first proof compared against an ancient merge base.
Both short-circuited the ladder BEFORE the staleness rung, so neither was
ever measured.
THE LADDER IS NOT REORDERED and no merge verdict moves: every rung above the
staleness one short-circuits, so handing it a count it previously lacked
cannot change a non-ready PR's `state`. That equivalence is ASSERTED, and it
is what proves the cost optimisation was removed from the REPORT and not
from the MERGER — `pr_watcher` keeps its own lazy probe, untouched.
STALENESS IS A THIRD AXIS, beside `state` and `pipeline_state` and never
inside them, because a `draft` PR's state can NEVER be `behind_main` — the
ladder refuses it earlier, correctly. `staleness()` returns `stale` +
`stale_reason`; `stale` is `None`, NEVER `False`, when the count was not
measured, and `stale_count` / `stale_unmeasured_count` are reported as TWO
numbers so "2 stale" cannot be read over a board where five PRs were never
compared. The flat table marks a stale row "!", the grouped view (which has
no BEHIND column, and is what the kanban CLI and the dashboard read) prints
a STALE line under it, and the panel renders an `mr-stale` badge gated on
`r.stale === true` rather than on truthiness.
COST: one /compare per DISTINCT (base, head sha) rather than per ready PR —
~15 calls on a normal board against a 5,000/hr budget, behind the panel's
120s cache. `--no-measure-behind` turns it off, and then every PR reports
UNMEASURED rather than fresh.

SURFACED (kpr-watch-03) — a report nobody opens is not observability, and for
two cards the only place this answer existed was a CLI somebody had to think
to run. Now: the "Awaiting Merge" panel on Home (a section inside
templates/_autonomy_status.html, NOT a new page, so the 8-point page gate does
not apply) and `python tools/kanban/cli.py --awaiting-merge`. Both read
`collect_report`, the SAME gatherer `python -m tools.ci.merge_readiness`
reads — one ladder, one gatherer, three surfaces.
READ ONLY BY CONSTRUCTION: no merge button, no un-draft, and the route is GET
with no POST sibling (asserted in tests/test_merge_readiness_surface.py).
TWO VERDICTS PER PR, FROM ONE TABLE. `state` is the merger's, unchanged and
authoritative. `pipeline_state` is the same function called with
`linked_urls=()` — "why would this not merge, setting aside who owns it" —
because `state` short-circuits at the `linked` rung for EVERY kanban PR,
which is the exact population the panel is for; grouping on it collapsed the
board into one bucket labelled "a task owns it". For an UNLINKED PR the two
are identical by construction and a test asserts it, which is what proves
this is not a second copy of the ladder.
AGE IS A LOWER BOUND, and it is NOT `updatedAt`. Nothing persists a state
transition, so the age is measured from the NEWEST observable event on the
PR and reported as "it has been like this at LEAST this long". `updatedAt`
does not bump when a check completes — measured 2026-08-19, PR #1817 reported
updatedAt=01:10:24Z while its own check completed at 01:11:09Z, 45s LATER —
so keying on it alone overstates the age, in the direction that makes a stuck
pipeline look MORE stuck. `gh` renders an unfinished check's timestamp as the
Go zero value, which parses to a real year-1 datetime; it is rejected, not
min()-ed away. Unmeasured is `None` and prints "?", NEVER 0.
Groups are in ATTENTION order (`ready`, `behind_main` first — the two states
the card names), which is deliberately NOT the ladder order; presentation
only, in ATTENTION_ORDER beside the ladder, and it never reorders the ladder.
The panel hides ONLY on a known-empty board: a failed report stays visible and
says why, because a panel that disappears when it breaks is indistinguishable
from a clean board. Cached 120s server-side (a `gh pr list` plus a /compare
per ready PR), and the cache AGE is rendered — never presented as live.
