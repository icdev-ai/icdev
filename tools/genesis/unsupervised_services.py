# CUI // SP-CTI
"""Start, stop and report the two services `/start` launches OUTSIDE the supervisor.

WHY THIS EXISTS (omx-linux-01). The SaaS portal (`tools/saas/api_gateway.py`,
port 8443) and the CI poll trigger (`tools/ci/triggers/poll_trigger.py`) are not
children of `tools/genesis/launch.py`: nothing respawns them, and `/start` and
`/stop` handled them with `Start-Process` and a `Get-CimInstance` filter --
PowerShell only, so a Linux host could start the stack and never stop it
cleanly.

This module is the one OS-agnostic way to do both, by PIDFILE:

  * ``--start`` launches each service detached, logs to ``.tmp/<log>.log``, and
    records its pid in ``.tmp/services/<name>.pid``. A service already running
    (pidfile verified, or found by argv) is ADOPTED, never started twice.
  * ``--stop`` stops the pid in the pidfile only after VERIFYING its command
    line names the service's script -- a reused pid is refused and left alone.
    With no usable pidfile it falls back to the rule `/stop` already used: a
    process whose argv names the script (a token's basename, not a substring,
    so a shell that merely typed the name does not qualify). Never by process
    name, never `taskkill /im` / `pkill`.
  * ``--status`` reports each pid and whether it is alive.

Exit codes: 0 done and verified · 1 a survivor remains · 2 unmeasurable (no
psutil) -- never a clean answer.

Usage:
    python -m tools.genesis.unsupervised_services --status
    python -m tools.genesis.unsupervised_services --start [--only portal]
    python -m tools.genesis.unsupervised_services --stop [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 -- fixed argv of first-party scripts, shell=False
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from icdev.core.paths import repo_root
from tools.genesis.shutdown_dashboard import DEFAULT_GRACE, PsutilProcApi, _stop


@dataclass(frozen=True)
class Service:
    name: str
    argv: Tuple[str, ...]   # script first, relative to the repo root
    log: str                # .tmp/<log>.log and .tmp/<log>_err.log

    @property
    def script(self) -> str:
        return Path(self.argv[0]).name


SERVICES = (
    Service("portal", ("tools/saas/api_gateway.py", "--port", "8443", "--debug"), "api_gateway"),
    Service("poll_trigger", ("tools/ci/triggers/poll_trigger.py",), "poll_trigger"),
)


def pid_dir(base: Path) -> Path:
    return base / ".tmp" / "services"


def _read_pid(path: Path) -> Optional[int]:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _verified(api, pid: Optional[int], svc: Service) -> bool:
    """The pid is alive AND its argv names this service's script."""
    return bool(pid) and api.pid_exists(pid) and pid in api.find_by_cmdline(svc.script)


def locate(api, svc: Service, base: Path) -> Dict[str, Any]:
    """Where the service is: the pidfile's pid if verified, else argv matches."""
    pf = pid_dir(base) / f"{svc.name}.pid"
    pid = _read_pid(pf)
    if _verified(api, pid, svc):
        return {"pidfile": str(pf), "pids": [pid], "source": "pidfile"}
    rec: Dict[str, Any] = {"pidfile": str(pf), "pids": api.find_by_cmdline(svc.script),
                           "source": "argv"}
    if pid is not None:
        rec["stale_pidfile_pid"] = pid   # dead or reused -- never touched
    return rec


def _spawn(svc: Service, base: Path) -> int:
    tmp = base / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(base), PYTHONIOENCODING="utf-8")
    kwargs: Dict[str, Any] = {"cwd": str(base), "env": env, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True                  # survives the launching shell
    with open(tmp / f"{svc.log}.log", "ab") as out, open(tmp / f"{svc.log}_err.log", "ab") as err:
        proc = subprocess.Popen([sys.executable, *svc.argv], stdout=out, stderr=err,  # nosec B603
                                **kwargs)
    return proc.pid


def start(api, services, base: Path, dry_run: bool = False, spawn=_spawn) -> List[Dict[str, Any]]:
    out = []
    for svc in services:
        loc = locate(api, svc, base)
        pf = Path(loc["pidfile"])
        if loc["pids"]:
            rec = {"name": svc.name, "action": "adopted", "pid": loc["pids"][0], "source": loc["source"]}
            if not dry_run and loc["source"] == "argv":
                pf.parent.mkdir(parents=True, exist_ok=True)
                pf.write_text(str(loc["pids"][0]), encoding="utf-8")
        elif dry_run:
            rec = {"name": svc.name, "action": "would_start", "pid": None}
        else:
            pid = spawn(svc, base)
            pf.parent.mkdir(parents=True, exist_ok=True)
            pf.write_text(str(pid), encoding="utf-8")
            rec = {"name": svc.name, "action": "started", "pid": pid,
                   "log": f".tmp/{svc.log}.log"}
        out.append(rec)
    return out


def stop(api, services, base: Path, dry_run: bool = False,
         grace: float = DEFAULT_GRACE) -> List[Dict[str, Any]]:
    out = []
    for svc in services:
        loc = locate(api, svc, base)
        results = [_stop(api, pid, grace, dry_run) for pid in loc["pids"]]
        rec = {"name": svc.name, "source": loc["source"], "results": results,
               "survivors": [r["pid"] for r in results if r["dead"] is False]}
        if "stale_pidfile_pid" in loc:
            rec["stale_pidfile_pid"] = loc["stale_pidfile_pid"]
        pf = Path(loc["pidfile"])
        if not dry_run and not rec["survivors"] and pf.exists():
            pf.unlink()   # only once every pid it could name is confirmed dead
        out.append(rec)
    return out


def status(api, services, base: Path) -> List[Dict[str, Any]]:
    return [{"name": s.name, **locate(api, s, base)} for s in services]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Start/stop the portal and poll trigger by pidfile")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start", action="store_true")
    mode.add_argument("--stop", action="store_true")
    mode.add_argument("--status", action="store_true")
    ap.add_argument("--only", choices=[s.name for s in SERVICES], action="append")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--grace", type=float, default=DEFAULT_GRACE)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        api = PsutilProcApi()
    except ImportError:
        print("psutil is not installed -- the process table cannot be read; nothing touched")
        return 2
    base = repo_root()
    services = [s for s in SERVICES if not args.only or s.name in args.only]
    if args.start:
        rows = start(api, services, base, dry_run=args.dry_run)
    elif args.stop:
        rows = stop(api, services, base, dry_run=args.dry_run, grace=args.grace)
    else:
        rows = status(api, services, base)
    rc = 1 if any(r.get("survivors") for r in rows) else 0
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(json.dumps(r))
        if rc:
            print("SURVIVORS remain -- confirm each pid's command line, then stop THAT pid")
    return rc


if __name__ == "__main__":
    sys.exit(main())
