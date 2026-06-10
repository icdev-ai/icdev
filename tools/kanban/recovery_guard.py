#!/usr/bin/env python3
# CUI // SP-CTI
"""Kanban recovery guard — bounded auto-restart on repeated task failures.

Why this exists
---------------
The Kanban scheduler can fall into a feedback loop where a single stuck task
(e.g. ``acf-vv-03`` with a 40-min reaper threshold × 35 cycles) consumes
LLM tokens indefinitely: the scheduler dispatches → reaper sweeps → repeat.
Each cycle burns tokens for the agent invocation, worktree, pytest, and
the reaper sweep itself.

This module puts a hard ceiling on that loop per task:

  - **First crossing** (failure_count >= 5): kill all python processes and
    restart dashboard + scheduler (one recovery attempt). The newly restarted
    scheduler is fresh — it will re-dispatch the same task, but at least the
    in-memory state is cleared and any leaks are reaped.

  - **Second crossing** (failure_count >= 10, i.e. another 5 after restart):
    kill all python processes and **do NOT restart anything**. The system
    sits dark until a human intervenes. This bounds the total wasted tokens
    per stuck task to ~10 dispatch attempts instead of unbounded 35+.

State persistence
-----------------
The recovery state for each task is stored in ``kanban_board_settings`` under
key ``recovery_count_<task_id>``. The value is a small integer encoded as
text:

  - ``"1"`` — the restart path has fired for this task; halt is still pending.
  - ``"2"`` — the halt path has fired for this task; recovery is complete.

This survives a process kill because it's a DB row. Using a real DB-backed
key-value store instead of an env var or a temp file means a SIGKILL of the
scheduler mid-recovery doesn't lose the marker. The two-state encoding lets
us distinguish "restart done, awaiting halt" from "halt done, no more
action" so a task that keeps failing can't re-trigger the kill+no-restart
ps1 on every dispatch attempt.

Restart primitives
------------------
All kill/restart work is delegated to a one-shot PowerShell script launched
with ``subprocess.Popen(..., creationflags=DETACHED_PROCESS)`` so it can
outlive the calling Python process (which we are about to ``taskkill``).

The script itself is written to ``.tmp/kanban/recovery_<mode>_<ts>.ps1`` and
launched via ``Start-Process powershell.exe -File`` — never inline — so the
``taskkill /f /im python.exe`` step does not kill the new python.exe that
the script spawns.

Usage
-----
Called from ``_record_failure_and_maybe_flag`` (a single call site) after
the failure_count DB update::

    from tools.kanban.recovery_guard import check_and_maybe_restart
    check_and_maybe_restart(task_id, new_failure_count)

Run the self-test (does NOT kill anything)::

    python tools/kanban/recovery_guard.py --self-test
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Literal

# Tunables — all overridable via env so they're discoverable alongside the
# other KANBAN_* knobs in tools/genesis/reflexes/kanban.py.
FAILURE_THRESHOLD_FIRST = int(os.environ.get("KANBAN_RECOVERY_FC_FIRST", "5"))
FAILURE_THRESHOLD_HALT = int(os.environ.get("KANBAN_RECOVERY_FC_HALT", "10"))
KILL_SETTLE_SECONDS = float(os.environ.get("KANBAN_RECOVERY_KILL_SETTLE", "2"))
SELF_TEST_TASK_ID = "recovery-guard-selftest"

# Marker key prefix in kanban_board_settings. The task id contains hyphens
# (e.g. acf-vv-03) which is fine in the value column but we prefix for grep.
RECOVERY_KEY_PREFIX = "recovery_count_"
HALT_LOG_DIR = os.path.join(".tmp", "kanban")


# ---------------------------------------------------------------------------
# Settings I/O — wraps kanban_board_settings key-value store
# ---------------------------------------------------------------------------
def _get_setting(key: str) -> str | None:
    """Read a value from kanban_board_settings, or None if absent."""
    from tools.db.storage import get_connection
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT value FROM kanban_board_settings WHERE key = ?", (key,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row).get("value")


def _set_setting(key: str, value: str) -> None:
    """Upsert a key into kanban_board_settings."""
    from tools.db.storage import get_connection
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM kanban_board_settings WHERE key = ?", (key,),
        )
        if cur.fetchone() is None:
            cur.execute(
                "INSERT INTO kanban_board_settings "
                "(key, value, updated_at, classification) "
                "VALUES (?, ?, ?, 'CUI')",
                (key, value, now),
            )
        else:
            cur.execute(
                "UPDATE kanban_board_settings "
                "SET value = ?, updated_at = ? WHERE key = ?",
                (value, now, key),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Recovery primitive — write + launch a detached PowerShell script
# ---------------------------------------------------------------------------
def _launch_detached_ps1(script_body: str, log_path: str) -> int:
    """Write a .ps1 file and launch it detached. Returns the script PID.

    Detached via CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS so the script
    survives the calling python.exe being killed by ``taskkill /f /im python.exe``
    later in the recovery flow.

    The .ps1 is written with a UTF-8 BOM. PowerShell's default file
    encoding for .ps1 is the system code page (cp1252 on en-US Windows),
    which mangles the em-dash and other non-ASCII characters in the
    embedded log lines. The BOM forces the parser to interpret the file
    as UTF-8. (Confirmed via Parser.ParseFile: the same bytes without
    BOM parse to "string missing terminator" on line 10.)
    """
    # CREATE_NEW_PROCESS_GROUP (0x00000200) + DETACHED_PROCESS (0x00000008)
    flags = 0x00000008 | 0x00000200
    # Write the script to a .tmp file (not inlined into -Command) so the
    # process spawned by powershell.exe -File is powershell.exe, not python.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False,
        dir=HALT_LOG_DIR,
        prefix="recovery_",
        encoding="utf-8-sig",  # UTF-8 with BOM
    ) as f:
        f.write(script_body)
        script_path = f.name

    # Launch the script detached. The new process is powershell.exe, which
    # taskkill /f /im python.exe will NOT match.
    creationflags = flags
    proc = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", script_path],
        stdout=open(log_path, "ab"),
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
        close_fds=True,
    )
    return proc.pid


def _build_restart_script(task_id: str, failure_count: int) -> tuple[str, str]:
    """Build a PowerShell script that kills python.exe and restarts the
    dashboard + scheduler. Returns (script_body, log_path)."""
    log_path = os.path.join(
        HALT_LOG_DIR, f"recovery_restart_{task_id}_{int(time.time())}.log",
    )
    # Use Start-Process (not & direct call) so the new python.exe inherits
    # the window detachment. PYTHONPATH is required on Windows for the
    # ``tools.*`` import shim to resolve.
    script = f"""# Auto-generated by tools/kanban/recovery_guard.py
