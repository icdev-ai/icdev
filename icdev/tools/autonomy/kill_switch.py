#!/usr/bin/env python3
# CUI // SP-CTI
"""Kill Switch — immediate halt for all autonomous behavior (D-AE-6).

Defense in depth: checks BOTH env var AND lockfile.
Either one active = all autonomy halted.
Deactivation requires explicit human command.

Usage:
    python tools/autonomy/kill_switch.py --activate
    python tools/autonomy/kill_switch.py --deactivate
    python tools/autonomy/kill_switch.py --status --json
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
LOCKFILE = BASE_DIR / ".tmp" / "autonomy" / "kill.lock"
ENV_VAR = "ICDEV_AUTONOMY_KILL"


def is_killed() -> bool:
    """Check if kill switch is active. Called at top of every autonomy loop."""
    # Check 1: env var
    if os.environ.get(ENV_VAR, "").lower() in ("true", "1", "yes"):
        return True
    # Check 2: lockfile
    if LOCKFILE.exists():
        return True
    return False


def activate(reason: str = "manual") -> dict:
    """Activate kill switch — halt all autonomous behavior immediately."""
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    LOCKFILE.write_text(
        json.dumps(
            {
                "activated_at": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
            }
        ),
        encoding="utf-8", newline="",
    )
    os.environ[ENV_VAR] = "true"
    return {"status": "activated", "lockfile": str(LOCKFILE), "env_var": ENV_VAR}


def deactivate() -> dict:
    """Deactivate kill switch — resume autonomous behavior."""
    if LOCKFILE.exists():
        LOCKFILE.unlink()
    os.environ.pop(ENV_VAR, None)
    return {"status": "deactivated"}


def status() -> dict:
    """Get kill switch status."""
    env_active = os.environ.get(ENV_VAR, "").lower() in ("true", "1", "yes")
    lock_active = LOCKFILE.exists()
    lock_info = None
    if lock_active:
        try:
            lock_info = json.loads(LOCKFILE.read_text(encoding="utf-8"))
        except Exception:
            lock_info = {"error": "Could not read lockfile"}

    return {
        "killed": env_active or lock_active,
        "env_var_active": env_active,
        "lockfile_active": lock_active,
        "lockfile_path": str(LOCKFILE),
        "lockfile_info": lock_info,
    }


def main():
    parser = argparse.ArgumentParser(description="Autonomy Kill Switch (D-AE-6)")
    parser.add_argument("--activate", action="store_true", help="Halt all autonomy")
    parser.add_argument("--deactivate", action="store_true", help="Resume autonomy")
    parser.add_argument("--status", action="store_true", help="Check status")
    parser.add_argument("--reason", type=str, default="manual", help="Reason for activation")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.activate:
        result = activate(reason=args.reason)
        print(json.dumps(result, indent=2) if args.json else f"Kill switch ACTIVATED: {result}")
    elif args.deactivate:
        result = deactivate()
        print(json.dumps(result, indent=2) if args.json else "Kill switch DEACTIVATED")
    elif args.status:
        result = status()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"  Status: {'KILLED' if result['killed'] else 'ACTIVE'}")
            print(f"  Env var ({ENV_VAR}): {result['env_var_active']}")
            print(f"  Lockfile: {result['lockfile_active']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
