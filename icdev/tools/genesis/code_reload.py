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
strictly worse than running stale. ``os.execv`` replaces the process in place, so
it works identically whether something is supervising or nothing is. It is
available on Windows and POSIX alike.

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
import sys
import time
from pathlib import Path
from typing import Dict, Optional

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
    """Files whose mtime moved, plus any that newly appeared.

    A file that DISAPPEARED is not reported: an import that vanished cannot be
    the code this process is running, and a deleted file mid-write would
    otherwise trigger a restart that fixes nothing.
    """
    out = []
    for path, mtime in after.items():
        prior = before.get(path)
        if prior is None or mtime != prior:
            out.append(path)
    return sorted(out)


def restart_if_code_changed(
    baseline: Dict[str, float],
    *,
    started_at: float,
    enabled: bool = True,
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
    runner = execv or os.execv
    try:
        # sys.argv[0] may be a relative path and the cwd may since have changed,
        # so re-exec through the interpreter with the original argv.
        runner(sys.executable, [sys.executable] + sys.argv)
    except Exception as exc:  # noqa: BLE001 — a failed re-exec must not kill it
        logger.error("re-exec failed, continuing with stale code: %s", exc)
    return changed
