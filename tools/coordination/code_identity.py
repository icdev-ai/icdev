# CUI // SP-CTI
"""Which code is THIS process running — captured once at boot, never re-read.

THE DEFECT THIS EXISTS FOR. Nothing recorded the code version a running process
held. On 2026-08-20 three fixes (#1859, #1861, #1863) merged and the running
scheduler and daemon went on executing pre-merge code. The board was correct and
CI was green; the one thing nobody could state was whether the code doing the
work was the code that had been merged. A human noticed by eye and no surface
could say it.

WHY IT IS READ ONCE AND FROZEN, WHICH IS THE WHOLE DESIGN.
``tools/genesis/code_reload.py`` fast-forwards the working copy *underneath a
running daemon* — ``pull_if_safe`` runs inside the same poll loop that
heartbeats. So ``git HEAD`` moves while the process keeps executing the modules
it imported at startup. A process that re-read HEAD on each heartbeat would
therefore report THE TREE IT COULD HAVE BEEN RUNNING rather than the one it is,
and it would report "current" at exactly the moment it went stale. That is worse
than recording nothing: it manufactures a green answer for the precise failure
the record exists to expose.

The capture therefore happens at first call and is never recomputed. A re-exec
(``code_reload.respawn``) is a NEW process which re-imports this module and
takes a fresh reading — which is correct, because a re-exec really is a new
boot.

UNKNOWN IS A REAL ANSWER AND IS NEVER SMOOTHED INTO CURRENT. ``code_version`` is
None when it cannot be determined — no git, an unreadable repository, a tarball
install on an air-gapped host. ``code_version_source`` says WHY, because "git
said so" and "there is no git here" send you to different fixes, and a bare None
cannot tell them apart.

FOUR FIELDS, KEPT APART ON PURPOSE:

    module               the entry point this process is running
    code_version         the commit the tree was at, or None
    code_version_source  git | env | unavailable  — how the above was obtained
    code_dirty           1/0, or None when unknowable

``code_dirty`` earns its own field because a commit alone OVERSTATES what is
known. This checkout carries local modifications routinely, and a process
started from a modified tree is NOT running the tree that SHA names. Folding
that into the version string would make the record assert something false in
detail; folding it into ``code_version_source`` would merge "how we learned it"
with "did the tree match", which are independent axes.

THIS CARD RECORDS. It deliberately computes NO staleness verdict — comparing a
recorded SHA against the tip is autonomy-id-02's business, and it must be done
against the process's own import closure rather than the tip generally, or every
process reads stale several times an hour and the signal is ignored within a day.
"""
from __future__ import annotations

import os
import subprocess  # nosec B404 — git only, fixed argv, shell=False
import sys
from pathlib import Path
from typing import Any, Dict, Optional

#: Resolved from ``__file__``, never ``os.getcwd()`` — daemons are started from
#: worktrees and from service managers with an arbitrary working directory.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

#: A hung git must never block a daemon's boot. Short, and every failure mode
#: degrades to "unavailable" rather than raising.
GIT_TIMEOUT_SECONDS = 10

#: Populated on first call, then never recomputed. See the module docstring —
#: re-reading is the defect, not an optimisation opportunity.
_BOOT: Optional[Dict[str, Any]] = None


def _run_git(args, root: Path):
    return subprocess.run(  # nosec B603 B607 — fixed argv, shell=False, git only
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=GIT_TIMEOUT_SECONDS, check=False, shell=False,
    )


def _unavailable() -> Dict[str, Any]:
    return {"code_version": None, "code_version_source": "unavailable",
            "code_dirty": None}


