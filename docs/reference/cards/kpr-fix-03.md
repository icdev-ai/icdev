# Would that check have been RIGHT to refuse? Surveyed; answer is NO (kpr-fix-03)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.kanban.landed_dispatch_survey --json
python -m tools.kanban.landed_dispatch_survey --window-days 30
```

Replays all 6,218 recorded scheduler dispatches against origin/main and asks
what `KANBAN_LANDED_CHECK=enforce` would have DONE, split by whether it would
have been right: 9.20% refused, 6.53% correctly (the id was on main and
nothing more ever landed), 2.67% WRONGLY (29.0% of fires) because a further
commit carrying the id landed after the dispatch. 2.67% is above the 1.63%
the PreToolUse rule above already calls refusing routine work, so the check
STAYS `warn`. Three discriminators were tested and all three failed — landing
age (7-24% wrong in every band from <1min to >7d), evidence tier (36.4%
merge_ref, 25.5% subject) and repeat count (flat 27-37% from the 1st
re-dispatch to the 11th). The premise is what fails: "the id is on main" is
not "the task is delivered" — a task legitimately spans several commits
across several PRs. The signal for the case it was built for is PR IDENTITY
(a MERGED PR carrying the id while the task points at a different one), not a
threshold on this one. Report only; a survey with a --gate earns a `|| true`.
