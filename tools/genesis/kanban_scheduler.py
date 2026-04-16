# CUI // SP-CTI
"""Kanban Scheduler — standalone process that runs the kanban reflex on a loop.

Designed to run as a persistent background service (Windows Task Scheduler,
systemd, or nohup). Calls the kanban reflex every INTERVAL seconds to:
1. Poll Telegram for incoming commands
2. Promote due/backlog tasks to in_progress
3. Dispatch tasks to Claude CLI
4. Check for completed tasks and notify

Usage:
    python tools/genesis/kanban_scheduler.py [--interval 60]
    nohup python tools/genesis/kanban_scheduler.py > .tmp/kanban_scheduler.log 2>&1 &
"""

import argparse
import logging
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [kanban] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Kanban Scheduler")
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Seconds between kanban reflex cycles (default: 60)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one cycle and exit (for Task Scheduler)",
    )
    args = parser.parse_args()

    # Single-instance guard via PID lockfile. Handles two cases:
    #  (1) startup race — if another scheduler's PID is in the lockfile and
    #      that process is alive, exit immediately.
    #  (2) concurrent starts — re-check the lockfile each cycle; if another
    #      instance took ownership, exit.
    # --once bypasses this so one-shot/test runs always work.
    LOCK_PATH = BASE_DIR / ".tmp" / "kanban_scheduler.pid"
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

    def _lock_owner_alive() -> int:
        """Return the PID of a live owner, or 0 if lockfile is stale/missing/us."""
        import os
        try:
            owner_pid = int(LOCK_PATH.read_text(encoding="utf-8").strip())
        except Exception:
            return 0
        if owner_pid == os.getpid():
            return 0
        try:
            import psutil as _ps
            if _ps.pid_exists(owner_pid):
                p = _ps.Process(owner_pid)
                if "kanban_scheduler" in " ".join(p.cmdline()):
                    return owner_pid
        except Exception:
            pass
        return 0

    def _take_lock() -> bool:
        import os
        try:
            LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
            return True
        except Exception:
            return False

    if not args.once:
        owner = _lock_owner_alive()
        if owner:
            logger.info(
                "Another kanban scheduler is alive (pid=%d). Exiting to avoid "
                "duplicate dispatch.", owner,
            )
            return
        if not _take_lock():
            logger.warning("Failed to take lockfile — starting anyway")

    # Load .env for Telegram bot token, API keys, etc.
    try:
        from dotenv import load_dotenv

        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass

    from tools.genesis.reflexes.kanban import run as kanban_run

    dummy_config = {"enabled": True, "risk_tier": "green"}
    dummy_trust = None

    # ── STARTUP RECOVERY: reset orphaned in_progress tasks ──────────
    # If the scheduler crashed, tasks may be stuck in in_progress with
    # no running subprocess.  Reset them to backlog so they get re-dispatched.
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            stuck = conn.execute("SELECT id, title FROM kanban_tasks WHERE status = 'in_progress'").fetchall()
            if stuck:
                for row in stuck:
                    conn.execute(
                        "UPDATE kanban_tasks SET status = 'backlog', updated_at = datetime('now') WHERE id = ?",
                        (row["id"],),
                    )
                conn.commit()
                logger.info(
                    "Startup recovery: reset %d orphaned in_progress tasks to backlog",
                    len(stuck),
                )
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Startup recovery failed: %s", exc)

    if args.once:
        logger.info("Running single kanban cycle...")
        result = kanban_run(dummy_config, dummy_trust)
        details = result.get("details", {})
        logger.info(
            "Cycle complete: activated=%s, completed=%s, running=%s",
            details.get("tasks_activated", 0),
            len(details.get("completed_this_cycle", [])),
            len(details.get("running", [])),
        )
        return

    logger.info("Kanban scheduler started (interval=%ds)", args.interval)
    logger.info("Press Ctrl+C to stop")

    # guard-6: Orphan cleanup on startup — kill any Claude CLI subprocesses
    # left over from a previous run that may have crashed.
    _cleanup_orphan_processes()

    # guard-6: Heartbeat file for dashboard health monitoring
    heartbeat_path = BASE_DIR / ".tmp" / "kanban_scheduler.heartbeat"
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)

    cycle = 0
    while True:
        cycle += 1
        # In-loop lock recheck: if a different live scheduler now owns the
        # lockfile (simultaneous start race where we all passed the initial
        # check), surrender. Rewrite our PID if the file is stale/missing.
        owner = _lock_owner_alive()
        if owner:
            logger.info(
                "Cycle %d: another scheduler took ownership (pid=%d). Exiting.",
                cycle, owner,
            )
            return
        _take_lock()  # refresh our ownership (idempotent)

        # guard-6: Write heartbeat BEFORE work so a hung cycle is still detectable
        try:
            heartbeat_path.write_text(
                f"{cycle}\n{time.time()}\n",
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("heartbeat write failed: %s", exc)

        try:
            result = kanban_run(dummy_config, dummy_trust)
            details = result.get("details", {})
            status = details.get("status", "ok")
            activated = details.get("tasks_activated", 0)
            completed = details.get("completed_this_cycle", [])
            running = details.get("running", [])

            if status == "token_retry":
                retry_count = details.get("retry_count", 0)
                logger.info(
                    "Cycle %d: RETRYING token-exhausted task %s (attempt %d)",
                    cycle,
                    details.get("task_id", "?"),
                    retry_count,
                )
            elif activated or completed or running:
                logger.info(
                    "Cycle %d: status=%s activated=%s completed=%s running=%s",
                    cycle,
                    status,
                    activated,
                    len(completed),
                    len(running),
                )
            elif cycle % 10 == 0:
                # Heartbeat every 10 cycles
                logger.info("Cycle %d: idle (no due tasks)", cycle)
        except Exception as exc:
            # guard-6: log FULL traceback, never exit on transient errors
            import traceback
            logger.error(
                "Cycle %d error: %s\n%s", cycle, exc, traceback.format_exc()
            )

        time.sleep(args.interval)


def _cleanup_orphan_processes() -> None:
    """guard-6: Kill orphaned Claude CLI subprocesses from previous runs.

    Scans .tmp/kanban/ for *.pid files left over from crashed scheduler
    runs. Checks if the PID is still running and belongs to a claude
    process; if so, terminates it and removes the pid file.
    """
    pid_dir = BASE_DIR / ".tmp" / "kanban"
    if not pid_dir.exists():
        return
    killed = 0
    for pid_file in pid_dir.glob("*.pid"):
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except Exception:
            pid_file.unlink(missing_ok=True)
            continue
        try:
            import subprocess
            import os
            # Check if process still running (Windows + Unix)
            if os.name == "nt":
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True, text=True, timeout=5,
                )
                if str(pid) in result.stdout and "claude" in result.stdout.lower():
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True, timeout=5,
                    )
                    killed += 1
            else:
                try:
                    os.kill(pid, 0)
                    os.kill(pid, 9)
                    killed += 1
                except ProcessLookupError:
                    pass
        except Exception as exc:
            logger.debug("orphan cleanup error for pid %s: %s", pid, exc)
        finally:
            pid_file.unlink(missing_ok=True)
    if killed:
        logger.warning("guard-6: killed %d orphaned Claude CLI processes", killed)


if __name__ == "__main__":
    main()
