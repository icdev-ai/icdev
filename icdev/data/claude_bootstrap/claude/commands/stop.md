# Stop — Shut down the ICDEV™ service stack in the one order that does not fight itself

The stop rule has lived in `/start` as prose: stop the **supervisor**, never a
supervised child by name, never `taskkill /f /im python.exe`. On 2026-09-03 it
had to be re-derived by hand from the process table before a shutdown, so it
became a script. `/stop` runs that script and then PROVES the result from the
process table and the ports — never from a log file.

## Variables

DASHBOARD_PORT: read from `.env` (`ICDEV_DASHBOARD_PORT`, default 5050)
FT_PORT: 5200
RT_PORT: 5300
PORTAL_PORT: 8443

## What the script does, in order

`python tools/genesis/shutdown_dashboard.py`

1. **Supervisor first.** Pid from `.tmp/genesis/launcher.pid`, command line
   VERIFIED to run `tools/genesis/launch.py`. A reused pid is refused (exit 2,
   nothing touched).
2. **Then its children, by pid RECORDED from the tree before anything was
   stopped** — dashboard, genesis daemon, proposal_genesis, kanban scheduler,
   pr_watcher. On Windows `terminate()` skips the launcher's own cleanup, so the
   children orphan rather than stop; once the supervisor is provably gone
   nothing respawns them and stopping by pid is safe. Nothing is ever selected
   by name.
3. **Agent workers mid-build are REPORTED and left running.** They are the
   scheduler's grandchildren; killing them discards work (~40 minutes lost on
   2026-08-29). `--include-workers` stops them too — a decision, not a default.
4. **The external supervisors, each then its child** — ICDEV[RT]
   (`supervise_rt.py` → `launch_rt.py`, port 5300), and ICDEV[FT] only as a
   fallback. Each supervisor is the innermost of any wrapper chain (Git Bash
   cannot `exec()`, so the shells above it carry the same command line).
   `--keep-ft` / `--keep-rt` skip one pair; the table is `EXTERNAL_SUPERVISORS`
   in the script. **ICDEV[FT] is stopped by its OWN script first** (step 1a
   below): this script's stop of the FT pair is `terminate()`, a hard kill that
   cuts a reflex tick mid-cycle; `C:\AI\icdev_ft\stop_ft.py` (ftl-stop-01)
   stops the FT supervisor, then asks the server to drain over HTTP (the reflex
   clock is stopped and joined BEFORE uvicorn exits), and falls back to
   terminate only after `grace_seconds`, saying so. Measured 2026-09-06: the
   hard path was the only one that existed, logged by the FT supervisor as
   `rc 4294967295`.
5. **Verify, never assume:** every recorded pid re-tested dead; ports 5050,
   5200 and 5300 re-tested for a listener. A survivor is exit 1 with its pid.
6. The stale `launcher.pid` is removed only after its pid is confirmed dead.

Exit codes: **0** stopped and verified (or already down) · **1** a survivor or a
listener remains · **2** the tree could not be measured (no psutil, unreadable
lock, reused pid) — never a clean answer.

## Workflow

> **Windows PowerShell note:** set `$env:PYTHONPATH = "C:\AI\ICDev"` before any
> `python` call. Run from the checkout that STARTED the stack — a worktree's
> `.tmp/` is empty and reads as "already down" while 5050 is still served. From
> anywhere else, pass `--pid-file C:\AI\ICDev\.tmp\genesis\launcher.pid`.