# Recovery RESTART for task {task_id} (fc={failure_count})
$ErrorActionPreference = 'Continue'
$ts = (Get-Date).ToString('o')
Add-Content -Path '{log_path}' -Value "[$ts] recovery: starting for {task_id} (fc={failure_count})"

# 1. Kill all python.exe — this kills the scheduler that called us, so we
#    MUST be running in a separate powershell.exe process (we are — see
#    _launch_detached_ps1). It does NOT kill the powershell.exe running
#    this script.
taskkill /f /im python.exe 2>$null | Out-Null
Start-Sleep -Seconds {KILL_SETTLE_SECONDS}
Add-Content -Path '{log_path}' -Value "[$ts] recovery: python.exe killed, settling"

# 2. Restart dashboard + scheduler using the same args as /start
$env:PYTHONPATH = 'C:\\AI\\ICDev'
$dash = Start-Process python -ArgumentList 'tools/dashboard/app.py' `
    -RedirectStandardOutput '.tmp/dashboard.log' `
    -RedirectStandardError '.tmp/dashboard_err.log' `
    -WindowStyle Hidden -PassThru
$sched = Start-Process python -ArgumentList 'tools/genesis/kanban_scheduler.py' `
    -RedirectStandardOutput '.tmp/kanban_scheduler.log' `
    -RedirectStandardError '.tmp/kanban_scheduler_err.log' `
    -WindowStyle Hidden -PassThru
Add-Content -Path '{log_path}' -Value ("[$ts] recovery: dashboard PID=" + $dash.Id + " scheduler PID=" + $sched.Id)
Add-Content -Path '{log_path}' -Value "[$ts] recovery: RESTART complete for {task_id}"
"""
    return script, log_path


def _build_halt_script(task_id: str, failure_count: int) -> tuple[str, str]:
    """Build a PowerShell script that kills python.exe and DOES NOT restart.

    The system sits dark until a human runs /start.
    """
    log_path = os.path.join(
        HALT_LOG_DIR, f"recovery_halt_{task_id}_{int(time.time())}.log",
    )
    script = f"""# Auto-generated by tools/kanban/recovery_guard.py
# Recovery HALT for task {task_id} (fc={failure_count})
$ErrorActionPreference = 'Continue'
$ts = (Get-Date).ToString('o')
Add-Content -Path '{log_path}' -Value "[$ts] recovery: HALT for {task_id} (fc={failure_count})"

