#!/usr/bin/env python3
# CUI // SP-CTI
"""omx-guard-05 (L3): an autonomous worker can only WRITE inside its task worktree.

L1 (a native pre-tool hook) and L2 (the guarded shell) both judge a tool call
by reading it. Neither sees a harness's BUILT-IN file-edit tool when the
harness has no hook (codex, copilot, goose): those writes go straight to disk.
L3 does not read anything. It starts the harness inside an OS sandbox whose
writable set is

* the write-boundary roots L1 already enforces --
  ``shared_checks.sanctioned_write_roots(<worktree>)`` (worktree, the main
  checkout git writes its objects and refs into, temp dirs, the ~/.claude
  session dirs, ``$ICDEV_WORKTREE_ROOT``, ``$ICDEV_WRITE_BOUNDARY_EXTRA_ROOTS``)
  -- reused, never restated, so L1 and L3 cannot disagree;
* the spawned harness's own declared state (``args/write_sandbox.yaml``);

and everything else read-only. Reads, execution and the network stay open:
the LLM providers are remote.

BACKENDS, measured rather than assumed (``status()`` runs each one):

* Linux -- bubblewrap (``bwrap``) when present AND able to create its
  namespace (an AppArmor userns restriction makes an installed bwrap fail);
  else Landlock (kernel >= 5.13) through ``landlock_exec.py``, which confines
  itself and execs the harness; else ``unavailable``, reported.
* Windows -- explicitly UNSUPPORTED; see ``UNSUPPORTED_WINDOWS`` for what was
  evaluated and why none of it is implemented. Reported on every dispatch.
* anything else (macOS) -- explicitly unsupported, reported.

Wiring: the kanban runner sets ``metadata[META_KEY]``; each CLI adapter's
``build_argv`` ends in ``wrap_argv``, which prepends the sandbox for THAT
adapter. A session without the key (interactive use, probes) is untouched.

Kill switches are the existing ones for this boundary:
``ICDEV_WRITE_BOUNDARY_GUARD=0`` (or ``=monitor`` -- an OS sandbox cannot
merely record, so monitor means not sandboxed, and says so) and the hook-wide
``ICDEV_PRETOOLUSE_ENFORCE=0``.

Failure: a sandbox that cannot be BUILT is reported and the dispatch runs
without it (logged, like a broken L2 bridge); a Landlock confinement that
fails at exec time refuses to run the harness at all (``landlock_exec``).

Usage::

    python -m tools.hooks.write_sandbox --status [--json]
    python -m tools.hooks.write_sandbox --self-test [--adapter codex_cli] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from icdev.core.paths import repo_root
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = repo_root(__file__)
STATE_PATH = BASE_DIR / "args" / "write_sandbox.yaml"
LANDLOCK_HELPER = Path(__file__).resolve().with_name("landlock_exec.py")

#: Session metadata key the runner sets to ask for the sandbox.
META_KEY = "write_sandbox"
#: Force one Linux backend (``bwrap`` / ``landlock``) -- tests and operators.
BACKEND_ENV = "ICDEV_WRITE_SANDBOX_BACKEND"

BWRAP = "bwrap"
LANDLOCK = "landlock"
BACKENDS = (BWRAP, LANDLOCK)

ENFORCE = "enforce"
MONITOR = "monitor"
OFF = "off"

UNSUPPORTED_WINDOWS = (
    "the L3 write sandbox is unsupported on Windows. Evaluated: AppContainer "
    "denies READS outside its ACL'd paths too, so a harness cannot load its own "
    "install or the repo without re-ACLing the user's tree; a low-integrity "
    "token needs the worktree, main checkout and every harness state dir "
    "relabelled low -- a persistent change that lets every low-IL process on "
    "the host write them; Windows Sandbox is a VM that cannot see the host "
    "worktree and is absent on Home editions. Writes by a harness without a "
    "native hook (L1) are NOT confined on this host"
)
UNSUPPORTED_OTHER = (
    "the L3 write sandbox is implemented for Linux only (bubblewrap / Landlock); "
    "unsupported on {platform} -- writes are NOT confined on this host"
)
UNAVAILABLE_LINUX = (
    "no Linux write sandbox available: bubblewrap {bwrap} and Landlock {landlock} "
    "-- writes are NOT confined on this host"
)


# ── mode and backend ─────────────────────────────────────────────────────────

def mode() -> str:
    """``enforce`` unless one of the existing kill switches stands L3 down."""
    from tools.hooks import harness_guard, shared_checks  # noqa: PLC0415

    off = ("0", "false", "no", "off")
    if os.environ.get(harness_guard.ENFORCEMENT_ENV, "").strip().lower() in off:
        return OFF
    value = os.environ.get(shared_checks.WRITE_BOUNDARY_GUARD_ENV, "").strip().lower()
    if value in off:
        return OFF
    if value == MONITOR:
        return MONITOR
    return ENFORCE


def _bwrap_verdict() -> Tuple[bool, str]:
    exe = shutil.which("bwrap")
    if not exe:
        return False, "not installed"
    try:
        proc = subprocess.run([exe, "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev",
                               "--", "true"], capture_output=True, text=True,
                              timeout=20, stdin=subprocess.DEVNULL, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"did not run ({type(exc).__name__}: {exc})"
    if proc.returncode != 0:
        return False, f"cannot create its namespace ({proc.stderr.strip()[:160]})"
    return True, exe


def _landlock_verdict() -> Tuple[bool, str]:
    from tools.hooks import landlock_exec  # noqa: PLC0415

    version = landlock_exec.abi()
    return (True, f"ABI {version}") if version >= 1 else (False, "not enabled in this kernel")


@lru_cache(maxsize=1)
def _detect(platform: str, forced: str) -> Tuple[Optional[str], str]:
    if platform == "win32":
        return None, UNSUPPORTED_WINDOWS
    if not platform.startswith("linux"):
        return None, UNSUPPORTED_OTHER.format(platform=platform)
    bw_ok, bw = (False, "not selected") if forced == LANDLOCK else _bwrap_verdict()
    if bw_ok:
        return BWRAP, f"bubblewrap at {bw}"
    ll_ok, ll = (False, "not selected") if forced == BWRAP else _landlock_verdict()
    if ll_ok:
        return LANDLOCK, f"Landlock {ll} (bubblewrap: {bw})"
    return None, UNAVAILABLE_LINUX.format(bwrap=bw, landlock=ll)


def backend() -> Tuple[Optional[str], str]:
    """``(backend or None, why)`` for this host. Measured once per process."""
    forced = os.environ.get(BACKEND_ENV, "").strip().lower()
    return _detect(sys.platform, forced if forced in BACKENDS else "")


def reset_cache() -> None:
    """Forget the measured backend. Tests only."""
    _detect.cache_clear()


def status() -> Dict[str, Any]:
    """Whether the runner can sandbox a worker here, and why (never silent)."""
    current = mode()
    name, why = backend()
    if current != ENFORCE:
        return {"supported": False, "backend": name, "mode": current,
                "reason": f"L3 stood down by its kill switch (mode={current}); "
                          f"writes are NOT confined"}
    return {"supported": name is not None, "backend": name, "mode": current,
            "reason": why}


# ── the writable set ─────────────────────────────────────────────────────────

def declared_state(adapter: Optional[str],
                   path: Optional[Path] = None) -> Dict[str, List[Path]]:
    """``args/write_sandbox.yaml`` entries for ``adapter``, ``~`` expanded."""
    import yaml  # noqa: PLC0415

    try:
        data = yaml.safe_load((path or STATE_PATH).read_text(encoding="utf-8")) or {}
    except OSError:
        data = {}
    entry = ((data.get("adapters") or {}).get(adapter or "") or {})
    return {key: [Path(str(p)).expanduser() for p in entry.get(key) or []]
            for key in ("state_dirs", "state_files")}


def writable_roots(worktree: str, adapter: Optional[str] = None,
                   state_path: Optional[Path] = None) -> List[str]:
    """Every path the sandbox leaves writable, deduplicated, existing dirs created.

    The shared write-boundary roots first, then the adapter's declared state.
    A declared state DIR is created (the harness could not create it from
    inside); a declared state file that does not exist is left out.
    """
    from tools.hooks import shared_checks  # noqa: PLC0415

    roots: List[str] = [str(r) for r in shared_checks.sanctioned_write_roots(Path(worktree))]
    state = declared_state(adapter, state_path)
    for directory in state["state_dirs"]:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("write_sandbox: cannot create state dir %s (%s)", directory, exc)
            continue
        roots.append(str(directory.resolve()))
    roots += [str(f.resolve()) for f in state["state_files"] if f.exists()]
    seen: List[str] = []
    for root in roots:
        if root not in seen:
            seen.append(root)
    return seen


def prefix(worktree: str, adapter: Optional[str] = None,
           which: Optional[str] = None,
           state_path: Optional[Path] = None) -> List[str]:
    """The argv that runs a command inside the sandbox; the command follows it."""
    which = which or backend()[0]
    roots = writable_roots(worktree, adapter, state_path)
    if which == BWRAP:
        argv = [shutil.which("bwrap") or "bwrap",
                "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev",
                "--die-with-parent"]
        for root in roots:
            argv += ["--bind-try", root, root]
        return argv + ["--"]
    if which == LANDLOCK:
        argv = [sys.executable, "-I", str(LANDLOCK_HELPER)]
        for root in roots + ["/dev"]:  # /dev/null, ttys, /dev/shm
            argv += ["--rw", root]
        return argv + ["--"]
    raise RuntimeError(f"no write sandbox backend on this host: {backend()[1]}")


def wrap_argv(argv: List[str], session: Any, adapter: str) -> List[str]:
    """``argv`` inside the sandbox when the runner asked for one, else unchanged.

    Every CLI adapter's ``build_argv`` ends here. A sandbox that cannot be built
    is logged and the command runs without it -- never silently.
    """
    meta = getattr(session, "metadata", None) or {}
    if not meta.get(META_KEY):
        return argv
    worktree = getattr(session, "working_dir", None)
    try:
        if not worktree:
            raise RuntimeError("the session has no working_dir to confine writes to")
        return prefix(str(worktree), adapter) + list(argv)
    except Exception as exc:  # noqa: BLE001 -- reported, never silent
        logger.warning("write_sandbox: L3 NOT applied to %s for %s (%s: %s); "
                       "running UNCONFINED", adapter, getattr(session, "task_id", "?"),
                       type(exc).__name__, exc)
        return argv


# ── the measurement ──────────────────────────────────────────────────────────

def _write(cmd_prefix: Sequence[str], target: Path, cwd: Path) -> Tuple[bool, str]:
    argv = [*cmd_prefix, "sh", "-c", 'printf icdev-l3 > "$1"', "sh", str(target)]
    try:
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                              timeout=60, stdin=subprocess.DEVNULL, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    written = target.is_file()
    return written, (proc.stderr or "").strip()[:200]


def self_test(cmd_prefix: Sequence[str], worktree: Path, outside: Path,
              state_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Run real writes through ``cmd_prefix``: inside and state must land, outside must not."""
    checks: Dict[str, Any] = {}
    ok_in, err_in = _write(cmd_prefix, worktree / ".icdev-l3-inside", worktree)
    checks["worktree_write"] = {"written": ok_in, "stderr": err_in}
    if state_dir is not None:
        target = state_dir / f".icdev-l3-probe-{uuid.uuid4().hex[:8]}"
        ok_state, err_state = _write(cmd_prefix, target, worktree)
        checks["state_write"] = {"path": str(state_dir), "written": ok_state, "stderr": err_state}
        target.unlink(missing_ok=True)
    ok_out, err_out = _write(cmd_prefix, outside, worktree)
    checks["outside_write"] = {"path": str(outside), "written": ok_out, "stderr": err_out}
    outside.unlink(missing_ok=True)
    if ok_out:
        return {"sandboxed": False, "checks": checks,
                "reason": f"a write OUTSIDE the worktree landed: {outside}"}
    if not ok_in:
        return {"sandboxed": False, "checks": checks,
                "reason": f"a write INSIDE the worktree was refused ({err_in})"}
    if state_dir is not None and not checks["state_write"]["written"]:
        return {"sandboxed": False, "checks": checks,
                "reason": f"the declared state dir {state_dir} is not writable "
                          f"({checks['state_write']['stderr']})"}
    return {"sandboxed": True, "checks": checks,
            "reason": f"outside write refused ({err_out or 'no file'}); worktree"
                      + (" and state dir" if state_dir is not None else "") + " writable"}