0. **See the plan before touching anything.** A dry run records the supervisor,
   every child with its service name, any agent workers, the FT and RT pairs
   and the current listeners, and acts on none of it:
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   python tools/genesis/shutdown_dashboard.py --dry-run
   ```
   If it lists a worker under **LEFT RUNNING**, decide now: let it finish (the
   default) or stop it with `--include-workers` in step 1b.

1a. **Stop ICDEV[FT] gracefully, with its own script.** It reads the FT
   pidfiles (`C:\AI\icdev_ft\.tmp\ft_supervisor.pid`, `ft_server.pid`),
   VERIFIES each pid's command line before touching it (a reused pid is exit 2,
   nothing touched), stops the supervisor first, then `POST /api/v1/admin/shutdown`
   with `FIN_API_TOKEN` from the FT `.env`, waits `grace_seconds`, and proves
   the pids and port 5200 from the process table. Skip this step (and drop
   `--keep-ft` in 1b) only if you WANT the hard kill:
   ```powershell
   python C:\AI\icdev_ft\stop_ft.py --dry-run     # who is running; touches nothing
   python C:\AI\icdev_ft\stop_ft.py               # exit 0 stopped+verified · 1 survivor/listener · 2 refused, nothing touched
   ```
   - exit **1**: `SURVIVORS:` / `port listening after: True` name the pid —
     confirm its command line, then `Stop-Process -Id <id>` that exact pid.
   - exit **2**: nothing was touched. `FIN_API_TOKEN` missing or refused →
     fix it or rerun with `--force` (a deliberate hard kill, reported as
     `FORCED`); a reused pid → remove the stale pidfile it names.
   - To RESTART FT onto a merge the supervisor did not deploy itself (it
     deploys only when origin/main ≠ local HEAD), use
     `python C:\AI\icdev_ft\stop_ft.py --restart` instead: the server alone
     drains and the supervisor respawns it on the code on disk.

1b. **Stop the rest of the stack.** Add `--pause` when the board should NOT
   dispatch on the next start (sets Manual Build); `--keep-ft` because 1a
   already stopped ICDEV[FT] (or is leaving it serving); add `--keep-rt` to
   leave ICDEV[RT] serving:
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   python tools/genesis/shutdown_dashboard.py --pause --keep-ft
   echo "exit: $LASTEXITCODE"
   ```
   - exit **1**: read the `SURVIVORS:` / `LISTENER REMAINS:` lines — each names
     a pid. Confirm its command line with
     `Get-CimInstance Win32_Process -Filter "ProcessId=<id>"` and stop THAT
     exact pid with `Stop-Process -Id <id>`. Never by name, never by filter.
   - exit **2**: nothing was touched. Fix what it names (install psutil, remove
     a lock naming a process that is not the launcher) and re-run.

2. **The two unsupervised services `/start` launches are not in the tree.** The
   SaaS portal (`tools/saas/api_gateway.py`, port 8443) and the poll trigger
   (`tools/ci/triggers/poll_trigger.py`) are started with `Start-Process`, have
   no supervisor to respawn them, and are stopped by verified pid. List them,
   READ the command lines, then stop only those pids:
   ```powershell
   $svc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
     Where-Object { $_.CommandLine -match 'tools[\\/]saas[\\/]api_gateway\.py|tools[\\/]ci[\\/]triggers[\\/]poll_trigger\.py' }
   $svc | Format-Table ProcessId, CommandLine -AutoSize
   $svc | ForEach-Object { Stop-Process -Id $_.ProcessId }
   ```
   The match is on the SCRIPT PATH in argv, so a shell that merely typed the
   name does not qualify; review the table before the last line regardless.

3. **Prove it from the record.** The supervisor must read DOWN with no lock,
   and no listener may remain on the four ports:
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   python tools/genesis/supervisor_status.py
   Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
     Where-Object { $_.LocalPort -in 5050, 5200, 5300, 8443 } |
     Format-Table LocalPort, OwningProcess -AutoSize
   ```
   An empty table is the answer. A row names the owning pid — verify its
   command line before stopping it, as in step 1b.

4. **Report to the user:** which pids were stopped and in what order, any
   worker deliberately left running, the FT pair's outcome from `stop_ft.py`
   (graceful, `FORCED`, or refused) and the RT pair's from 1b, the build
   mode (`manual` means nothing dispatches on the next `/start` until
   `python tools/kanban/cli.py --build-mode auto`), and the four empty ports.

## Do NOT

- `taskkill /f /im python.exe` — it kills the supervisor with everything else
  and has taken out unrelated tooling on this machine before.
- `Stop-Process` a scheduler, pr_watcher, daemon or dashboard by name or
  `Where-Object` filter while the supervisor is up — it restarts them, and a
  name filter is what produced three concurrent pr_watchers.
- Delete `.tmp/genesis/launcher.pid` by hand to make the status read DOWN. The
  script removes it only after the pid it names is confirmed dead; a lock
  deleted under a live supervisor lets `/start` launch a second one.
- Stop the agent workers to make the shutdown "complete". Their tasks are
  re-dispatched by startup recovery on the next `/start`; their work is not.
- Stop ICDEV[FT] with this script's hard path (no `--keep-ft`) while
  `stop_ft.py` exists and `FIN_API_TOKEN` is set — a reflex tick cut mid-cycle
  is exactly what the graceful path was built to avoid. `--force` on
  `stop_ft.py` is the sanctioned hard kill: it still verifies the pid first.

## Restart

`/start`. Step 0 there reads `supervisor_status.py` first and, finding it DOWN,
runs the PG cleanup and the stuck-task reset before `--ensure` launches a fresh
supervisor, and its step 8b brings ICDEV[FT] and ICDEV[RT] back under their
own supervisors. If the build mode was paused here, resume with
`python tools/kanban/cli.py --build-mode auto` when you want dispatch back.
