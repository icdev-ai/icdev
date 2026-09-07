# autonomy-id-06 — the genesis daemon's self-reload could not see a fix to a reflex

**Verdict:** the daemon's reload watch set was the modules imported by the end of
start-up, and a reflex is imported on its first dispatch, so NO reflex was ever
watched. Fixed by adopting lazily imported files into the baseline as they
arrive (`tools/genesis/code_reload.py::adopt_new_imports`).

## The incident (2026-09-06, record: kph-repark-task-det-e9a2e3ea16-resolution.md)

| event | time (UTC) |
|---|---|
| #2146 (fix to `tools/genesis/reflexes/kanban.py`) merged | 16:17:04 |
| kanban scheduler pulled and re-exec'd — the only `code_reload` decision that day | 16:19:20 |
| genesis daemon (pid 12180, started 05:44:03) — no reload decision logged | — |
| its stale `kanban` reflex parked kph-repark-task-det-e9a2e3ea16 with the pre-fix shape | 16:48–16:50 |
| `restore_acts --apply restart_stale_daemon` restored it by hand (audit_trail 141359) | later |

The scheduler imports the kanban reflex at start-up; the daemon imports every
reflex lazily. That single difference is why one process saw the merge and the
other did not.

## The mechanism

`DaemonBase.run_forever` takes `code_reload.snapshot()` ONCE before its loop.
`changed_files(before, after)` reports only paths present in BOTH snapshots whose
mtime moved — by design, because counting a path new in `after` as a change turned
the scheduler into a once-a-minute restart loop (its docstring records that).
Nothing ever ADDED a new path to the baseline, so "not a change" silently became
"not watched", for the life of the process.

## Survey — what the start-up watch set contains

Re-derive with the probe below (a process that has imported `tools.genesis.daemon`
the way `main()` does, then imports every reflex the way dispatch does):

```python
from tools.genesis import daemon, code_reload as cr
from pathlib import Path
import importlib
snap = cr.snapshot(); root = Path(cr._repo_root())
files = {n: str((root / "tools/genesis/reflexes" / f"{n}.py").resolve())
         for n in daemon.REFLEX_NAMES}
print(len(snap), sum(p in snap for p in files.values()))
for n in files:
    importlib.import_module(f"tools.genesis.reflexes.{n}")
snap2 = cr.snapshot(); base = dict(snap); adopted = cr.adopt_new_imports(base, snap2)
print(len(snap2), len(adopted), len(cr.changed_files(snap, snap2)))
```

Measured 2026-09-06 on this checkout (6eef4df17):

| measure | value |
|---|---|
| modules in the watch set at start | 30 |
| reflexes in `REFLEX_NAMES` / with a module on disk | 101 / 101 |
| of those in the start-up watch set | **0** |
| modules after importing every reflex | 164 |
| files `adopt_new_imports` brings in | 134 (101 reflexes + 33 transitive imports) |
| changes the old rule reported across all of them | 0 |
| `snapshot()` cost, start / after all reflexes | 0.9 ms / 1.8 ms |

The 33 transitive imports matter as much as the reflexes: a fix to a module a
reflex reaches (a worktree helper, a lease module) was invisible on the same terms.

## The fix, and the shape not taken

Shape (a) from the card: `adopt_new_imports(baseline)` records every path new in
the current snapshot into the baseline AT ITS CURRENT MTIME — still not a change,
so a first import never restarts and the loop the old docstring describes cannot
return — and the next rewrite of that file is seen. `restart_if_code_changed`
calls it, so the scheduler, pr_watcher and all seven `DaemonBase` subclasses get
it from one edit and no caller changed. Cost: one extra `snapshot()` per cycle,
about 1–2 ms.

Shape (b) — seeding the baseline from `REFLEX_NAMES` at start — was not taken. It
covers only the reflex files themselves, not the 33 transitive imports the
survey found, and a pull between start and a reflex's first import would make it
restart the daemon for code the import had already loaded.

**Order is load-bearing:** adoption runs BEFORE `pull_if_safe`. A module imported
during the cycle holds pre-pull code; adopting after the pull would record the
post-pull mtime and bury the very change the pull delivered. A test pins this by
making the pull rewrite the file and asserting the restart fires on that call.

**Residual window, named:** a pull made by SOMEBODY ELSE on the same checkout
(the scheduler shares C:/AI/ICDev with the daemon) between a module's first
import and the end of that cycle is adopted at the post-pull mtime, and that one
fix is seen only on the file's next change. One cycle per module, once per
process — against never.

## What a fixed board reads like

`.logs/tools.genesis.code_reload.ndjson` now records, from the daemon's process,
`now watching N lazily imported file(s), e.g. kanban.py` on the cycle a reflex
first runs, and `code changed on disk (...) — re-executing` after the next merge
to it, with no `restart_stale_daemon` row in `audit_trail` for a reflex fix.
`python tools/awareness/restore_acts.py --plan --json` is the re-derivation: a
daemon proven `stale` on a reflex-only change after `MIN_UPTIME_SECONDS` means
the adoption did not happen.

Tests: `tests/genesis/test_code_reload.py` (three new cases, RED on the previous
tree: 2 failed / 1 passed).
