# A daemon's reload watch set is what it EXECUTES, not what it had imported at start (autonomy-id-06)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m pytest tests/genesis/test_code_reload.py -q
grep "lazily imported" .logs/tools.genesis.code_reload.ndjson | tail -3   # a reflex joining the watch set
python tools/awareness/restore_acts.py --plan --json                        # a daemon still `stale` on a reflex fix = adoption did not happen
```

`DaemonBase.run_forever` took `code_reload.snapshot()` ONCE at start, every
genesis reflex is imported lazily on its first dispatch, and `changed_files`
compares only paths in BOTH snapshots (by design -- counting a new path as a
change was the scheduler's once-a-minute restart loop). Nothing ever ADDED a
path to the baseline, so "not a change" was silently "not watched" for the
life of the process. MEASURED 2026-09-06: 30 modules in the daemon's start-up
watch set, 0 of its 101 reflexes; #2146's fix to reflexes/kanban.py re-exec'd
the scheduler (imports that reflex at startup) two minutes after merge while
the daemon ran the pre-fix reflex until a human applied restart_stale_daemon.
`adopt_new_imports` records a path new in the current snapshot into the
baseline AT ITS CURRENT MTIME -- still not a change, so a first import never
restarts -- and the next rewrite is seen. 134 files join on dispatch (101
reflexes + 33 transitive imports); the old rule reported 0 changes across them.
It runs BEFORE `pull_if_safe`, on purpose: adopting after the pull records the
post-pull mtime and buries the change the pull delivered. Residual, named: a
pull by SOMEBODY ELSE on the shared checkout between a module's first import
and the end of that cycle -- one cycle per module, once per process, against
never. Survey: docs/audits/autonomy-id-06-lazy-import-watch-set.md
