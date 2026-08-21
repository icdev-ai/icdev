#!/usr/bin/env python3
# CUI // SP-CTI
"""Let a long-lived daemon pick up its own code changes without a human.

THE MANUAL STEP THIS REMOVES. A daemon imports its modules once and runs for
days. Every fix to that code is inert until somebody restarts the process — and
"somebody" is the problem: on 2026-08-09 `pr_watcher` was restarted FOUR times by
hand, and between each restart it ran fixes that had already merged. Twice the
board looked broken when the only fault was a daemon serving code from hours
earlier. Fixing the underlying bug does not help if the fix cannot reach the
process that needs it.

WHY RE-EXEC RATHER THAN EXIT. Exiting relies on a supervisor to restart the
process, and on this deployment the supervision is uneven: some daemons are
launched by the dashboard, some by the genesis launcher, some by hand from a
shell. A daemon that exits under a launcher that is not watching is simply dead —
strictly worse than running stale. Re-exec works identically whether something is
supervising or nothing is.

WINDOWS IS NOT POSIX HERE, AND THE DIFFERENCE COSTS A SERVER. This module used to
say ``os.execv`` "replaces the process in place … available on Windows and POSIX
alike". The first half is false on Windows. There is no image replacement: the
CRT SPAWNS A NEW PROCESS with ``bInheritHandles=TRUE`` and terminates this one,
so the replacement starts life holding a duplicate of every INHERITABLE handle
the old process had.

Ordinarily that is harmless — PEP 446 makes Python sockets non-inheritable by
default. The exception is the one that matters here: ``werkzeug.serving`` calls
``srv.socket.set_inheritable(True)`` ON PURPOSE, so its own reloader child can
reuse the port through ``WERKZEUG_SERVER_FD``. We run with ``use_reloader=False``
and re-exec ourselves instead, so the replacement never reads that variable — it
binds a fresh socket while the inherited one stays open in a process that has
already exited.

What that looks like in production, observed on the dashboard 2026-08-15: the
listening socket on :5050 was owned by a PID that no longer existed, holding six
ESTABLISHED and six CLOSE_WAIT connections. The kernel kept routing new
connections into that socket and nobody ever read them, while the live dashboard
sat idle in its own ``serve_forever`` accept loop. Every symptom said healthy —
the log showed a clean boot, ``py-spy`` showed every thread idle and correct, the
port answered a TCP connect instantly — and not one HTTP request was ever
answered. Killing the live process released the port, which is what proved it was
holding the dead parent's handle.

So the re-exec is platform-split. POSIX keeps ``os.execv``, which really is an
in-place image replacement and is correct as written. Windows spawns the
replacement with ``close_fds=True`` — no handle inheritance at all — and then
``os._exit``s, which makes the kernel close the listening socket for real.
Standard streams are the deliberate exception, passed through explicitly so the
replacement keeps writing to the same log file.

WHAT COUNTS AS "CHANGED". The mtimes of every already-imported module that lives
inside the repo — not just the daemon's own file. A change to
``tools/ci/error_classifier.py`` changes how ``pr_watcher`` behaves just as much
as a change to ``pr_watcher.py``, and watching one file would have missed most of
today's merges. Reading ``sys.modules`` means the watch set is exactly the code
the process actually loaded, with no list to maintain and no way for it to drift.

SAFETY. The re-exec happens only between polls, never mid-work; only after a
minimum uptime, so a half-written file cannot induce a restart loop; and only
when explicitly enabled by the caller.
"""
from __future__ import annotations

import os
import subprocess  # nosec B404 — git only, fixed argv, shell=False
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Never re-exec within this many seconds of starting. A restart loop is worse
#: than stale code: it burns CPU, it never finishes a poll, and every cycle looks
#: like a fresh start in the log so the loop itself is hard to see. Editors write
#: files in stages, so a save can briefly change an mtime twice.
MIN_UPTIME_SECONDS = 120


def _repo_root() -> Path:
    # From __file__, never cwd — daemons are started from worktrees and from
    # service managers with an arbitrary working directory.
    return Path(__file__).resolve().parents[2]


def snapshot(root: Optional[Path] = None) -> Dict[str, float]:
    """mtime of every imported module whose file lives under the repo.

    Missing or unreadable files are skipped rather than recorded as absent: a
    module being rewritten is exactly when a stat can fail, and treating that as
    "changed" would restart on a transient.
    """
    root = root or _repo_root()
    root_str = str(root)
    out: Dict[str, float] = {}
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        try:
            path = os.path.abspath(f)
            if not path.startswith(root_str):
                continue
            out[path] = os.stat(path).st_mtime
        except OSError:
            continue
    return out


