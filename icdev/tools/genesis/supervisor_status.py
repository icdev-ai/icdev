# CUI // SP-CTI
"""Is the supervisor up, and what is it supervising? (autonomy-id-03)

WHY THIS EXISTS. `tools/genesis/launch.py` -> `launcher.main()` is a SUPERVISOR:
it holds a pid lock, starts six services, and restarts any that die on a 30s
loop. `/start` does not know that. Its steps 8 and 9 `Start-Process` the kanban
scheduler and the genesis daemon DIRECTLY, so on a machine where the supervisor
is already up they race it.

WHAT THAT LOOKS LIKE, observed 2026-08-20. The manually started copies lose —
`_kill_stale_instances` reaps them, or they exit on a lock — and they exit
SILENTLY: no traceback, no stderr, nothing in the log. Worse, the
`-RedirectStandardOutput .tmp/kanban_scheduler.log` that started them TRUNCATES
the log file on open, so the healthy supervised pair reads as a total failure:
empty logs plus a dead PID of your own making. Measured that day, manual PIDs
30876/15684 were dead inside 20s while the supervisor's own 21772/25820 were
alive and already dispatching work.

THE LOG PATHS DISAGREE, which is the other half of the confusion. The supervisor
writes the genesis daemon to `.tmp/genesis/daemon.log`; `/start` writes it to
`.tmp/genesis_daemon.log`. Tailing the second while the first is being written
shows nothing, and "no log output" reads as "the daemon is dead".

    A SUPERVISED SERVICE'S LIVENESS IS NEVER PROVEN BY ITS LOG FILE.

Ask this module instead. It reads the supervisor's pid lock and the process
table, and — where autonomy-id-01 recorded one — the code identity each child
booted with.

DEFER, NEVER DUPLICATE. `ensure()` starts THE SUPERVISOR when none is running,
never an individual child: a child started beside a live supervisor is the exact
duplicate this module exists to stop, and a child started WITHOUT one has nothing
watching it when it dies.

IT KILLS NOTHING. Not a child, not a stale instance, and above all not by name.
`/start`'s own `taskkill /f /im python.exe` has already taken out unrelated
tooling on this machine once; and since the supervisor is itself `python.exe`,
that command kills the one process that would have restarted everything else.
Stopping a child is `launcher`'s business, which does it by verified pid.

Usage:
    python tools/genesis/supervisor_status.py            # human table
    python tools/genesis/supervisor_status.py --json
    python tools/genesis/supervisor_status.py --ensure   # start the supervisor if absent
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 — fixed argv, shell=False, starts the supervisor only
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: The supervisor's own lock, written by `launcher._acquire_pid_lock`.
PID_FILE = ROOT / ".tmp" / "genesis" / "launcher.pid"
#: What `launch.py` runs. `ensure()` starts THIS, never a child.
SUPERVISOR_SCRIPT = "tools/genesis/launch.py"


@dataclass(frozen=True)
class Service:
    """One supervised child.

    ``match`` is the command-line fragment `launcher._kill_stale_instances` uses
    for the same service, so "what the supervisor manages" and "what this reports"
    cannot disagree about which process is which. A test asserts every
    ``_start_*`` in launcher.py has an entry here — the two lists are one rule in
    two renderings, and a service added there without an entry here would simply
    go unreported.
    """

    name: str
    match: str
    log: str


#: In launcher.main() start order.
SERVICES = (
    Service("dashboard", "tools/dashboard/app.py", ".tmp/dashboard.log"),
    Service("genesis_daemon", "tools/genesis/daemon.py", ".tmp/genesis/daemon.log"),
    Service("proposal_genesis", "proposal_genesis/daemon.py",
            ".tmp/proposal_genesis/daemon.log"),
    Service("kanban_scheduler", "kanban_scheduler.py", ".tmp/kanban_scheduler.log"),
    Service("pr_watcher", "pr_watcher", ".tmp/pr_watcher.log"),
    Service("trading_dashboard", "trading/dashboard/app.py",
            ".tmp/trading_dashboard.log"),
)


def supervisor(pid_file: Optional[Path] = None) -> Dict[str, Any]:
    """Is a supervisor holding the lock?

    Three states, and `unknown` is never folded into `down`: a lock we cannot
    read is not proof that nothing is running, and starting a second supervisor
    on that assumption is how duplicates begin.
    """
    path = pid_file or PID_FILE
    if not path.exists():
        return {"state": "down", "pid": None,
                "reason": "no launcher.pid — no supervisor has taken the lock"}
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        return {"state": "unknown", "pid": None,
                "reason": f"launcher.pid unreadable: {exc}"}
    try:
        from tools.compat.platform_utils import pid_exists
        alive = pid_exists(pid)
    except Exception as exc:  # noqa: BLE001
        return {"state": "unknown", "pid": pid,
                "reason": f"could not test pid {pid}: {exc}"}
    if alive:
        return {"state": "up", "pid": pid, "reason": None}
    return {"state": "down", "pid": pid,
            "reason": f"launcher.pid names pid {pid}, which is not running (stale lock)"}


def _identity_rows() -> Dict[str, Dict[str, Any]]:
    """Recorded code identity per module, keyed by module name (autonomy-id-01).

    Absent on a deployment that has not run the migration, and absent for a
    service that does not register. Both are reported as `null`, never as a
    version — see :func:`children`.
    """
    try:
        from tools.coordination.code_identity import processes
        report = processes()
    except Exception:  # noqa: BLE001
        return {}
    if report.get("state") not in ("measured",):
        return {}
    out = {}
    for row in report.get("processes") or []:
        module = row.get("module")
        if module:
            out[module] = row
    return out


def children(services=SERVICES) -> List[Dict[str, Any]]:
    """Which supervised services are running, and what code they reported.

    `pids` is UNMEASURED (None) rather than empty when the process table cannot
    be read — "nothing is running" and "we could not look" justify opposite
    actions, and only the first is a reason to start anything.
    """
    try:
        from tools.compat.platform_utils import find_pids_by_cmdline
        # THE SAME EXCLUSION THE KILLER USES, not a second copy.
        # `find_pids_by_cmdline` substring-matches the whole joined command line
        # across processes of ANY name, so the fragment "pr_watcher" also matches
        # a shell that merely TYPED it — `bash -c '... pr_watcher ...'`.
        # `launcher._kill_stale_instances` already guards against that (it
        # measured the same thing: five matches, four of them diagnostic shells).
        # This reporter did not, and on its first live run it told a human there
        # were THREE pr_watchers racing on auto-merge when there was one, plus
        # two greps of its own. Over-reporting a hazard is the same defect as
        # under-reporting one: a claim whose evidence nothing re-derived.
        from tools.genesis.launcher import _is_inline_snippet
    except Exception:  # noqa: BLE001
        find_pids_by_cmdline = None
        _is_inline_snippet = None

    identities = _identity_rows()
    out = []
    for svc in services:
        pids: Optional[List[int]]
        if find_pids_by_cmdline is None:
            pids = None
        else:
            try:
                found = find_pids_by_cmdline(svc.match)
                if _is_inline_snippet is not None:
                    found = [p for p in found if not _is_inline_snippet(p, svc.match)]
                pids = sorted(found)
            except Exception:  # noqa: BLE001
                pids = None
        ident = next((row for module, row in identities.items()
                      if svc.match.split("/")[-1].removesuffix(".py")
                      in (module or "").replace(".", "/").split("/")), None)
        out.append({
            "name": svc.name,
            "match": svc.match,
            "log": svc.log,
            "pids": pids,
            "running": None if pids is None else bool(pids),
            # None means NOT RECORDED — never "current".
            "code_version": (ident or {}).get("code_version"),
            "code_version_source": (ident or {}).get("code_version_source"),
        })
    return out


def pid_file_for(root: Optional[Path] = None) -> Path:
    """The launcher lock for a given checkout.

    ROOT resolves from ``__file__``, never ``os.getcwd()`` — but that means a
    copy of this module inside a WORKTREE reports on the worktree's supervisor,
    which is correct and easy to misread. A worktree legitimately has none;
    that is not evidence about the main checkout. Pass ``--root`` to ask about a
    different tree, explicitly rather than by accident.
    """
    return (Path(root) if root else ROOT) / ".tmp" / "genesis" / "launcher.pid"


def status(root: Optional[Path] = None) -> Dict[str, Any]:
    """The whole picture: the supervisor, and what it should be supervising."""
    sup = supervisor(pid_file_for(root))
    kids = children()
    running = [c for c in kids if c["running"]]
    unmeasured = [c for c in kids if c["running"] is None]
    return {
        "supervisor": sup,
        "children": kids,
        "running_count": len(running),
        "unmeasured_count": len(unmeasured),
        # The one question /start needs answered before it starts anything.
        "should_start_supervisor": sup["state"] == "down",
        "defer_to_supervisor": sup["state"] in ("up", "unknown"),
    }


def ensure(dry_run: bool = False, runner=None,
           root: Optional[Path] = None) -> Dict[str, Any]:
    """Start the supervisor if, and only if, none is running.

    Never starts a child. A child started beside a live supervisor is a
    duplicate that dies silently; a child started without one has nothing
    watching it. `unknown` DEFERS — it is not proof that nothing is running.
    """
    sup = supervisor(pid_file_for(root))
    if sup["state"] == "up":
        return {"action": "deferred", "supervisor": sup,
                "reason": f"a supervisor is already running (pid {sup['pid']})"}
    if sup["state"] == "unknown":
        return {"action": "deferred", "supervisor": sup,
                "reason": ("the supervisor's state could not be determined; "
                           "starting one anyway risks a duplicate")}
    if dry_run:
        return {"action": "would_start", "supervisor": sup,
                "command": [sys.executable, SUPERVISOR_SCRIPT]}

    run = runner or _spawn
    try:
        pid = run([sys.executable, SUPERVISOR_SCRIPT])
    except Exception as exc:  # noqa: BLE001
        return {"action": "failed", "supervisor": sup, "reason": str(exc)[:200]}
    return {"action": "started", "pid": pid, "supervisor": sup}


def _spawn(argv: List[str]) -> int:
    kwargs: Dict[str, Any] = {"cwd": str(ROOT)}
    if os.name == "nt":
        # Detach so the supervisor outlives the shell that started it — the
        # whole point is a process that keeps running after /start returns.
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **kwargs)  # nosec B603 — fixed argv, shell=False
    return proc.pid


def render(rep: Dict[str, Any]) -> str:
    sup = rep["supervisor"]
    mark = {"up": "UP", "down": "DOWN", "unknown": "??"}[sup["state"]]
    out = [f"Supervisor: {mark}" + (f" (pid {sup['pid']})" if sup["pid"] else "")]
    if sup.get("reason"):
        out.append(f"  {sup['reason']}")
    out.append("")
    for c in rep["children"]:
        if c["running"] is None:
            state = "  ??  "
        elif c["running"]:
            state = "  up  "
        else:
            state = " DOWN "
        pids = "?" if c["pids"] is None else (",".join(str(p) for p in c["pids"]) or "-")
        ver = (c["code_version"] or "")[:9] or "not recorded"
        out.append(f"{state} {c['name']:20} pid={pids:<14} {ver}")
    out.append("")
    if rep["should_start_supervisor"]:
        out.append("  No supervisor is running. Start THE SUPERVISOR, not the children:")
        out.append(f"      python {SUPERVISOR_SCRIPT}")
        out.append("      (or: python tools/genesis/supervisor_status.py --ensure)")
    else:
        out.append("  A supervisor is running — do NOT Start-Process its children.")
        out.append("  A child started beside it is reaped silently, and the redirect")
        out.append("  that starts it TRUNCATES the log you would then read.")
    if rep["unmeasured_count"]:
        out.append(f"  {rep['unmeasured_count']} service(s) unmeasured "
                   f"(the process table could not be read — not the same as down)")
    return "\n".join(out)


def main(argv=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--ensure", action="store_true",
                        help="start the supervisor if none is running")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --ensure, print what would start")
    parser.add_argument("--root", help="checkout to ask about (default: this module's own tree)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.ensure:
        result = ensure(dry_run=args.dry_run, root=args.root)
        print(json.dumps(result, indent=2, default=str) if args.json
              else f"{result['action']}: {result.get('reason') or result.get('pid', '')}")
        return 0 if result["action"] != "failed" else 1

    rep = status(args.root)
    print(json.dumps(rep, indent=2, default=str) if args.json else render(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
