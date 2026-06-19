#!/usr/bin/env python3
# CUI // SP-CTI
"""launch_dashboard.py — One-shot launcher for the ICDEV™ Dashboard + Kanban Scheduler.

Usage:
    python tools/launch_dashboard.py [--no-kill] [--wait 30]

Steps:
    1. Kill all python.exe processes (stale dashboard, scheduler, daemon, etc.).
    2. Verify PostgreSQL is reachable (required by kanban scheduler).
    3. Start Dashboard (tools/dashboard/app.py) on ICDEV_DASHBOARD_PORT.
    4. Poll /health until the dashboard is responding.
    5. Start Kanban Scheduler (tools/genesis/kanban_scheduler.py).
    6. Print PIDs and URLs.

Logs are written to .tmp/dashboard*.log and .tmp/kanban_scheduler*.log.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# NOTE: we deliberately do NOT add BASE_DIR to sys.path here.
# The repo's `tools/` package shadows stdlib names (http, logging, etc.).
# Child processes get PYTHONPATH explicitly; the parent script only uses
# stdlib + pip packages (dotenv, psycopg2).

# ---------------------------------------------------------------------------
# Environment / .env
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

DASHBOARD_PORT = int(os.environ.get("ICDEV_DASHBOARD_PORT", "5050"))
DASHBOARD_HOST = os.environ.get("ICDEV_DASHBOARD_HOST", "127.0.0.1")
PG_HOST = os.environ.get("ICDEV_PG_HOST", "127.0.0.1")
if PG_HOST == "localhost":
    PG_HOST = "127.0.0.1"
PG_PORT = int(os.environ.get("ICDEV_PG_PORT", "5432"))
PG_DB = os.environ.get("ICDEV_PG_DB", "icdev")
PG_USER = os.environ.get("ICDEV_PG_USER", "icdev")
PG_PASSWORD = os.environ.get("ICDEV_PG_PASSWORD", "")

LOG_DIR = BASE_DIR / ".tmp"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_windows() -> bool:
    return sys.platform == "win32"


def _kill_all_python() -> None:
    """Terminate every python.exe process. Uses OS-native commands."""
    if _is_windows():
        cmd = ["taskkill", "/f", "/im", "python.exe"]
    else:
        cmd = ["pkill", "-9", "python"]
    print(f"[launch] Killing all Python processes: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, capture_output=True, check=False)
    except FileNotFoundError:
        # pkill might not exist; fall back to killall
        if not _is_windows():
            try:
                subprocess.run(["killall", "-9", "python"], capture_output=True, check=False)
            except FileNotFoundError:
                pass
    # Allow processes to release ports/files
    time.sleep(2)


def _check_postgresql() -> bool:
    """Verify PG is accepting connections. Returns True/False."""
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname=PG_DB,
            user=PG_USER,
            password=PG_PASSWORD,
            connect_timeout=5,
        )
        conn.close()
        return True
    except Exception as exc:
        print(f"[launch] PostgreSQL check FAILED: {exc}")
        return False


def _start_process(
    script: str,
    stdout_log: Path,
    stderr_log: Path,
    extra_args: list[str] | None = None,
) -> subprocess.Popen:
    """Launch a python script in the background with redirected logs."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BASE_DIR)
    # Prevent stale bytecode from shadowing edits in long-running processes
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    args = [sys.executable, script]
    if extra_args:
        args.extend(extra_args)

    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    stderr_log.parent.mkdir(parents=True, exist_ok=True)

    with open(stdout_log, "w", encoding="utf-8") as out_fh, \
         open(stderr_log, "w", encoding="utf-8") as err_fh:
        proc = subprocess.Popen(
            args,
            stdout=out_fh,
            stderr=err_fh,
            env=env,
            cwd=str(BASE_DIR),
        )
    return proc


def _poll_health(timeout: int = 30) -> bool:
    """Wait for dashboard TCP port to become accepting (Flask is up)."""
    addr = (DASHBOARD_HOST, DASHBOARD_PORT)
    print(f"[launch] Polling dashboard port {addr} ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(addr, timeout=2):
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _cleanup_stale_lockfiles() -> None:
    """Remove stale scheduler lockfiles and orphaned kanban PID files."""
    lock = LOG_DIR / "kanban_scheduler.pid"
    if lock.exists():
        lock.unlink(missing_ok=True)
        print("[launch] Removed stale kanban_scheduler.pid lockfile")
    kanban_dir = LOG_DIR / "kanban"
    if kanban_dir.exists():
        for pid_file in kanban_dir.glob("*.pid"):
            pid_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Launch ICDEV Dashboard + Kanban Scheduler")
    parser.add_argument("--no-kill", action="store_true", help="Skip killing existing Python processes")
    parser.add_argument("--wait", type=int, default=30, help="Seconds to wait for dashboard health (default: 30)")
    parser.add_argument("--json", action="store_true", help="Emit JSON status output")
    args = parser.parse_args()

    status: dict = {"ok": False, "dashboard_pid": None, "scheduler_pid": None, "error": None}

    # 1. Kill
    if not args.no_kill:
        _kill_all_python()
    else:
        print("[launch] --no-kill set; skipping process termination")

    # 2. PostgreSQL check
    if not _check_postgresql():
        msg = (
            "PostgreSQL is not reachable. "
            f"Host={PG_HOST}:{PG_PORT} DB={PG_DB} User={PG_USER}. "
            "Ensure PG is running and .env credentials are correct."
        )
        status["error"] = msg
        print(f"[launch] ERROR: {msg}")
        if args.json:
            print(json.dumps(status))
        return 1
    print("[launch] PostgreSQL connectivity OK")

    # 3. Clean stale locks
    _cleanup_stale_lockfiles()

    # 4. Start Dashboard
    dash_stdout = LOG_DIR / "dashboard.log"
    dash_stderr = LOG_DIR / "dashboard_err.log"
    dash_proc = _start_process(
        str(BASE_DIR / "tools" / "dashboard" / "app.py"),
        dash_stdout,
        dash_stderr,
    )
    status["dashboard_pid"] = dash_proc.pid
    print(f"[launch] Dashboard started (PID {dash_proc.pid}) → http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
    print(f"[launch] Dashboard logs: {dash_stdout} | {dash_stderr}")

    # 5. Wait for dashboard health
    if _poll_health(timeout=args.wait):
        print("[launch] Dashboard health check PASSED")
    else:
        msg = f"Dashboard did not respond on /health within {args.wait}s. Check {dash_stderr}"
        status["error"] = msg
        print(f"[launch] WARNING: {msg}")

    # 6. Start Kanban Scheduler
    sched_stdout = LOG_DIR / "kanban_scheduler.log"
    sched_stderr = LOG_DIR / "kanban_scheduler_err.log"
    sched_proc = _start_process(
        str(BASE_DIR / "tools" / "genesis" / "kanban_scheduler.py"),
        sched_stdout,
        sched_stderr,
    )
    status["scheduler_pid"] = sched_proc.pid
    status["ok"] = True
    print(f"[launch] Kanban Scheduler started (PID {sched_proc.pid})")
    print(f"[launch] Scheduler logs: {sched_stdout} | {sched_stderr}")

    # 7. Summary
    print("\n[launch] All services started:")
    print(f"  Dashboard       : http://{DASHBOARD_HOST}:{DASHBOARD_PORT}  (PID {dash_proc.pid})")
    print(f"  Kanban Scheduler: (PID {sched_proc.pid})")
    if args.json:
        print(json.dumps(status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
