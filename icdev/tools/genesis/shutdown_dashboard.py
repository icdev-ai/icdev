#!/usr/bin/env python3
# CUI // SP-CTI
"""Stop the ICDEV service stack in the ONE order that does not fight itself.

    python tools/genesis/shutdown_dashboard.py              # stop everything
    python tools/genesis/shutdown_dashboard.py --dry-run    # plan; touch nothing
    python tools/genesis/shutdown_dashboard.py --pause      # also set Manual Build
    python tools/genesis/shutdown_dashboard.py --keep-ft    # leave ICDEV[FT] serving
    python tools/genesis/shutdown_dashboard.py --keep-rt    # leave ICDEV[RT] serving
    python tools/genesis/shutdown_dashboard.py --json

WHY AN ORDER, and why a script. `.claude/commands/start.md` already states the
rule: the kanban scheduler, pr_watcher and genesis daemon are SUPERVISED, so
stopping one by name just makes the supervisor restart it -- and a name filter
is what produced three concurrent pr_watchers. Stop the supervisor instead, and
never kill every python process wholesale. The rule was prose; on 2026-09-03 it had to
be re-derived by hand from the process table before a shutdown. This module is
that derivation, run the same way every time:

  1. SUPERVISOR FIRST. Its pid comes from `.tmp/genesis/launcher.pid` (the lock
     `launcher._acquire_pid_lock` writes), and the pid's command line must
     actually run `tools/genesis/launch.py` -- a reused pid is refused. Its
     children are RECORDED from the process tree before anything is stopped.
  2. THEN ITS CHILDREN, by recorded pid only. On Windows terminate() is
     TerminateProcess: the launcher's own KeyboardInterrupt cleanup never runs,
     so its children orphan rather than stop. Once the supervisor is provably
     gone there is nothing left to respawn them, and only then is stopping them
     by pid safe. Nothing is ever selected by name.
  3. GRANDCHILDREN ARE REPORTED, NOT STOPPED. A scheduler's children are agent
     workers mid-build; killing them discards work (measured 2026-08-29: ~40
     minutes gone). They are listed with their command lines; `--include-workers`
     stops them too, and is a decision, not a default.
  4. THE EXTERNAL SUPERVISORS, EACH THEN ITS CHILD, the same way: ICDEV[FT]
     (`supervise_ft.py` restarts `launch_ft.py` on exit, :5200) and ICDEV[RT]
     (`supervise_rt.py` restarts `launch_rt.py`, :5300). They are declared in
     EXTERNAL_SUPERVISORS -- one table, so a third stack is a row, not a
     branch. `--keep-ft` / `--keep-rt` skip one pair.
  5. VERIFY, never assume: every recorded pid re-tested dead; ports 5050, 5200
     and 5300 re-tested for a listener. A survivor is reported with its pid and
     the exit code says so.
  6. THE STALE LOCK is removed only after the pid it names is confirmed dead --
     the launcher removes it on a clean exit and cannot after a terminate.

UNMEASURED IS NEVER CLEAN. Without psutil the process tree cannot be read, and
a tree that cannot be read is not stopped blind: the run reports `unmeasurable`
and exits 2. `--dry-run` proves every step and touches nothing.

Exit codes: 0 stopped and verified (or already down) | 1 a survivor or a
listener remains | 2 the tree could not be measured.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# sys.path BOOTSTRAP ONLY (kax-conflict-04): the import root, so that
# `python <this file>` finds first-party code. The repo root itself is
# repo_root()'s answer below (xit-decl-03), never this.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)

#: What `launch.py` runs -- the only command line the lock's pid may carry.
SUPERVISOR_FRAGMENTS = ("tools/genesis/launch.py", "tools\\genesis\\launch.py",
                        "tools/genesis/launcher.py", "tools\\genesis\\launcher.py")
#: The external auto-redeploy supervisors, each with the child it restarts on
#: exit and the port that child serves: (key, supervisor script, child script,
#: port). Stopping order is supervisor THEN child, per row; `--keep-<key>`
#: skips a row. Add a stack here, never as a fourth branch in plan_and_run.
EXTERNAL_SUPERVISORS = (
    ("ft", "supervise_ft.py", "launch_ft.py", 5200),
    ("rt", "supervise_rt.py", "launch_rt.py", 5300),
)
EXTERNAL_KEYS = tuple(row[0] for row in EXTERNAL_SUPERVISORS)
#: Ports whose listener must be gone when we are done.
DEFAULT_PORTS = (5050,) + tuple(row[3] for row in EXTERNAL_SUPERVISORS)
DEFAULT_GRACE = 10.0

STATE_STOPPED = "stopped"
STATE_ALREADY_DOWN = "already_down"
STATE_SURVIVORS = "survivors"
STATE_UNMEASURABLE = "unmeasurable"


# --------------------------------------------------------------------------- #
# The process seam. Everything that touches the OS goes through one object so a
# test can script a tree and assert the order without a process in sight.
# --------------------------------------------------------------------------- #
class PsutilProcApi:
    """The real thing. Raises ImportError at construction without psutil."""

    def __init__(self):
        import psutil  # noqa: F401 -- probe

        self._ps = psutil

    def pid_exists(self, pid: int) -> bool:
        return self._ps.pid_exists(pid)

    def cmdline(self, pid: int) -> Optional[str]:
        try:
            return " ".join(self._ps.Process(pid).cmdline())
        except Exception:  # noqa: BLE001 -- gone, or access denied
            return None

    def children(self, pid: int) -> List[int]:
        try:
            return sorted(c.pid for c in self._ps.Process(pid).children(recursive=False))
        except Exception:  # noqa: BLE001
            return []

    def terminate(self, pid: int) -> None:
        try:
            self._ps.Process(pid).terminate()
        except self._ps.NoSuchProcess:
            pass

    def kill(self, pid: int) -> None:
        try:
            self._ps.Process(pid).kill()
        except self._ps.NoSuchProcess:
            pass

    def wait_dead(self, pid: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._ps.pid_exists(pid):
                return True
            time.sleep(0.25)
        return not self._ps.pid_exists(pid)

    def find_by_cmdline(self, fragment: str) -> List[int]:
        """Processes whose argv NAMES the script ``fragment`` -- a token whose
        basename is the script, not a substring anywhere in the command line.
        A `bash -c "... supervise_ft.py ..."` wrapper mentions the script and
        is not it."""
        out = []
        for p in self._ps.process_iter(["pid", "cmdline"]):
            try:
                argv = list(p.info.get("cmdline") or [])
            except Exception:  # noqa: BLE001
                continue
            if p.info["pid"] == os.getpid():
                continue
            if any(Path(tok.strip('"')).name == fragment for tok in argv[:3]):
                out.append(int(p.info["pid"]))
        return sorted(out)

    def listeners(self, ports) -> Dict[int, Optional[int]]:
        """port -> owning pid for every LISTEN socket on one of ``ports``."""
        found: Dict[int, Optional[int]] = {}
        try:
            for c in self._ps.net_connections(kind="inet"):
                if c.status == "LISTEN" and c.laddr and c.laddr.port in ports:
                    found[int(c.laddr.port)] = c.pid
        except Exception:  # noqa: BLE001 -- permission-limited on some hosts
            pass
        return found


def _matches(cmd: Optional[str], fragments) -> bool:
    return bool(cmd) and any(f in cmd for f in fragments)


def _service_name(cmd: Optional[str]) -> str:
    """Which supervised service a child's command line is, from the ONE list
    `supervisor_status.SERVICES` keeps in step with launcher.py."""
    try:
        from tools.genesis.supervisor_status import SERVICES
    except Exception:  # noqa: BLE001
        return "?"
    for svc in SERVICES:
        if cmd and svc.match in cmd:
            return svc.name
    return "unlisted"


def _external_supervisors(api, fragment: str) -> List[int]:
    """The REAL supervisor processes for ``fragment`` (supervise_ft.py,
    supervise_rt.py): the innermost of any wrapper chain.

    Git Bash cannot exec(), so the shell that launched the supervisor keeps a
    command line naming the script one level UP the tree, and the harness's own
    `bash -c` wrappers above that can too. Measured 2026-09-03: a substring
    match returned FOUR pids for one supervisor. A candidate whose child is also
    a candidate is a wrapper, and stopping it would stop the shell, not the
    service.
    """
    cands = set(api.find_by_cmdline(fragment))
    return sorted(pid for pid in cands
                  if not any(child in cands for child in api.children(pid)))


def _read_lock(pid_file: Path) -> Dict[str, Any]:
    """The supervisor's lock, through supervisor_status (one reader, one rule)."""
    try:
        from tools.genesis.supervisor_status import supervisor

        return supervisor(pid_file)
    except Exception as exc:  # noqa: BLE001
        return {"state": "unknown", "pid": None, "reason": f"supervisor_status failed: {exc}"}


