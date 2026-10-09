#!/usr/bin/env python3
# CUI // SP-CTI
"""Prove a core change against every parent, on the developer's machine (xcore-compat-01).

WHY A LOCAL RUNNER AND NOT JUST CI. ``icdev-core`` is a separate repository and the two parents
live in separate checkouts, so the person changing core is the one person who has all three and
CI is the one place that has none of them at once. Finding out at PR time that a core edit broke
ICDEV[FT] costs a round trip through two repositories; finding out before pushing costs one
command.

    export ICDEV_CORE_PARENTS="/c/AI/ICDev;/c/ai/icdev_ft"
    python tools/dev/core_compat_local.py                    # install core, run each parent
    python tools/dev/core_compat_local.py --core ../icdev-core
    python tools/dev/core_compat_local.py --no-install       # parents only, core already installed
    python tools/dev/core_compat_local.py --json

Each parent declares its own compat suite in ``args/ci_test_files/core_compat.txt`` and its own
coherence tier (``coherence_checker --tier core``). A parent that declares NEITHER is reported
``undeclared`` and is NOT counted as passing -- "this parent has nothing to say" and "this parent
is happy" are different answers, and merging them is how a compat matrix ends up green over a
parent it never ran.

WHAT THIS DELIBERATELY DOES NOT DO. It never writes to a parent checkout, never installs into
the ambient interpreter without ``--install`` being the default-and-visible step, and never
merges anything. It reports.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

#: ``;``-separated parent roots. ``;`` rather than ``:`` because a Windows path
#: contains a colon and this project's primary developer machine is Windows.
PARENTS_ENV = "ICDEV_CORE_PARENTS"

#: Each parent declares its own suite here, relative to its root.
COMPAT_LIST = Path("args") / "ci_test_files" / "core_compat.txt"


class State:
    """What a parent leg concluded. Kept apart on purpose."""

    passed = "passed"
    failed = "failed"
    undeclared = "undeclared"
    """The parent declares no compat suite and no core tier. NOT a pass."""
    unreachable = "unreachable"
    """The path does not exist or is not a checkout. NOT a pass."""
    error = "error"
    """The leg could not be run — a harness failure, never a verdict."""


@dataclass
class LegResult:
    parent: str
    state: str
    tests: Optional[Dict[str, Any]] = None
    coherence: Optional[Dict[str, Any]] = None
    reason: str = ""
    duration_sec: float = 0.0

    def to_dict(self) -> dict:
        return {
            "parent": self.parent,
            "state": self.state,
            "tests": self.tests,
            "coherence": self.coherence,
            "reason": self.reason,
            "duration_sec": round(self.duration_sec, 1),
        }


@dataclass
class Report:
    core: str
    core_installed: Optional[str] = None
    legs: List[LegResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # `undeclared` and `unreachable` are NOT passes. A matrix that goes green
        # over a parent it never exercised is the defect this tool exists for.
        return bool(self.legs) and all(leg.state == State.passed for leg in self.legs)

    def to_dict(self) -> dict:
        return {
            "core": self.core,
            "core_installed": self.core_installed,
            "ok": self.ok,
            "legs": [leg.to_dict() for leg in self.legs],
        }


def parent_roots(explicit: Optional[List[str]] = None) -> List[Path]:
    raw = explicit if explicit else [
        p for p in os.environ.get(PARENTS_ENV, "").split(";") if p.strip()
    ]
    return [Path(p.strip()).expanduser() for p in raw if p.strip()]


def read_suite(root: Path) -> List[str]:
    """The parent's declared compat modules, comments and blanks dropped."""
    path = root / COMPAT_LIST
    if not path.is_file():
        return []
    out: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            out.append(entry)
    return out


def install_core(core_root: Path) -> str:
    """Install the core under test into the CURRENT interpreter.

    Editable, so the caller keeps iterating on core without reinstalling between runs. The
    version string is returned so the report can say WHICH core was proven -- a compat run
    that does not name the artefact it exercised proves nothing reproducible.
    """
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-e", str(core_root)],
        check=True, capture_output=True, text=True,
    )
    probe = subprocess.run(
        [sys.executable, "-c",
         "import importlib.metadata as m; print(m.version('icdev-core'))"],
        capture_output=True, text=True,
    )
    return probe.stdout.strip() or "unknown"