taskkill /f /im python.exe 2>$null | Out-Null
Start-Sleep -Seconds {KILL_SETTLE_SECONDS}
Add-Content -Path '{log_path}' -Value "[$ts] recovery: HALT complete — manual intervention required."
Add-Content -Path '{log_path}' -Value "[$ts] recovery: task {task_id} failed {failure_count} times total. Investigate before running /start."
"""
    return script, log_path


# ---------------------------------------------------------------------------
# Public entry point — called by the scheduler on every failure
# ---------------------------------------------------------------------------
def check_and_maybe_restart(
    task_id: str, failure_count: int,
) -> Literal["none", "restarted", "halted"]:
    """Inspect failure_count; if it crosses a threshold, trigger recovery.

    Idempotent: only triggers when CROSSING the threshold (not on every call
    above it). Once a task has been restarted once, the next 5 failures run
    in the new process; if those also fail, the halt path triggers.

    Returns the action taken:
      - "none": normal failure, no escalation
      - "restarted": killed all python + restarted dashboard + scheduler
      - "halted": killed all python, no restart (manual intervention)
    """
    # Guard the self-test task id (it can be any string) — only the scheduler
    # can persist real recovery markers. The self-test is read-only against
    # kanban_board_settings.
    if failure_count < FAILURE_THRESHOLD_FIRST:
        return "none"

    key = RECOVERY_KEY_PREFIX + task_id
    prior = _get_setting(key)

    if prior is None:
        # First crossing: trigger restart, persist marker.
        script, log_path = _build_restart_script(task_id, failure_count)
        try:
            pid = _launch_detached_ps1(script, log_path)
        except Exception as exc:  # noqa: BLE001 — recovery is best-effort
            sys.stderr.write(
                f"[recovery_guard] failed to launch restart script: {exc}\n"
            )
            return "none"
        # Only persist marker AFTER we successfully launched the script.
        # If the launch raises, we don't want a stale "already recovered"
        # marker blocking the next attempt.
        try:
            _set_setting(key, "1")
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(
                f"[recovery_guard] marker write failed: {exc}\n"
            )
        print(
            f"[recovery_guard] task {task_id} hit fc={failure_count} "
            f"(>= {FAILURE_THRESHOLD_FIRST}) — RESTART triggered "
            f"(ps1 PID {pid}, log {log_path})"
        )
        return "restarted"

    # prior is set. Distinguish:
    #   "1" — restart already triggered; halt may still be pending.
    #   "2" — halt already triggered; this task is DONE (no more action).
    if prior == "2":
        # Already halted. The system should be dark; any further calls
        # here mean the scheduler is running again (post-/start) and
        # hit the same task. Stay dark — return "halted" so the caller
        # can audit-log without re-launching.
        return "halted"

    if failure_count >= FAILURE_THRESHOLD_HALT:
        # Second crossing: post-restart cycle also failed 5x. Halt.
        # Use a distinct marker value ("2") so subsequent calls at the
        # same or higher fc are recognized as "already halted" and do NOT
        # re-launch the kill+no-restart ps1. Without this distinction, a
        # task that keeps failing would re-kill the system on every
        # dispatch attempt.
        script, log_path = _build_halt_script(task_id, failure_count)
        try:
            pid = _launch_detached_ps1(script, log_path)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(
                f"[recovery_guard] failed to launch halt script: {exc}\n"
            )
            return "none"
        try:
            _set_setting(key, "2")
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(
                f"[recovery_guard] halt marker write failed: {exc}\n"
            )
        print(
            f"[recovery_guard] task {task_id} hit fc={failure_count} "
            f"(>= {FAILURE_THRESHOLD_HALT}) AFTER restart — HALT triggered "
            f"(ps1 PID {pid}, log {log_path})"
        )
        return "halted"

    # Between thresholds — recovery already happened, just waiting.
    return "none"


# ---------------------------------------------------------------------------
# Self-test — exercises the decision logic without killing anything
# ---------------------------------------------------------------------------
def _selftest() -> int:
    """Exercise both code paths against a fake task id. Does NOT kill anything.

    Uses an in-memory monkeypatch of _launch_detached_ps1 so we can verify
    the decision logic + DB marker write without actually restarting
    anything.
    """
    print("=" * 60)
    print("recovery_guard self-test")
    print("=" * 60)

    # Ensure the log dir exists.
    os.makedirs(HALT_LOG_DIR, exist_ok=True)

    # 1. fc below threshold — must return "none" and write nothing.
    launches: list[tuple[str, str, int]] = []

    def _fake_launch(body: str, log_path: str) -> int:
        launches.append((body[:80], log_path, 99999))
        return 99999

    # CRITICAL: when this file is run as __main__, the test runs in the
    # module object whose __name__ is "__main__", NOT the
    # tools.kanban.recovery_guard shim-resolved module. ``check_and_maybe_restart``
    # does LOAD_GLOBAL against THIS module's __dict__, so we must patch
    # globals() in __main__ (or, more robustly, also patch the shim's
    # module if it's been imported). The simplest universal fix is to
    # patch the global in BOTH places.
    orig_launch = globals()["_launch_detached_ps1"]
    globals()["_launch_detached_ps1"] = _fake_launch  # type: ignore[assignment]

    # Also patch the shim-resolved module if it exists in sys.modules and
    # is a different object (defensive — covers the case where the test
    # is imported by another test runner that already loaded the shim).
    shim_module = sys.modules.get("tools.kanban.recovery_guard")
    if shim_module is not None and shim_module is not sys.modules.get("__main__"):
        shim_module._launch_detached_ps1 = _fake_launch  # type: ignore[assignment]

    test_id = SELF_TEST_TASK_ID + "-" + str(int(time.time()))
    key = RECOVERY_KEY_PREFIX + test_id

    # Clean up any leftover marker from a previous self-test run.
    if _get_setting(key) is not None:
        # Direct delete (no helper for this) — use a small inline query.
        from tools.db.storage import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM kanban_board_settings WHERE key = ?", (key,),
            )
            conn.commit()

    try:
        # 1a) Below threshold
        result = check_and_maybe_restart(test_id, FAILURE_THRESHOLD_FIRST - 1)
        assert result == "none", f"fc<threshold should be 'none', got {result!r}"
        assert _get_setting(key) is None, "below-threshold should not write marker"
        assert len(launches) == 0, "below-threshold must not launch anything"
        print(f"  [OK] fc={FAILURE_THRESHOLD_FIRST - 1} -> 'none', no marker, no launch")

        # 1b) At first threshold (no prior marker) — must trigger restart
        result = check_and_maybe_restart(test_id, FAILURE_THRESHOLD_FIRST)
        assert result == "restarted", f"first crossing should be 'restarted', got {result!r}"
        assert _get_setting(key) == "1", "first crossing should write marker"
        assert len(launches) == 1, f"first crossing should launch 1 ps1, got {len(launches)}"
        print(f"  [OK] fc={FAILURE_THRESHOLD_FIRST} -> 'restarted', marker written, ps1 launched")

        # 1c) Subsequent failures between thresholds — must be no-op
        for fc in range(FAILURE_THRESHOLD_FIRST + 1, FAILURE_THRESHOLD_HALT):
            result = check_and_maybe_restart(test_id, fc)
            assert result == "none", (
                f"fc={fc} (between thresholds, post-restart) should be 'none', "
                f"got {result!r}"
            )
        assert len(launches) == 1, (
            f"between-threshold calls must not re-launch, got {len(launches)}"
        )
        print(f"  [OK] fc in ({FAILURE_THRESHOLD_FIRST+1}..{FAILURE_THRESHOLD_HALT-1}) -> 'none'")

        # 1d) At halt threshold — must trigger halt
        result = check_and_maybe_restart(test_id, FAILURE_THRESHOLD_HALT)
        assert result == "halted", f"halt crossing should be 'halted', got {result!r}"
        assert len(launches) == 2, f"halt crossing should launch 1 more ps1, got {len(launches)} total"
        print(f"  [OK] fc={FAILURE_THRESHOLD_HALT} -> 'halted', 2nd ps1 launched")

        # 1e) Idempotent — repeated calls at halt threshold must not re-launch
        result = check_and_maybe_restart(test_id, FAILURE_THRESHOLD_HALT + 5)
        assert result == "halted", "halt is idempotent"
        assert len(launches) == 2, "halt must be idempotent — no 3rd launch"
        print(f"  [OK] fc={FAILURE_THRESHOLD_HALT + 5} -> 'halted' (idempotent, no re-launch)")

        # 1f) DIFFERENT task id at first threshold — must trigger ANOTHER restart
        other_id = test_id + "-other"
        other_key = RECOVERY_KEY_PREFIX + other_id
        if _get_setting(other_key) is not None:
            from tools.db.storage import get_connection
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM kanban_board_settings WHERE key = ?", (other_key,),
                )
                conn.commit()
        result = check_and_maybe_restart(other_id, FAILURE_THRESHOLD_FIRST)
        assert result == "restarted", "different task should trigger its own restart"
        assert _get_setting(other_key) == "1", "different task should get its own marker"
        assert len(launches) == 3, f"different task should launch 1 more ps1, got {len(launches)}"
        print("  [OK] different task id -> independent restart (3 total launches)")

        # Clean up test markers
        from tools.db.storage import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM kanban_board_settings WHERE key IN (?, ?)",
                (key, other_key),
            )
            conn.commit()
        print(f"  [OK] cleaned up test markers ({key}, {other_key})")

    finally:
        globals()["_launch_detached_ps1"] = orig_launch  # type: ignore[assignment]
        shim_module = sys.modules.get("tools.kanban.recovery_guard")
        if shim_module is not None and shim_module is not sys.modules.get("__main__"):
            shim_module._launch_detached_ps1 = orig_launch  # type: ignore[assignment]

    print("=" * 60)
    print("self-test PASSED")
    print("=" * 60)
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return _selftest()
    print("Usage: python tools/kanban/recovery_guard.py --self-test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