def changed_files(before: Dict[str, float], after: Dict[str, float]) -> list:
    """Files present in BOTH snapshots whose mtime moved.

    A path in `after` but not in `before` is NOT a change. It is a lazy import:
    the same file that was always on disk, loaded later because some code path
    reached it for the first time. Counting those as new code turned this
    feature into a restart loop — the daemon re-execs, takes a fresh baseline,
    runs one cycle, lazily imports something else, and re-execs again, never
    finishing a dispatch. Observed on kanban_scheduler at ~1 restart/minute:

        09:02:10  code changed (1 file, e.g. pr_linker.py) - re-executing
        09:03:09  code changed (1 file, e.g. pr_linker.py) - re-executing
        09:04:08  code changed (11 files, e.g. leases.py, connector.py)

    None of those files had been modified. The previous docstring here claimed
    the opposite — "plus any that newly appeared" — and that claim WAS the bug:
    what this needs to detect is a file being REWRITTEN, and a rewritten file is
    by definition one this process had already loaded.

    A file that DISAPPEARED is likewise not reported: an import that vanished
    cannot be the code this process is running, and a file deleted mid-write
    would otherwise trigger a restart that fixes nothing.
    """
    out = []
    for path, mtime in after.items():
        prior = before.get(path)
        if prior is not None and mtime != prior:
            out.append(path)
    return sorted(out)


#: Never pull more often than this. A fetch is cheap but not free, and a daemon
#: polling every 30s does not need to ask the forge every time.
MIN_PULL_INTERVAL_SECONDS = 300

_last_pull: float = 0.0


def _run_git(args, root, timeout=120):
    return subprocess.run(  # nosec B603 — fixed argv, shell=False
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, shell=False,
    )


def pull_if_safe(root: Optional[Path] = None, *, runner=None,
                 min_interval: float = MIN_PULL_INTERVAL_SECONDS,
                 dry_run: bool = False) -> Dict[str, Any]:
    """Fast-forward the working copy, but ONLY when nothing local can be lost.

    THE GAP THIS CLOSES. restart_if_code_changed watches mtimes on disk, and a
    merge on the forge does not touch disk. So a fix could merge, the daemon
    could be perfectly capable of reloading it, and nothing would ever tell it:
    #1500 merged at 01:20 and the working copy still had the old file twelve
    minutes later. The reload loop needs something to fetch.

    THE GUARD IS THE POINT, not the pull. This checkout carries local
    modifications — args/projects.yaml, a batch of skills files — and a blind
    `git pull` in a daemon would either fail on every cycle or, worse, clobber
    them. So: fetch (never merges), compute what would arrive, intersect that
    with what is locally modified, and REFUSE if the sets touch. That is the same
    check that was done by hand three times today before each safe pull.

    Refuses on: any overlap, a dirty index (a merge or rebase in flight), a
    detached HEAD or any branch other than the default, and a non-fast-forward.
    Every refusal returns a reason instead of raising, because this runs inside
    someone else's poll loop.
    """
    global _last_pull
    root = root or _repo_root()
    now = time.time()
    # A DRY RUN NEVER CONSUMES THE THROTTLE. `dry_run` exists so a reporter can
    # ask "would this pull, and if not why" through THIS function rather than
    # re-deriving the predicate — and a reporter that spent the throttle window
    # would starve the real updater it exists to describe (autonomy-dep-03).
    if not dry_run:
        if now - _last_pull < min_interval:
            return {"pulled": False, "reason": "throttled"}
        _last_pull = now

    run = runner or (lambda args, **kw: _run_git(args, root, **kw))
    try:
        branch = run(["rev-parse", "--abbrev-ref", "HEAD"])
        name = (getattr(branch, "stdout", "") or "").strip()
        if name != "main":
            # A daemon must never move a checkout someone is working on.
            return {"pulled": False, "reason": f"not on main (on {name or 'detached'})"}

        if getattr(run(["fetch", "--quiet", "origin", "main"]), "returncode", 1) != 0:
            return {"pulled": False, "reason": "fetch failed"}

        incoming = run(["diff", "--name-only", "HEAD..origin/main"])
        if getattr(incoming, "returncode", 1) != 0:
            return {"pulled": False, "reason": "cannot list incoming files"}
        arriving = {f.strip() for f in (incoming.stdout or "").splitlines() if f.strip()}
        if not arriving:
            return {"pulled": False, "reason": "already current"}

        dirty = run(["status", "--porcelain"])
        if getattr(dirty, "returncode", 1) != 0:
            return {"pulled": False, "reason": "cannot read working tree state"}
        local = set()
        for line in (dirty.stdout or "").splitlines():
            if not line.strip():
                continue
            if line[:2].strip() in {"UU", "AA", "DU", "UD", "AU", "UA"}:
                return {"pulled": False, "reason": "merge in progress"}
            path = line[3:].strip().strip('"')
            if " -> " in path:            # a rename reports "old -> new"
                path = path.split(" -> ", 1)[1]
            local.add(path)

        clash = sorted(arriving & local)
        if clash:
            logger.warning(
                "code_reload: refusing to pull — %d incoming file(s) are locally "
                "modified: %s", len(clash), ", ".join(clash[:3]))
            return {"pulled": False, "reason": "local changes would be lost",
                    "conflicts": clash}

        if dry_run:
            # Everything the guard checks has passed. Report that, and touch
            # nothing.
            return {"pulled": False, "reason": "would pull", "dry_run": True,
                    "incoming": len(arriving)}

        ff = run(["merge", "--ff-only", "origin/main"])
        if getattr(ff, "returncode", 1) != 0:
            return {"pulled": False, "reason": "not a fast-forward"}
        logger.info("code_reload: pulled %d file(s) from origin/main", len(arriving))
        return {"pulled": True, "files": len(arriving)}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"pulled": False, "reason": f"git unavailable: {exc}"}