def _run(cmd: List[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    # Absolute and pointing at THIS parent: each leg must import the parent it is
    # measuring, not whichever checkout happens to be on sys.path already.
    env["PYTHONPATH"] = str(cwd)
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace", env=env,
    )


def run_leg(root: Path, timeout: int = 3600) -> LegResult:
    started = time.monotonic()
    if not root.is_dir():
        return LegResult(str(root), State.unreachable, reason="path does not exist")
    if not (root / ".git").exists():
        return LegResult(str(root), State.unreachable, reason="not a git checkout")

    suite = read_suite(root)
    checker = root / "tools" / "workflow" / "coherence_checker.py"
    if not suite and not checker.is_file():
        return LegResult(
            str(root), State.undeclared,
            reason=f"no {COMPAT_LIST.as_posix()} and no coherence_checker -- nothing declared",
        )

    tests: Optional[Dict[str, Any]] = None
    if suite:
        proc = _run([sys.executable, "-m", "pytest", *suite, "-q", "--tb=line"], root, timeout)
        tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-12:]
        tests = {"modules": len(suite), "exit_code": proc.returncode, "tail": tail}

    coherence: Optional[Dict[str, Any]] = None
    if checker.is_file():
        proc = _run(
            [sys.executable, str(checker), "--tier", "core", "--gate", "--json"], root, timeout
        )
        try:
            payload = json.loads(proc.stdout)
            coherence = {
                "exit_code": proc.returncode,
                "checks": payload.get("total_checks"),
                "failed": payload.get("failed_checks"),
                "warned": payload.get("warned_checks"),
                "failures": [
                    c["check_id"] for c in payload.get("checks", []) if c.get("status") == "fail"
                ],
            }
        except (json.JSONDecodeError, TypeError):
            coherence = {
                "exit_code": proc.returncode,
                "checks": None,
                "error": "coherence output was not JSON",
                "tail": proc.stderr.strip().splitlines()[-6:],
            }

    failed = bool(tests and tests["exit_code"] != 0) or bool(
        coherence and coherence["exit_code"] != 0
    )
    return LegResult(
        parent=str(root),
        state=State.failed if failed else State.passed,
        tests=tests,
        coherence=coherence,
        duration_sec=time.monotonic() - started,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--core", default=".", help="the icdev-core checkout under test")
    parser.add_argument(
        "--parent", action="append", default=None,
        help=f"a parent root (repeatable); defaults to ${PARENTS_ENV}",
    )
    parser.add_argument("--no-install", action="store_true", help="core is already installed")
    parser.add_argument("--timeout", type=int, default=3600, help="per-leg timeout, seconds")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    roots = parent_roots(args.parent)
    if not roots:
        print(
            f"No parents. Set {PARENTS_ENV} to a ';'-separated list of parent checkouts, "
            "or pass --parent. Refusing to report a matrix over zero parents."
        )
        # exit 2: could not run, which is never the same as nothing found.
        return 2

    report = Report(core=str(Path(args.core).resolve()))
    if not args.no_install:
        try:
            report.core_installed = install_core(Path(args.core).resolve())
        except subprocess.CalledProcessError as exc:
            print(f"ERROR: could not install core from {args.core}: {exc.stderr or exc}")
            return 2

    for root in roots:
        report.legs.append(run_leg(root.resolve(), timeout=args.timeout))

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        label = report.core_installed or "already installed"
        print(f"core-compat: icdev-core {label} from {report.core}\n")
        for leg in report.legs:
            print(f"  [{leg.state}] {leg.parent}  ({leg.duration_sec:.0f}s)")
            if leg.reason:
                print(f"      {leg.reason}")
            if leg.tests:
                print(f"      tests    : {leg.tests['modules']} module(s), "
                      f"exit {leg.tests['exit_code']}")
                if leg.tests["exit_code"] != 0:
                    for line in leg.tests["tail"]:
                        print(f"        | {line}")
            if leg.coherence:
                fails = leg.coherence.get("failures") or []
                print(f"      coherence: {leg.coherence.get('checks')} check(s), "
                      f"{leg.coherence.get('failed')} failed"
                      + (f" -> {', '.join(fails)}" if fails else ""))
        print()
        print("OK — every parent passed" if report.ok else "NOT OK — see the legs above")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
