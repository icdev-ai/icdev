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
import os
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
# Reused, not reimplemented: #1195 already worked out how to find the repo root
# every worktree shares (`git rev-parse --git-common-dir`) when it fixed the
# pause sentinel. The lockfile needs exactly the same anchor.
from tools.kanban.scheduler_control import _canonical_repo_root  # noqa: E402


def _running_from_linked_worktree() -> bool:
    """True when this file lives in a linked worktree rather than the main checkout.

    In a linked worktree ``--git-dir`` points at ``<main>/.git/worktrees/<name>``
    while ``--git-common-dir`` points at ``<main>/.git``; in the main checkout
    they are the same path.
    """
    try:
        import subprocess as _sp

        def _rev(flag: str) -> str:
            out = _sp.run(["git", "rev-parse", flag], cwd=str(BASE_DIR),
                          capture_output=True, text=True, timeout=10)
            if out.returncode != 0 or not out.stdout.strip():
                return ""
            p = Path(out.stdout.strip())
            return str((p if p.is_absolute() else (BASE_DIR / p)).resolve())

        git_dir, common = _rev("--git-dir"), _rev("--git-common-dir")
        return bool(git_dir and common and git_dir != common)
    except Exception:  # noqa: BLE001 — never let this check stop a legitimate start
        return False


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
    #
    # The path is resolved from the CANONICAL repo root, not BASE_DIR. BASE_DIR
    # comes from __file__, so a scheduler launched from a git worktree used to
    # get its OWN lockfile and the guard could not see the canonical instance --
    # two schedulers then dispatched into the same board and database, one of
    # them running whatever code that worktree happened to hold.
    #
    # Observed 2026-08-02: pid 18132 from C:\ai\icdev and pid 25224 from
    # C:\AI\.wt-tsh-d4-audit5, concurrently, and only the canonical one honoured
    # a pause. #1195 fixed the same bug for the pause sentinel; this is the
    # lockfile half of it, and it reuses that fix's helper rather than
    # reimplementing the resolution.
    LOCK_PATH = _canonical_repo_root() / ".tmp" / "kanban_scheduler.pid"
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

    def _lock_owner_alive() -> int:
        """Return the PID of a live owner, or 0 if lockfile is stale/missing/us.

        Delegates to ``scheduler_control.scheduler_lock_owner_pid`` — the startup
        recovery sweep asks the same question, and two copies of "who owns the
        runner" is exactly the kind of drift that lets one of them say nobody.
        """
        from tools.kanban.scheduler_control import scheduler_lock_owner_pid
        return scheduler_lock_owner_pid()

    def _take_lock() -> bool:
        import os
        try:
            LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8", newline="")
            return True
        except Exception:
            return False

    # A scheduler launched from a linked worktree dispatches WHATEVER CODE that
    # worktree holds against the SHARED board and database. That is stale by
    # construction — the worktree exists because someone is changing things in
    # it — and the shared lock above now stops it from running alongside the
    # canonical instance, but it could still win the race and become the only
    # scheduler. Refusing is the honest answer: nobody wants the board driven by
    # a feature branch.
    #
    # Escape hatch for the deliberate case (testing a scheduler change end to
    # end), because a guard with no override gets deleted rather than respected.
    if not args.once and _running_from_linked_worktree():
        if os.environ.get("KANBAN_ALLOW_WORKTREE_SCHEDULER", "").lower() in ("1", "true", "yes"):
            logger.warning(
                "Starting from a linked worktree (%s) because "
                "KANBAN_ALLOW_WORKTREE_SCHEDULER is set — this dispatches THIS "
                "tree's code against the shared board.", BASE_DIR,
            )
        else:
            msg = (
                "Refusing to start: this scheduler lives in a linked git worktree\n"
                f"  worktree      : {BASE_DIR}\n"
                f"  main checkout : {_canonical_repo_root()}\n"
                "It would dispatch THIS tree's code against the shared board. Run it\n"
                "from the main checkout, or set KANBAN_ALLOW_WORKTREE_SCHEDULER=1 if\n"
                "this is deliberate."
            )
            logger.error(msg.replace("\n", " "))
            # Also straight to stderr. get_logger() does not propagate to the
            # root stderr handler configured above, so logger.error alone lands
            # ONLY in .logs/*.ndjson — and a refusal that prints nothing while
            # exiting 0 is indistinguishable from a successful start. Whoever
            # just ran this command needs to see why nothing is scheduling.
            print(msg, file=sys.stderr, flush=True)
            return

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

    # Activate tracing so the spans this process produces are actually recorded.
    #
    # otel_spans had 0 rows on the live board. Measured 2026-08-02, the cause is
    # NOT that the writer is broken — SQLiteTracer.flush() writes correctly on
    # PostgreSQL (translate_sql rewrites its `INSERT OR IGNORE` to an ON CONFLICT
    # form; verified by inserting and reading back a probe span). The cause is
    # that `get_tracer()` returns a ProxyTracer delegating to a NullTracer until
    # something calls `enable_tracing_if_enabled()`, and only tools/dashboard/app.py
    # and tools/mcp/base_server.py ever did.
    #
    # So every `tracer.start_span(...)` in tools/llm/router.py and
    # tools/agent/bedrock_client.py was a silent no-op in this process — the
    # autonomous runner, which is where most LLM traffic on this board happens.
    #
    # Honours ICDEV_TRACING_ENABLED (unset = on). Spans buffer 10-deep and flush
    # at exit, so this is batched writes, not one per span.
    try:
        from tools.observability import enable_tracing_if_enabled

        if enable_tracing_if_enabled() is not None:
            logger.info("tracing enabled — LLM/agent spans will be recorded to otel_spans")
    except Exception as exc:  # noqa: BLE001 — never let telemetry stop the scheduler
        logger.warning("tracing activation skipped: %s", exc)

    from tools.genesis.reflexes.kanban import run as kanban_run
    from tools.monitoring.reflex_observer import observe

    dummy_config = {"enabled": True, "risk_tier": "green"}
    dummy_trust = None

    # -- STARTUP RECOVERY: reset orphaned in_progress tasks ----------
    # Power outage or daemon restart leaves tasks stuck in in_progress, and an
    # orphaned row is invisible to every promotion path -- so the reset stays.
    #
    # What it no longer does is reset a task something is STILL WORKING. This
    # block used to reset every non-gate in_progress row unconditionally, which
    # made restarting the daemon a decision with a cost: a task that had not yet
    # committed lost whatever its session had done. On 2026-08-08 a needed
    # restart was deferred for exactly that reason and ~30 commits of reflex
    # fixes sat undeployed. tools/kanban/startup_recovery.py holds the reset for
    # any task with provable liveness and reports, per reset task, whether its
    # commits survive on kanban/<id> or its work is being discarded.
    #
    # --once runs it too, but respect_foreign_owner is what keeps a one-shot or
    # Task Scheduler invocation from wiping the live daemon's board: the lock
    # check above is skipped for --once, this one is not.
    try:
        from tools.kanban.startup_recovery import recover_interrupted_tasks

        _recovery = recover_interrupted_tasks()
        if _recovery["sweep_skipped"]:
            logger.info("Startup recovery: skipped -- %s", _recovery["reason"])
        else:
            logger.info(
                "Startup recovery: %d in_progress, %d reset, %d held "
                "(failure_count unchanged -- interruption is not a failure)",
                _recovery["swept"], len(_recovery["reset"]), len(_recovery["held"]),
            )
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
        # DISTINCT PER PROCESS (autonomy-sid-01). A fixed id made two
        # schedulers indistinguishable to leases.acquire, which refuses a
        # hard lease only when it is held by ANOTHER session — so the guard
        # against two workers building one task could not see two workers.
        from tools.coordination.service_identity import claim_service_identity
        claim_service_identity("kanban-scheduler", "kanban")
        from tools.coordination import session_registry as _coord_reg
        _coord_reg.register(intent="kanban scheduler — dispatching due tasks")
    except Exception as _reg_exc:  # noqa: BLE001
        _coord_reg = None
        # LOUD, not `pass`. A scheduler the registry cannot see is one the
        # scheduler_heartbeat_is_fresh claim will flag and the supervisor will
        # never restart: on 2026-09-02 pid 29880 ran five hours unregistered
        # and was found by a human noticing the board had not moved.
        logger.warning(
            "kanban scheduler: coordination registration FAILED (%s) -- this "
            "process will not heartbeat in agent_sessions and will read as a "
            "silent scheduler until restarted", _reg_exc,
        )

    # guard-6: Orphan cleanup on startup -- kill any Claude CLI subprocesses
    # left over from a previous run that may have crashed.
    _cleanup_orphan_processes()

    # guard-6: Heartbeat file for dashboard health monitoring
    heartbeat_path = BASE_DIR / ".tmp" / "kanban_scheduler.heartbeat"
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)

    # Pick up our own code changes between cycles. The scheduler dispatches from
    # an in-memory module, so a merged fix stays inert until someone restarts it
    # — the "scheduler runs stale code" failure, which cost two misdiagnoses on
    # 2026-08-09 before anyone checked the process against the tree.
    from tools.genesis import code_reload

    _code_baseline = code_reload.snapshot()
    _started_at = time.time()

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
                encoding="utf-8", newline="",
            )
        except Exception as exc:
            logger.debug("heartbeat write failed: %s", exc)

        # Coordination heartbeat — keep the scheduler visible to other sessions.
        try:
            if _coord_reg is not None:
                _coord_reg.heartbeat(intent=f"kanban scheduler — cycle {cycle}")
            elif cycle % 30 == 0:
                logger.warning(
                    "kanban scheduler: cycle %d running UNREGISTERED -- no "
                    "heartbeat reaches agent_sessions", cycle,
                )
        except Exception as _hb_exc:  # noqa: BLE001
            if cycle % 30 == 0:
                logger.warning(
                    "kanban scheduler: heartbeat failed at cycle %d (%s)",
                    cycle, _hb_exc,
                )

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
                #
                # "idle (no due tasks)" is true in six materially different
                # situations and identical in all of them (kpr-idle-01), so the
                # heartbeat carries the REASON. The diagnosis is recomputed only
                # when it changes or every _IDLE_DIAGNOSIS_EVERY cycles: the
                # heartbeat must stay cheap and unmissable, and a paragraph
                # every 60s trains people to stop reading it.
                reason = _idle_reason(cycle)
                logger.info("Cycle %d: %s", cycle, reason)
        except Exception as exc:
            # guard-6: log FULL traceback, never exit on transient errors
            import traceback
            logger.error(
                "Cycle %d error: %s\n%s", cycle, exc, traceback.format_exc()
            )

        # AFTER the cycle's work, before the sleep: a restart mid-dispatch would
        # abandon a task it had just claimed. Does not return if it re-execs.
        try:
            code_reload.restart_if_code_changed(
                _code_baseline, started_at=_started_at)
        except Exception as _cr_exc:  # noqa: BLE001 — watching must not kill it
            logger.warning("scheduler: code-change check failed: %s", _cr_exc)

        time.sleep(args.interval)


#: Re-diagnose an unchanged idle state this often (cycles). At the default
#: 60s interval that is roughly every 30 minutes.
_IDLE_DIAGNOSIS_EVERY = 30

#: (cycle_last_diagnosed, last_summary) — module state so the reason survives
#: between cycles without re-querying the board every 60 seconds.
_idle_state: tuple = (None, None)


def _idle_reason(cycle: int) -> str:
    """A heartbeat that says WHY, degrading to the old line if it cannot.

    Wrapped whole in a try/except on purpose: this is a diagnostic on the
    liveness heartbeat, and a heartbeat that can be killed by its own
    diagnostic is worse than one that says nothing. Any failure falls back to
    the original wording.
    """
    global _idle_state
    last_cycle, last_summary = _idle_state
    if last_summary is not None and last_cycle is not None:
        if cycle - last_cycle < _IDLE_DIAGNOSIS_EVERY:
            return last_summary
    try:
        from tools.kanban.idle_advisor import diagnose, summary_line

        summary = summary_line(diagnose())
    except Exception as exc:  # noqa: BLE001 — never let the advisor stop the heartbeat
        logger.debug("idle advisor unavailable: %s", exc)
        summary = "idle (no due tasks)"
    _idle_state = (cycle, summary)
    return summary


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