def _read_git(root: Path, runner, *, check_dirty: bool) -> Dict[str, Any]:
    """The commit on disk and, when asked, whether the tree matches it.

    Returns ``code_version=None`` on every failure path. ``code_dirty`` stays
    None unless the porcelain read actually SUCCEEDED: a failed status read
    means we do not know whether the tree is modified, and reporting 0 there
    would be a clean bill of health nobody measured. None for "not measured"
    and None for "unreadable" are the same answer — unknown — so they share a
    value deliberately.
    """
    try:
        head = runner(["rev-parse", "HEAD"], root)
    except (OSError, subprocess.SubprocessError):
        return _unavailable()
    if getattr(head, "returncode", 1) != 0:
        return _unavailable()
    sha = (getattr(head, "stdout", "") or "").strip()
    if not sha:
        return _unavailable()

    dirty: Optional[int] = None
    if check_dirty:
        try:
            status = runner(["status", "--porcelain"], root)
            if getattr(status, "returncode", 1) == 0:
                dirty = 1 if (getattr(status, "stdout", "") or "").strip() else 0
        except (OSError, subprocess.SubprocessError):
            dirty = None
    return {"code_version": sha, "code_version_source": "git", "code_dirty": dirty}


def _module_from_path(path: Optional[str], root: Path) -> Optional[str]:
    """A repo-relative dotted module name for a file path, else None.

    Built from ``Path.parts`` rather than string surgery on separators: a
    backslash is not a separator on Linux, so a hand-rolled ``replace`` produces
    a name that matches on Windows and is silently wrong on CI.

    The path must be an EXISTING ``.py`` file. Without that check ``python -c``
    reports ``sys.argv[0] == "-c"``, which resolves against the cwd, lands
    inside the repo and yields the module name ``-c`` — a garbage identity that
    looks like a real answer. A process whose entry point is not a file on disk
    has no module name, and None is the honest result.
    """
    if not path:
        return None
    try:
        resolved = Path(path).resolve()
        if resolved.suffix != ".py" or not resolved.is_file():
            return None
        rel = resolved.relative_to(root)
    except (ValueError, OSError):
        return None
    parts = list(rel.with_suffix("").parts)
    if not parts:
        return None
    if parts[-1] == "__main__" and len(parts) > 1:
        parts = parts[:-1]
    return ".".join(parts)


def _detect_module(root: Path) -> Optional[str]:
    """The entry point this process was started as.

    ``__main__.__file__`` rather than ``sys.argv[0]``: argv[0] can be a relative
    path resolved against a cwd that has since changed, and these daemons change
    directory. Returns None rather than guessing when the process has no file
    entry point (an interactive interpreter, an embedded host).
    """
    main = sys.modules.get("__main__")
    name = _module_from_path(getattr(main, "__file__", None), root)
    if name:
        return name
    argv0 = sys.argv[0] if sys.argv else None
    return _module_from_path(argv0, root)


def boot_identity(*, module: Optional[str] = None, root: Optional[Path] = None,
                  runner=None, check_dirty: bool = False) -> Dict[str, Any]:
    """The code identity of this process. Computed once; identical thereafter.

    ``module`` names the entry point when the caller knows it better than
    ``__main__`` does — a daemon subclass knows its own module, while
    ``__main__`` for ``python tools/genesis/daemon.py`` is just the script path.
    It is honoured only on the FIRST call, like every other field: a later
    caller cannot rewrite what this process booted as.

    ``check_dirty`` is OFF by default because the two git reads cost two orders
    of magnitude apart on this repository — measured 2026-08-21, ``rev-parse
    HEAD`` 29ms against ``status --porcelain`` 835ms warm and ~1.8s cold. A
    long-lived daemon pays that once per boot and should ask for it; a hook that
    runs afresh on every agent turn must not, or the record's cost lands on
    interactive latency. Callers that decline get ``code_dirty=None``, which
    reads as unknown — never as a clean tree.

    Whoever calls FIRST decides, since the answer is then frozen. Daemons call
    it during startup before anything else can, and a later ``None`` is honest
    rather than wrong, so the ordering carries no risk of a false clean.

    Returns a fresh dict each time so a caller mutating the result cannot
    corrupt the frozen record.
    """
    global _BOOT
    if _BOOT is None:
        base = root or BASE_DIR
        run = runner or _run_git
        try:
            ident: Dict[str, Any] = dict(
                _read_git(base, run, check_dirty=check_dirty))
        except Exception:  # noqa: BLE001 — boot identity must never stop a boot
            ident = _unavailable()

        if ident.get("code_version") is None:
            # No git here. An air-gapped tarball install has no repository, and
            # a declared build id is then the only truth available. Deliberately
            # AFTER git, unlike sbom_revision.source_revision which prefers the
            # declaration: an SBOM describes a BUILD, where a pipeline id beats
            # a commit, while this describes THE TREE ON DISK, where a direct
            # observation beats a declaration that may be a stale leftover.
            env_build = (os.environ.get("ICDEV_BUILD_ID") or "").strip()
            if env_build:
                ident = {"code_version": env_build, "code_version_source": "env",
                         "code_dirty": None}

        try:
            ident["module"] = module or _detect_module(base)
        except Exception:  # noqa: BLE001
            ident["module"] = module
        _BOOT = ident
    return dict(_BOOT)


