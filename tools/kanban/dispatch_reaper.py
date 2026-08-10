#!/usr/bin/env python3
# CUI // SP-CTI
"""Kill the process tree behind a reaped task — safely, or not at all.

WHY THIS EXISTS. The stale-reaper's only handle on a dispatched subprocess was
the in-memory ``_running`` dict in the scheduler. That handle does not survive a
scheduler restart, and it never existed for a task dispatched by a previous
instance. So the reap flipped the task's status and left the process tree alive:
the scheduler re-dispatched, a second tree spawned, and the first wedged forever
holding its worktree and its port.

Measured on ``task-e2e-ebf5ab21`` (2026-08-10): three ``stale-reaper -> backlog``
transitions, ``failure_count`` 3, and an orphaned Playwright tree still listening
on 5090 whose own launcher had already exited — 1.7s of CPU total, no browser and
no test workers, i.e. it had never reached test execution and never would. Two
more cycles would have quarantined the task at fc>=5 and leaked two more trees.

WHY A PID IS NOT ENOUGH. Pids are reused. A stored pid that now belongs to
something else is the one input that turns a cleanup into an outage, so nothing
here kills on a pid alone: the recorded start time must also match, to the
second. Any mismatch, any unreadable field, any error — decline. A leaked
process tree costs a worktree and a port; killing the wrong process costs
whatever that process was doing. The asymmetry is the whole design.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)


def _win_start_time(pid: int) -> Optional[str]:
    """Process creation time via GetProcessTimes.

    Deliberately not `wmic`: it is REMOVED on Windows 11 build 26200, where the
    first version of this returned None for every live pid — which, because
    unknown means decline, silently disabled the entire cleanup. Not `tasklist`
    either: it does not report a creation time, so it cannot answer the only
    question being asked. ctypes needs no external binary and no shell.
    """
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_t, kernel_t, user_t = (wintypes.FILETIME() for _ in range(3))
        ok = k32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exit_t),
            ctypes.byref(kernel_t), ctypes.byref(user_t))
        if not ok:
            return None
        # The raw 64-bit value is the identity; formatting it would only add a
        # way for two different processes to compare equal.
        return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
    finally:
        k32.CloseHandle(handle)


def process_start_time(pid: int) -> Optional[str]:
    """A stable identity for a pid, or None when it cannot be established.

    Returning None is a refusal, not a shrug: every caller treats "unknown" as
    "do not kill".
    """
    try:
        if pid is None or int(pid) <= 0:
            return None
    except (TypeError, ValueError):
        return None
    try:
        if os.name == "nt":
            return _win_start_time(int(pid))
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(int(pid))],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        return out or None
    except Exception as exc:  # noqa: BLE001 — an unidentifiable pid is not killable
        logger.debug("dispatch_reaper: cannot identify pid %s: %s", pid, exc)
        return None


def is_same_process(pid: int, recorded_start: Optional[str]) -> bool:
    """True only when this pid is still the process we dispatched.

    Fail-closed on every unknown. Without the recorded start time there is
    nothing to compare against, so the answer is no — not "probably".
    """
    if not recorded_start:
        return False
    current = process_start_time(pid)
    if not current:
        return False
    return current.strip() == recorded_start.strip()


def kill_tree(pid: int, recorded_start: Optional[str]) -> dict:
    """Kill a dispatched process AND its children, if it is provably still ours.

    Children matter more than the parent here: the wedged Playwright run held a
    dashboard subprocess on a port, and killing only the parent would have left
    that port bound and the next dispatch unable to start.
    """
    if not is_same_process(pid, recorded_start):
        return {"killed": False, "reason": "pid does not match the dispatched process"}
    try:
        if os.name == "nt":
            # /T takes the tree, /F is required for a process that is wedged
            # rather than merely busy — a wedged process will not honour a
            # polite request, which is precisely why it is being reaped.
            proc = subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                capture_output=True, text=True, timeout=30,
            )
            ok = proc.returncode == 0
            return {"killed": ok, "reason": (proc.stdout or proc.stderr or "").strip()[:200]}
        os.killpg(os.getpgid(int(pid)), 9)
        return {"killed": True, "reason": "killpg"}
    except ProcessLookupError:
        return {"killed": False, "reason": "already gone"}
    except Exception as exc:  # noqa: BLE001 — a failed cleanup must not break the reap
        logger.warning("dispatch_reaper: kill of %s failed: %s", pid, exc)
        return {"killed": False, "reason": str(exc)[:200]}


def record_dispatch(conn, task_id: str, pid: int) -> None:
    """Persist the pid and its start time so a LATER scheduler can clean it up.

    Best-effort by construction: failing to record a pid must never fail the
    dispatch it describes.
    """
    try:
        conn.execute(
            "UPDATE kanban_tasks SET dispatch_pid = %s, dispatch_pid_started_at = %s "
            "WHERE id = %s",
            (int(pid), process_start_time(pid), task_id),
        )
        try:
            conn.commit()
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("dispatch_reaper: could not record pid for %s: %s", task_id, exc)


def kill_recorded_dispatch(conn, task_id: str) -> dict:
    """Kill the tree recorded for this task. Safe to call when there is none."""
    try:
        row = conn.execute(
            "SELECT dispatch_pid, dispatch_pid_started_at FROM kanban_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 — pre-migration DBs have no column
        return {"killed": False, "reason": f"no dispatch_pid column: {exc}"[:120]}
    if not row or not row[0]:
        return {"killed": False, "reason": "no recorded pid"}
    outcome = kill_tree(int(row[0]), row[1])
    if outcome.get("killed"):
        logger.warning("dispatch_reaper: killed orphaned tree pid=%s for %s",
                       row[0], task_id)
    return outcome
