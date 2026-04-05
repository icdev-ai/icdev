#!/usr/bin/env python3
# CUI // SP-CTI
"""Scout Windows Task Scheduler Installer.

Creates/removes a Windows Task Scheduler job that triggers Scout daily.
The task runs `python tools/scout/daemon.py --once` at 9:00 AM.
Scout handles retry logic internally if CPU/VRAM is busy.

Usage:
    python tools/scout/install_scheduler.py --install --json
    python tools/scout/install_scheduler.py --uninstall --json
    python tools/scout/install_scheduler.py --status --json
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TASK_NAME = "ICDEV_Scout_Daily"


def _run_cmd(cmd: list, timeout: int = 30) -> dict:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "ok": result.returncode == 0,
        }
    except Exception as exc:
        return {"returncode": -1, "stdout": "", "stderr": str(exc), "ok": False}


def _find_python() -> str:
    """Find the Python executable path."""
    return sys.executable


def install(python_path: str = None) -> dict:
    """Create the Windows Task Scheduler job."""
    python = python_path or _find_python()
    script = str(BASE_DIR / "tools" / "scout" / "daemon.py")
    working_dir = str(BASE_DIR)

    # Build the command: python tools/scout/daemon.py --once
    task_cmd = f'"{python}" "{script}" --once'

    # Create scheduled task: daily at 9:00 AM
    cmd = [
        "schtasks",
        "/Create",
        "/TN",
        TASK_NAME,
        "/TR",
        task_cmd,
        "/SC",
        "DAILY",
        "/ST",
        "09:00",
        "/RL",
        "LIMITED",  # Run with limited privileges
        "/F",  # Force overwrite if exists
    ]

    result = _run_cmd(cmd)

    if result["ok"]:
        return {
            "status": "installed",
            "task_name": TASK_NAME,
            "schedule": "Daily at 09:00",
            "command": task_cmd,
            "working_dir": working_dir,
            "python": python,
            "message": f"Task '{TASK_NAME}' created. Scout will run daily at 9:00 AM.",
        }
    else:
        return {
            "status": "failed",
            "error": result["stderr"] or result["stdout"],
            "hint": "Try running as Administrator if permission denied",
        }


def uninstall() -> dict:
    """Remove the Windows Task Scheduler job."""
    result = _run_cmd(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if result["ok"]:
        return {"status": "uninstalled", "task_name": TASK_NAME}
    else:
        return {"status": "failed", "error": result["stderr"] or result["stdout"]}


def status() -> dict:
    """Check if the task exists and its last run time."""
    result = _run_cmd(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"])
    if not result["ok"]:
        return {"status": "not_installed", "task_name": TASK_NAME}

    info = {"status": "installed", "task_name": TASK_NAME}
    for line in result["stdout"].split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().lower().replace(" ", "_")
            val = val.strip()
            if key in ("last_run_time", "next_run_time", "status", "scheduled_task_state"):
                info[key] = val
    return info


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Scout Task Scheduler installer")
    parser.add_argument("--install", action="store_true", help="Install scheduled task")
    parser.add_argument("--uninstall", action="store_true", help="Remove scheduled task")
    parser.add_argument("--status", action="store_true", help="Check task status")
    parser.add_argument("--python-path", help="Override Python executable path")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    if args.install:
        result = install(args.python_path)
    elif args.uninstall:
        result = uninstall()
    elif args.status:
        result = status()
    else:
        parser.error("Specify --install, --uninstall, or --status")
        return

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        for k, v in result.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