def _stop(api, pid: int, grace: float, dry_run: bool) -> Dict[str, Any]:
    """terminate -> wait grace -> kill -> wait. Returns what happened."""
    rec: Dict[str, Any] = {"pid": pid, "terminated": False, "killed": False, "dead": None}
    if dry_run:
        rec["dead"] = None
        rec["would"] = "terminate"
        return rec
    api.terminate(pid)
    rec["terminated"] = True
    if api.wait_dead(pid, grace):
        rec["dead"] = True
        return rec
    api.kill(pid)
    rec["killed"] = True
    rec["dead"] = bool(api.wait_dead(pid, max(2.0, grace / 2)))
    return rec


def plan_and_run(*, api=None, pid_file: Optional[Path] = None, grace: float = DEFAULT_GRACE,
                 dry_run: bool = False, keep_ft: bool = False, keep_rt: bool = False,
                 include_workers: bool = False, ports=DEFAULT_PORTS,
                 pause: bool = False) -> Dict[str, Any]:
    """The whole procedure. Never raises; the report says what it did and why."""
    report: Dict[str, Any] = {
        "dry_run": dry_run, "grace_seconds": grace, "steps": [], "survivors": [],
        "listeners": {}, "workers_left_running": [], "state": None, "build_mode": None,
    }
    if api is None:
        try:
            api = PsutilProcApi()
        except ImportError:
            report["state"] = STATE_UNMEASURABLE
            report["reason"] = ("psutil is not installed -- the process tree cannot be read, "
                                "and a tree that cannot be read is not stopped blind")
            return report

    pid_file = pid_file or (BASE_DIR / ".tmp" / "genesis" / "launcher.pid")
    recorded: List[int] = []           # every pid this run may touch, and no other

    # ---- 1. the supervisor --------------------------------------------------
    lock = _read_lock(pid_file)
    step: Dict[str, Any] = {"step": "supervisor", "lock": lock}
    sup_pid = lock.get("pid")
    sup_children: List[Dict[str, Any]] = []
    if lock.get("state") == "unknown":
        report["state"] = STATE_UNMEASURABLE
        report["reason"] = lock.get("reason")
        report["steps"].append(step)
        return report
    if lock.get("state") == "down":
        step["outcome"] = "already_down"
        # A stale lock names a dead pid; the launcher only removes it on a clean exit.
        if sup_pid is not None and pid_file.exists() and not dry_run:
            try:
                pid_file.unlink()
                step["stale_lock_removed"] = True
            except OSError as exc:
                step["stale_lock_removed"] = f"failed: {exc}"
    else:
        cmd = api.cmdline(int(sup_pid))
        if not _matches(cmd, SUPERVISOR_FRAGMENTS):
            # A reused pid: the lock names a live process that is not the launcher.
            step["outcome"] = "refused"
            step["reason"] = (f"pid {sup_pid} is alive but its command line does not run "
                              f"launch.py ({(cmd or '?')[:80]!r}) -- a reused pid, not the supervisor")
            report["steps"].append(step)
            report["state"] = STATE_UNMEASURABLE
            report["reason"] = step["reason"]
            return report
        # RECORD the children before anything is stopped: after the supervisor is
        # gone their parent id is meaningless, and a name filter is forbidden.
        for cpid in api.children(int(sup_pid)):
            ccmd = api.cmdline(cpid)
            sup_children.append({"pid": cpid, "service": _service_name(ccmd),
                                 "cmd": (ccmd or "")[:90]})
            # Grandchildren: the scheduler's agent workers. Reported, not stopped.
            for gpid in api.children(cpid):
                report["workers_left_running"].append(
                    {"pid": gpid, "parent": cpid, "cmd": (api.cmdline(gpid) or "")[:90]})
        step["children"] = sup_children
        step["result"] = _stop(api, int(sup_pid), grace, dry_run)
        recorded.append(int(sup_pid))
        step["outcome"] = "would_stop" if dry_run else ("stopped" if step["result"]["dead"] else "SURVIVED")
        if not dry_run and step["result"]["dead"] and pid_file.exists():
            try:
                pid_file.unlink()
                step["lock_removed"] = True
            except OSError as exc:
                step["lock_removed"] = f"failed: {exc}"
    report["steps"].append(step)

    # ---- 2. its children, by recorded pid, only once the supervisor is gone --
    step = {"step": "children", "results": []}
    for child in sup_children:
        cpid = child["pid"]
        if not dry_run and not api.pid_exists(cpid):
            step["results"].append({**child, "outcome": "gone_with_supervisor"})
            continue
        res = _stop(api, cpid, grace, dry_run)
        recorded.append(cpid)
        step["results"].append({**child, "result": res,
                                "outcome": "would_stop" if dry_run else ("stopped" if res["dead"] else "SURVIVED")})
    report["steps"].append(step)

    # ---- 3. workers: reported above; stopped only on request -----------------
    if include_workers and report["workers_left_running"]:
        step = {"step": "workers", "results": []}
        for w in list(report["workers_left_running"]):
            res = _stop(api, w["pid"], grace, dry_run)
            recorded.append(w["pid"])
            step["results"].append({**w, "result": res,
                                    "outcome": "would_stop" if dry_run else ("stopped" if res["dead"] else "SURVIVED")})
        report["workers_left_running"] = []
        report["steps"].append(step)

    # ---- 4. the external supervisors, each then its child --------------------
    keep = {"ft": keep_ft, "rt": keep_rt}
    for key, sup_script, child_script, _port in EXTERNAL_SUPERVISORS:
        step = {"step": key, "skipped": bool(keep.get(key)), "results": [],
                "supervisor_script": sup_script, "child_script": child_script}
        if not keep.get(key):
            for spid in _external_supervisors(api, sup_script):
                kids = [{"pid": k, "cmd": (api.cmdline(k) or "")[:90]} for k in api.children(spid)]
                res = _stop(api, spid, grace, dry_run)
                recorded.append(spid)
                entry = {"supervisor": spid, "result": res, "children": [],
                         "outcome": "would_stop" if dry_run else ("stopped" if res["dead"] else "SURVIVED")}
                for k in kids:
                    if not dry_run and not api.pid_exists(k["pid"]):
                        entry["children"].append({**k, "outcome": "gone_with_supervisor"})
                        continue
                    kres = _stop(api, k["pid"], grace, dry_run)
                    recorded.append(k["pid"])
                    entry["children"].append({**k, "result": kres,
                                              "outcome": "would_stop" if dry_run else ("stopped" if kres["dead"] else "SURVIVED")})
                step["results"].append(entry)
            if not step["results"]:
                step["outcome"] = f"no_{sup_script}_running"
        report["steps"].append(step)

    # ---- 5. verify, never assume -------------------------------------------
    if not dry_run:
        report["survivors"] = [p for p in recorded if api.pid_exists(p)]
        report["listeners"] = {str(port): pid for port, pid in api.listeners(tuple(ports)).items()}
    else:
        report["listeners_now"] = {str(port): pid for port, pid in api.listeners(tuple(ports)).items()}

    # ---- 6. build mode -------------------------------------------------------
    try:
        from tools.kanban import build_mode

        if pause and not dry_run:
            build_mode.set_manual(True, actor="shutdown_dashboard",
                                  reason="paused at shutdown (shutdown_dashboard.py --pause)")
        report["build_mode"] = build_mode.status().get("mode")
    except Exception as exc:  # noqa: BLE001
        report["build_mode"] = f"unreadable: {exc}"

    if dry_run:
        report["state"] = "dry_run"
    elif lock.get("state") == "down" and not any(
            s.get("results") for s in report["steps"] if s.get("step") in EXTERNAL_KEYS):
        report["state"] = STATE_ALREADY_DOWN
    elif report["survivors"] or report["listeners"]:
        report["state"] = STATE_SURVIVORS
    else:
        report["state"] = STATE_STOPPED
    return report


