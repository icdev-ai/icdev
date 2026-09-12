# A worktree HUSK with no .git marker is provably dead -- swept on a clock of HOURS (mfx-own-04)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.kanban.worktree_husks --survey [--json]        # every .git-less dir under the live roots
python -m tools.kanban.worktree_husks --plan                   # what a sweep would act on; acts on nothing
python -m tools.kanban.worktree_husks --apply <task-id> --dry-run
python -m tools.kanban.worktree_husks --apply <task-id>        # prove -> audit -> ONE rmtree -> confirm
```

MEASURED 2026-09-06: task-det-e9a2e3ea16 sat in `validating` behind a 534 MB
`.tmp/worktrees/<id>` with NO .git file -- unregistered, so the empty-checkout
proof refused it (`worktree_unregistered`, correctly: `git status` inside an
unregistered dir describes the ENCLOSING checkout) -- while
`kanban_requeue_reflex` reported 60/60 successes. THE GAP IS THE CLOCK, and
re-reading the sweeper, also the CANDIDATE SET: `_sweep_candidates` returns
only `.git` carriers and `_worktree_is_disposable` refuses "entries but no
.git" by design, so the 7-day KANBAN_WORKTREE_STALE_AGE_DAYS rule could never
reach a husk at all. A live worktree ALWAYS carries `.git` (`git worktree add`
writes it first), so a husk cannot be one; the questions the 7-day path asks
(uncommitted? unpushed?) cannot be asked of it -- which is why it is safe and
why it is NEVER widened to a directory carrying `.git` (pinned by AST test).
THE CLASS, every answer required: a DIRECT child of .tmp/worktrees (the one
layout where the name IS the task id; the nested sanctioned root is SURVEYED
by name, never acted on); no `.git`; absent from a SUCCESSFUL `git worktree
list`; a BOARD ROW (`.tmp/worktrees/data` and `/tools` have none -- residue
of a root-computing bug, one still being written to 12h before the survey);
not `in_progress`; and the NEWEST mtime in the WHOLE tree older than
`husk_age_hours` (args/worktree_husk_sweep.yaml, 6; KANBAN_WORKTREE_HUSK_AGE_HOURS
overrides) -- the top-level stamp is not enough on Windows, and a process
still writing .logs/*.ndjson deep inside is the one sign of life a husk can
show. A walk over budget is `age_unmeasurable` and refuses: a partial walk
OVER-estimates the age, the direction that deletes. Unreadable is None; None
never acts. prove -> audit (`worktree_cleaned` / `husk_sweep.remove.intent`,
raise_on_error=True; NO ROW, NO ACT) -> one rmtree (read-only bits cleared,
extended-length paths -- the `<id>/node_modules`-only residue measured is what
an `ignore_errors` rmtree leaves) -> confirm. `max_removals_per_run: 3`,
OLDEST FIRST, deferred BY NAME (a walk costs 13-52s on a 20k-file husk).
SURVEYED BEFORE ARMING: 26 husks, 6.0 GB, 25 `done` + 1 `validating`,
0 `in_progress`; 2 refused (no board row); 14 husk-shaped dirs under the
sanctioned root with no task id, reported and left. The card's own husk was
then removed THROUGH THE ACT (proven, audit row, 534 MB, confirmed gone) --
the operator had rightly declined a manual `rm -rf` the same day.
Consumed by `_sweep_old_worktrees`; kill switch KANBAN_WORKTREE_HUSK_SWEEP=0.
Survey: docs/audits/mfx-own-04-worktree-husk-survey.md -- which also records
that this card's FIRST test run pointed the 7-day sweeper at a temp base
with the LIVE sanctioned-root resolver unpatched and `max_age_days=0`, and it
removed four clean, pushed worktrees (two of them icdev_ft's, through the
orphan branch: the disposability predicate is repo-agnostic and the remover
is not). All four were re-created at the same path/branch/commit; the test
now pins EVERY seam the sweeper reads. A force-remover under test needs every
root it walks pointed at the fixture, not just the one you are testing.
