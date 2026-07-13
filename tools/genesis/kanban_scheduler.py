from __future__ import annotations

# CUI // SP-CTI
"""Kanban Scheduler -- standalone process that runs the kanban reflex on a loop.

Designed to run as a persistent background service (Windows Task Scheduler,
systemd, or nohup). Calls the kanban reflex every INTERVAL seconds to:
1. Poll Telegram for incoming commands
2. Promote due/backlog tasks to in_progress
3. Dispatch tasks to Claude CLI
4. Check for completed tasks and notify

Usage:
    python -B tools/genesis/kanban_scheduler.py [--interval 60]
    PYTHONDONTWRITEBYTECODE=1 nohup python -B tools/genesis/kanban_scheduler.py >> .tmp/kanban_scheduler.log 2>&1 &

NOTE: Always use ``python -B`` (or set PYTHONDONTWRITEBYTECODE=1) to prevent
stale .pyc bytecache from shadowing code changes. The scheduler is a
long-running process that loads modules once at startup -- without -B, edits
to kanban.py (e.g. timeout adjustments) require both a restart AND manual
__pycache__ clearing.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402 — must follow sys.path patch

# Force UTF-8 on both stdout and stderr so emoji/Unicode in task titles never
# silently kill the logger on Windows (cp1252 streams raise UnicodeEncodeError
# and then stop writing entirely for the rest of the process lifetime).
_stderr_utf8 = open(sys.stderr.fileno(), mode="w", encoding="utf-8", errors="replace", closefd=False)
_stdout_utf8 = open(sys.stdout.fileno(), mode="w", encoding="utf-8", errors="replace", closefd=False)
sys.stdout = _stdout_utf8
sys.stderr = _stderr_utf8
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [kanban] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=_stderr_utf8,
)
logger = get_logger(__name__)



from tools.kanban.gates import is_manual_gate as _is_manual_gate  # noqa: F401

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
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON status output (useful with --once for scripted callers)",
    )
    args = parser.parse_args()

    # Single-instance guard via PID lockfile. Handles two cases:
    #  (1) startup race -- if another scheduler's PID is in the lockfile and
    #      that process is alive, exit immediately.
    #  (2) concurrent starts -- re-check the lockfile each cycle; if another
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
            logger.warning("Failed to take lockfile -- starting anyway")

    # Load .env for Telegram bot token, API keys, etc.
    try:
        from dotenv import load_dotenv

        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass

    from tools.genesis.reflexes.kanban import run as kanban_run
    from tools.monitoring.reflex_observer import observe

    dummy_config = {"enabled": True, "risk_tier": "green"}
    dummy_trust = None

    # -- STARTUP RECOVERY: reset orphaned in_progress tasks ----------
    # Power outage or daemon restart leaves tasks stuck in in_progress.
    # Reset them to backlog WITHOUT incrementing failure_count -- an
    # interruption is not a task failure and must not trigger decomposition.
    # Git commits on kanban/{task_id} branches are preserved on disk;
    # _write_prompt_file will include a "Resume Context" section so Claude
    # picks up exactly where it left off.
    try:
        from tools.db.storage import get_connection
        from datetime import datetime, timezone

        conn = get_connection()
        stuck_ids: list[tuple[str, str | None]] = []
        try:
            stuck = conn.execute(
                "SELECT id, title FROM kanban_tasks WHERE status = 'in_progress'"
            ).fetchall()
            if stuck:
                now_iso = datetime.now(timezone.utc).isoformat()
                # A MANUAL-MODE GATE (prem-gate-00 et al.) is held in_progress
                # FOREVER by design — it is not an "interrupted task", it is the
                # thing stopping its dependents from auto-dispatching. Resetting it
                # to backlog on every scheduler restart released the work it was
                # holding. The reflex's own startup recovery already exempts gates
                # (PR #241); this SECOND, independent recovery in the scheduler
                # entrypoint did not, so the gate died on every restart anyway.
                stuck_ids = [
                    (dict(r)["id"], dict(r).get("title")) for r in stuck
                    if not _is_manual_gate(dict(r)["id"], dict(r).get("title"))
                ]
                # Individual UPDATE per interrupted task -- no failure penalty.
                # One query per ID avoids dynamic IN-clause string construction.
                for tid, _ in stuck_ids:
                    conn.execute(
                        "UPDATE kanban_tasks SET status = 'backlog', updated_at = %s "
                        "WHERE id = %s AND status = 'in_progress'",
                        [now_iso, tid],
                    )
                conn.commit()
                logger.info(
                    "Startup recovery: reset %d interrupted task(s) to backlog "
                    "(failure_count unchanged -- interruption is not a failure): %s",
                    len(stuck_ids),
                    ", ".join(tid for tid, _ in stuck_ids),
                )
        finally:
            conn.close()

        # Fire Telegram for each interrupted task (best-effort, outside DB conn).
        for tid, title in stuck_ids:
            display = (title or tid)[:60]
            try:
                from tools.notifications.adapters.telegram import send as tg_send
                tg_send(
                    f"RESTARTED: {display}",
                    (
                        f"Task '{display}' ({tid}) was interrupted (power outage or "
                        "daemon restart). Reset to backlog for re-dispatch. "
                        "failure_count was NOT changed. Any git commits on the task "
                        "branch are preserved -- Claude will resume from them."
                    ),
                    severity="info",
                )
            except Exception as _tg_exc:
                logger.debug("startup-recovery Telegram send failed: %s", _tg_exc)
    except Exception as exc:
        logger.warning("Startup recovery failed: %s", str(exc).encode("ascii", errors="replace").decode("ascii"))

    # Startup inbox replay — process any messages that arrived while offline.
    _startup_replay_listeners = [
        ("tools.notifications.adapters.telegram_listener", "Telegram"),
        ("tools.notifications.adapters.teams_listener", "Teams"),
        ("tools.notifications.adapters.mattermost_listener", "MatterMost"),
        ("tools.notifications.adapters.github_listener", "GitHub"),
        ("tools.notifications.adapters.gitlab_listener", "GitLab"),
        ("tools.notifications.adapters.skype_listener", "Skype"),
    ]
    for _module_path, _platform_name in _startup_replay_listeners:
        try:
            import importlib as _importlib
            _mod = _importlib.import_module(_module_path)
            _inbox_result = _mod.replay_inbox()
            if _inbox_result["replayed"] or _inbox_result["failed"]:
                logger.info(
                    "Startup inbox replay [%s]: replayed=%d failed=%d",
                    _platform_name,
                    _inbox_result["replayed"],
                    _inbox_result["failed"],
                )
        except Exception as _exc:
            logger.debug("Startup inbox replay [%s] skipped: %s", _platform_name, _exc)

    if args.once:
        logger.info("Running single kanban cycle...")
        # [DISPATCH POINT - once mode]
        reflex_name = kanban_run.__module__.rsplit(".", 1)[-1]
        result = observe(reflex_name, kanban_run, dummy_config, dummy_trust)
        details = result.get("details", {})
        activated = details.get("tasks_activated", 0)
        completed = details.get("completed_this_cycle", [])
        running = details.get("running", [])
        logger.info(
            "Cycle complete: activated=%s, completed=%s, running=%s",
            activated,
            len(completed),
            len(running),
        )
        if args.json:
            print(json.dumps({
                "cycle": 1,
                "status": details.get("status", "ok"),
                "tasks_activated": activated,
                "completed": len(completed),
                "running": len(running),
            }))
        return

    logger.info("Kanban scheduler started (interval=%ds)", args.interval)
    logger.info("Press Ctrl+C to stop")

    # Cross-session coordination: register the scheduler as an agent session so
    # interactive Claude/Cursor sessions can SEE that kanban is active and what
    # it's dispatching (LLM-agnostic; tools/coordination). Best-effort.
    _coord_reg = None
    try:
        import os as _os
        _os.environ.setdefault("ICDEV_SESSION_ID", "kanban-scheduler")
        _os.environ.setdefault("ICDEV_AGENT", "kanban")
        from tools.coordination import session_registry as _coord_reg
        _coord_reg.register(intent="kanban scheduler — dispatching due tasks")
    except Exception:
        _coord_reg = None

    # guard-6: Orphan cleanup on startup -- kill any Claude CLI subprocesses
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

        # Coordination heartbeat — keep the scheduler visible to other sessions.
        try:
            if _coord_reg is not None:
                _coord_reg.heartbeat(intent=f"kanban scheduler — cycle {cycle}")
        except Exception:
            pass

        # Pause gate: manual (dashboard "Pause Scheduler" button) or automatic
        # (an interactive Claude/Cursor session is active). Skips the dispatch /
        # git stash+merge churn so it can't clobber in-flight human work. We still
        # heartbeat above so the scheduler shows as alive-but-paused.
        try:
            from tools.kanban.scheduler_control import should_pause
            _pp = should_pause()
        except Exception:
            _pp = {"paused": False, "mode": ""}
        if _pp.get("paused"):
            logger.info("Cycle %d: paused (%s) — skipping dispatch; %s",
                        cycle, _pp.get("mode"),
                        _pp.get("reason") or _pp.get("intents") or "")
            time.sleep(args.interval)
            continue

        try:
            # [DISPATCH POINT - main loop]
            reflex_name = kanban_run.__module__.rsplit(".", 1)[-1]
            result = observe(reflex_name, kanban_run, dummy_config, dummy_trust)
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
            else:
                # Always log every cycle so a silent hang is immediately visible.
                # Previously only logged every 10 idle cycles — that 9-cycle gap
                # masked scheduler death for up to 9 minutes.
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
            from tools.compat.platform_utils import pid_exists, kill_process, find_pids_by_cmdline
            claude_pids = set(find_pids_by_cmdline("claude"))
            if pid in claude_pids and pid_exists(pid):
                if kill_process(pid, force=True):
                    killed += 1
        except Exception as exc:
            logger.debug("orphan cleanup error for pid %s: %s", pid, exc)
        finally:
            pid_file.unlink(missing_ok=True)
    if killed:
        logger.warning("guard-6: killed %d orphaned Claude CLI processes", killed)


if __name__ == "__main__":
    main()