def exit_code(report: Dict[str, Any]) -> int:
    if report.get("state") == STATE_UNMEASURABLE:
        return 2
    if report.get("state") == STATE_SURVIVORS:
        return 1
    return 0


def _human(report: Dict[str, Any]) -> str:
    lines = [f"shutdown_dashboard: {report.get('state')}"
             + (" (DRY RUN -- nothing touched)" if report.get("dry_run") else "")]
    if report.get("reason"):
        lines.append(f"  {report['reason']}")
    for s in report.get("steps", []):
        name = s.get("step")
        if name == "supervisor":
            lock = s.get("lock", {})
            lines.append(f"  supervisor: lock {lock.get('state')} pid={lock.get('pid')} -> {s.get('outcome')}")
            for c in s.get("children", []):
                lines.append(f"    child {c['pid']:>6} {c['service']:18} {c['cmd'][:60]}")
        elif name == "children":
            for r in s.get("results", []):
                lines.append(f"  child {r['pid']:>6} {r['service']:18} -> {r.get('outcome')}")
        elif name == "workers":
            for r in s.get("results", []):
                lines.append(f"  worker {r['pid']:>6} -> {r.get('outcome')}")
        elif name in EXTERNAL_KEYS:
            if s.get("skipped"):
                lines.append(f"  {name}: kept (--keep-{name})")
            elif not s.get("results"):
                lines.append(f"  {name}: no {s.get('supervisor_script')} running")
            for r in s.get("results", []):
                lines.append(f"  {name} supervisor {r['supervisor']} -> {r['outcome']}")
                for k in r.get("children", []):
                    lines.append(f"    {name} child {k['pid']} -> {k.get('outcome')}")
    for w in report.get("workers_left_running", []):
        lines.append(f"  LEFT RUNNING (agent worker, not stopped without --include-workers): "
                     f"pid {w['pid']} {w['cmd'][:60]}")
    if report.get("survivors"):
        lines.append(f"  SURVIVORS: {report['survivors']}")
    lst = report.get("listeners") or report.get("listeners_now") or {}
    for port, pid in lst.items():
        lines.append(f"  {'LISTENER REMAINS' if not report.get('dry_run') else 'listening now'}: "
                     f"port {port} owned by pid {pid}")
    lines.append(f"  build mode: {report.get('build_mode')}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="plan every step; touch nothing")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--grace", type=float, default=DEFAULT_GRACE,
                    help=f"seconds to wait after terminate before kill (default {DEFAULT_GRACE:g})")
    ap.add_argument("--keep-ft", action="store_true", help="leave the ICDEV[FT] supervisor serving")
    ap.add_argument("--keep-rt", action="store_true", help="leave the ICDEV[RT] supervisor serving")
    ap.add_argument("--include-workers", action="store_true",
                    help="also stop the scheduler's agent workers (discards in-flight builds)")
    ap.add_argument("--pause", action="store_true",
                    help="set Manual Build so nothing dispatches when the stack comes back")
    ap.add_argument("--port", type=int, action="append", dest="ports",
                    help=f"port that must have no listener afterwards (default {DEFAULT_PORTS})")
    ap.add_argument("--pid-file", type=Path, default=None,
                    help="the supervisor lock to read (default: <this checkout>/.tmp/genesis/"
                         "launcher.pid). Run from the checkout that STARTED the stack, or name "
                         "its lock here -- a worktree's .tmp/ is empty and reads as 'down'")
    args = ap.parse_args(argv)
    report = plan_and_run(pid_file=args.pid_file, grace=args.grace, dry_run=args.dry_run,
                          keep_ft=args.keep_ft, keep_rt=args.keep_rt,
                          include_workers=args.include_workers,
                          pause=args.pause,
                          ports=tuple(args.ports) if args.ports else DEFAULT_PORTS)
    print(json.dumps(report, indent=2, default=str) if args.json else _human(report))
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