def probe(adapter: Optional[str] = None,
          build_argv: Optional[Callable[[str, Dict[str, Any]], List[str]]] = None,
          home: Optional[Path] = None) -> Dict[str, Any]:
    """Measure the sandbox for ``adapter``: ``{"sandboxed": True|False|None, ...}``.

    ``None`` = could not be measured here (unsupported OS, no backend, kill
    switch), with the reason. With ``build_argv(worktree, metadata)`` the
    sandbox is taken from the ADAPTER'S OWN command line, so a CLI adapter that
    forgot ``wrap_argv`` measures absent rather than present.

    The probe worktree and the outside target are SIBLINGS under ``home``,
    outside every shared root -- ``<wt>-outside`` also catches a containment
    check done by string prefix.
    """
    st = status()
    if not st["supported"]:
        return {"sandboxed": None, "reason": st["reason"], "status": st}
    base = (home or Path.home()) / ".cache" / "icdev" / "l3-probe"
    token = uuid.uuid4().hex[:10]
    worktree = base / token
    outside = base / f"{token}-outside"
    worktree.mkdir(parents=True, exist_ok=True)
    try:
        roots = [Path(r) for r in writable_roots(str(worktree), adapter)]
        if any(outside.resolve().is_relative_to(r) for r in roots):
            return {"sandboxed": None, "status": st,
                    "reason": f"inconclusive: {outside} is inside a sanctioned "
                              "root on this host, so there is no outside to test"}
        if build_argv is None:
            cmd_prefix = prefix(str(worktree), adapter)
        else:
            wrapped = build_argv(str(worktree), {META_KEY: True})
            plain = build_argv(str(worktree), {})
            if len(wrapped) <= len(plain) or wrapped[len(wrapped) - len(plain):] != plain:
                return {"sandboxed": False, "status": st,
                        "reason": "the adapter's build_argv does not apply the "
                                  "sandbox (no wrap_argv): its argv is unchanged"}
            cmd_prefix = wrapped[:len(wrapped) - len(plain)]
        state = declared_state(adapter)["state_dirs"]
        report = self_test(cmd_prefix, worktree=worktree, outside=outside,
                           state_dir=state[0] if state else None)
        report["status"] = st
        return report
    finally:
        shutil.rmtree(worktree, ignore_errors=True)
        outside.unlink(missing_ok=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.hooks.write_sandbox",
        description="The L3 write sandbox: host status and a live self-test.",
    )
    parser.add_argument("--status", action="store_true", help="backend and mode on this host")
    parser.add_argument("--self-test", action="store_true",
                        help="write inside / outside / state through the sandbox")
    parser.add_argument("--adapter", default=None, help="whose declared state to use")
    parser.add_argument("--json", action="store_true")
    opts = parser.parse_args(argv)
    if opts.self_test:
        result = probe(opts.adapter)
        code = 0 if result["sandboxed"] else 1
    elif opts.status:
        result = status()
        code = 0 if result["supported"] else 1
    else:
        parser.print_help()
        return 0
    if opts.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        verdict = result.get("sandboxed", result.get("supported"))
        print(f"L3 write sandbox: {verdict} -- {result['reason']}")
    return code


if __name__ == "__main__":
    sys.exit(main())