def reset_for_test() -> None:
    """Drop the frozen record so a test can boot a fresh identity.

    Test-only. Nothing on the runtime path may call this: re-reading is the
    defect this module exists to prevent.
    """
    global _BOOT
    _BOOT = None


# ────────────────────────────────────────────────────────────────────────────
# Reading the fleet back
# ────────────────────────────────────────────────────────────────────────────
#: A row whose identity was never recorded. NOT the same as a process running an
#: unknown-but-recorded version, and never smoothed into "current" — a process
#: registered before autonomy-id-01 genuinely has no reading, and defaulting it
#: to HEAD would assert that everything already running is up to date, inventing
#: exactly the reassurance this module exists to refuse.
UNKNOWN = "unknown"
#: An identity was recorded. Whether it is STALE is autonomy-id-02's question,
#: deliberately not answered here.
RECORDED = "recorded"


def processes(ttl_seconds: Optional[int] = None) -> Dict[str, Any]:
    """Every live process and the code it reported at boot.

    Computes no staleness verdict — see the module docstring. This answers
    "what is running and what does it say it is", which is the fact a verdict
    would have to be derived FROM.

    Returns ``state: unmeasurable`` when the registry cannot be read or the
    identity columns are absent, never an empty fleet: "nothing is running" and
    "nobody could look" justify opposite actions.
    """
    try:
        from tools.coordination import session_registry as reg
    except Exception as exc:  # noqa: BLE001
        return {"state": "unmeasurable", "reason": f"registry unimportable: {exc}",
                "processes": [], "recorded": None, "unknown": None}

    try:
        rows = (reg.list_active(ttl_seconds) if ttl_seconds is not None
                else reg.list_active())
    except Exception as exc:  # noqa: BLE001
        return {"state": "unmeasurable", "reason": f"registry unreadable: {exc}",
                "processes": [], "recorded": None, "unknown": None}

    out, recorded, unknown = [], 0, 0
    for row in rows or []:
        d = dict(row)
        version = d.get("code_version")
        state = RECORDED if version else UNKNOWN
        if version:
            recorded += 1
        else:
            unknown += 1
        out.append({
            "session_id": d.get("session_id"),
            "agent_type": d.get("agent_type"),
            "module": d.get("module"),
            "pid": d.get("pid"),
            "host": d.get("host"),
            "code_version": version,
            "code_version_source": d.get("code_version_source"),
            "code_dirty": d.get("code_dirty"),
            "state": state,
        })

    if not out:
        # No live process at all. Honest, and distinct from unmeasurable.
        return {"state": "no_live_processes", "processes": [],
                "recorded": 0, "unknown": 0}
    return {"state": "measured", "processes": out,
            "recorded": recorded, "unknown": unknown}


def _main(argv=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--boot", action="store_true",
                        help="print THIS process's identity and exit")
    args = parser.parse_args(argv)

    if args.boot:
        print(json.dumps(boot_identity(check_dirty=True), indent=2, default=str))
        return 0

    report = processes()
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    print(f"Fleet code identity — {report['state']}")
    if report.get("reason"):
        print(f"  {report['reason']}")
    for p in report["processes"]:
        ver = (p["code_version"] or "")[:9] or "?"
        dirty = {1: " +dirty", 0: "", None: " +dirty?"}.get(p["code_dirty"], "")
        print(f"  {str(p['module'] or p['agent_type'])[:38]:38} pid={p['pid'] or '?':>7} "
              f"{ver}{dirty}  [{p['state']}]")
    if report["state"] == "measured":
        print(f"\n  recorded {report['recorded']} · unknown {report['unknown']} "
              f"(unknown is NOT a clean bill of health)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