def _inheritable_std_streams() -> Dict[str, Any]:
    """The std handles to hand the replacement, so its output keeps landing.

    ``close_fds=True`` on Windows sets ``bInheritHandles=False``, which is the
    entire point — but it also means the child gets NO standard handles unless
    they are named explicitly. These daemons are started with their stdout and
    stderr redirected to files (``.tmp/dashboard.log`` and friends), so silently
    dropping them would trade a hung server for a mute one.

    Only these three are passed. A socket is never among them.
    """
    streams: Dict[str, Any] = {"stdin": subprocess.DEVNULL}
    for name, stream in (("stdout", sys.stdout), ("stderr", sys.stderr)):
        try:
            stream.flush()
            fileno = stream.fileno()
        except (AttributeError, ValueError, OSError):
            # No real handle behind it (captured under pytest, or already
            # closed). Inherit nothing rather than raise: losing the log is
            # survivable, failing to restart is the bug we are fixing.
            continue
        streams[name] = fileno
    return streams


def respawn(argv: list, *, execv=None, popen=None, exit_=None) -> None:
    """Replace this process with a fresh one running ``argv``. Does not return.

    POSIX: ``os.execv`` — a true in-place image replacement, which is what the
    original code assumed everywhere.

    Windows: there is no in-place replacement. ``os.execv`` spawns a new process
    that INHERITS this one's handles and then kills this one, which strands a
    server's listening socket in a dead process where it silently swallows every
    connection (see the module docstring). Spawning with ``close_fds=True`` and
    exiting hard is the same outcome without the inheritance.

    ``os._exit`` rather than ``sys.exit``: this runs on a watcher thread, where
    ``SystemExit`` would unwind that thread only and leave the old server — and
    its socket — alive alongside the replacement. That is the bug, twice.
    """
    if execv is not None:
        # The caller supplied the mechanism outright — the seam every
        # restart_if_code_changed test uses to observe "it decided to restart"
        # without actually restarting. It must win on EVERY platform: honouring
        # it only on POSIX meant that on Windows the tests took the real path
        # and os._exit(0) killed the pytest process mid-run, which surfaces as a
        # truncated report rather than a failure.
        execv(argv[0], argv)
        return
    if os.name == "nt":
        spawn = popen or subprocess.Popen
        leave = exit_ or os._exit
        spawn(argv, close_fds=True, **_inheritable_std_streams())  # nosec B603
        leave(0)
        return
    os.execv(argv[0], argv)


def restart_if_code_changed(
    baseline: Dict[str, float],
    *,
    started_at: float,
    enabled: bool = True,
    pull: bool = True,
    min_uptime: float = MIN_UPTIME_SECONDS,
    root: Optional[Path] = None,
    execv=None,
) -> list:
    """Re-exec this process when its own code has changed on disk.

    Returns the changed files when it declines to restart, so a caller can log
    them. Does not return at all when it re-execs.
    """
    if not enabled:
        return []
    if pull:
        # Fetch before looking at mtimes: a merge on the forge does not touch
        # disk, so without this the watcher can only ever see changes somebody
        # else pulled.
        pull_if_safe(root)
    current = snapshot(root)
    changed = changed_files(baseline, current)
    if not changed:
        return []

    uptime = time.time() - started_at
    if uptime < min_uptime:
        logger.info(
            "code changed (%d file(s)) but uptime is %.0fs — deferring restart "
            "until %.0fs to avoid a loop", len(changed), uptime, min_uptime)
        return changed

    logger.warning(
        "code changed on disk (%d file(s), e.g. %s) — re-executing to pick it up",
        len(changed), ", ".join(Path(p).name for p in changed[:3]))
    try:
        # sys.argv[0] may be a relative path and the cwd may since have changed,
        # so re-exec through the interpreter with the original argv.
        respawn([sys.executable] + sys.argv, execv=execv)
    except Exception as exc:  # noqa: BLE001 — a failed re-exec must not kill it
        logger.error("re-exec failed, continuing with stale code: %s", exc)
    return changed
