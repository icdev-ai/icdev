# The worktree-add budget is REAL, and the checkout is parallel (kph-repark-kph-repark-mfx-ci-04)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m pytest tests/kanban/test_worktree_add_budget_is_real.py -q
grep -h "Created worktree for" .logs/tools.genesis.reflexes.kanban.ndjson | tail -5   # each line now carries "in N.Ns (budget 30s)"
```

MEASURED 2026-09-06 12:42-15:14Z: twelve `worktree-isolation-guard` parks over
seven tasks, every one "git worktree add ... timed out after 30 seconds", and
every leftover on disk a COMPLETE, CLEAN 563 MB checkout. The budget was a
fiction: `git worktree add` checks out in a CHILD (`git reset --hard`), and
`subprocess.run(timeout=30)` killed the parent then BLOCKED on the pipes that
child had inherited until it finished -- branch created 14:32:54Z, "timed out"
logged 14:34:49Z, 115s later, over a finished worktree. The guard parked a task
whose worktree existed, and the orphaned checkout starved the next add.
Two things changed and NEITHER is a longer budget or a retry (the card forbids
both): the add runs `-c checkout.workers=0` (33.1s / 18.7s -> 8.5s on this
host, same tree, same minute; the `-c` reaches the child reset through
GIT_CONFIG_PARAMETERS), and on expiry the WHOLE process tree is killed
(`_kill_process_tree`, taskkill /T or killpg in its own session) and the
partial worktree, registration and branch are removed, so the park describes
what is on disk. WORKTREE_ADD_TIMEOUT_SECONDS stays 30 and is pinned by test.
NOT fixed there, and named: 21,400 files / 513 MB per worktree, of which
playwright-report/ was 209.9 MB (40.9%, 1,426 tracked files added in a bulk
chore on 2026-05-25 and in no .gitignore) -- every add wrote a test report
nobody reads from a worktree. UNTRACKED by task-wt-20f94d17, with backups/
(28.3 MB of canvas .db.bak / nc-backup zips nothing reads from git): an add
of main measured the same minute, checkout.workers=0, 2026-09-07T19:47Z,
went 21,455 files / 513.7 MB -> 19,968 files / 275.2 MB (-238.5 MB); the
origin/main add took 73.7s under dispatch load, over the 30s budget, and the
untracked tree 10.3s / 18.4s. tests/test_generated_artifacts_untracked.py
refuses a tracked file under either. Still open: the genesis daemon's
`kanban` reflex and the standalone scheduler both dispatch, so two adds can
run at once; the task lease keeps them off the same card, not off the same
disk.
