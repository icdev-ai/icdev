#!/usr/bin/env python3
# CUI // SP-CTI
"""Ghidra headless decompilation -- an OPTIONAL backend that says when it is absent (xrv-bin-02).

WHY THIS IS A SEPARATE MODULE AND A SEPARATE ANALYZER. ``binary_triage``
(xrv-bin-01) reads an artifact's own bytes and executes NOTHING -- no
``subprocess``, no ``ctypes``, asserted from its AST by a test. This module
SPAWNS A JVM. Those are different trust questions and they must not share a
report, a posture or a declaration: folding a decompiler into the pure-Python
triage module would silently widen that module's guarantee, and that guarantee
is the reason its posture in Gap 71 could be argued at all.

WHY IT IS OPTIONAL, AND WHY THAT IS THE WHOLE DESIGN. NSA Ghidra (Apache-2.0)
is the natural decompiler for a Gov/DoD platform and it runs headless. It also
needs a JDK 21+ and a ~400 MB install, so it is NOT in ``requirements.txt`` and
cannot be. A capability that is declared and then silently does nothing is this
repository's signature defect -- ``coherence_checker --check
capability_liveness`` exists for it -- so the one thing this module may never
do is return an empty, successful-looking report on a host with no Ghidra.

FIVE STATUSES, NEVER MERGED, because each sends a reader somewhere different:

    ok            Ghidra ran, the export script wrote its JSON, and it was read
    truncated     it ran and a DECLARED BOUND was hit. Everything reported is a
                  statement about a PREFIX of the program, and says so
    unavailable   NO GHIDRA WAS FOUND (or it is switched off). Nothing was run
                  and NOTHING is reported about the artifact. This is the
                  status on a default install and it is the CORRECT reading --
                  it is not a clean bill of health and it is not an error
    timeout       it ran past its wall budget and the PROCESS TREE was killed
    error         it ran and failed: a non-zero exit, no JSON, or JSON that
                  would not parse

AN EMPTY LIST AND AN UNMEASURED ONE ARE DIFFERENT ANSWERS. ``functions``,
``imports`` and ``strings`` are ``None`` -- never ``[]`` -- whenever this module
did not get an answer, with the reason named beside them in
``functions_basis`` / ``imports_basis`` / ``strings_basis``:

    parsed              the export script produced this list. ``[]`` is MEASURED
    ghidra_unavailable  no analyzeHeadless on this host
    disabled_by_env     ICDEV_GHIDRA_ENABLED=0
    run_timeout         the tree was killed before the script wrote anything
    run_failed          Ghidra exited non-zero
    export_absent       Ghidra exited 0 and wrote no JSON at all
    export_malformed    the JSON did not parse
    script_error        the script ran and recorded a failure for THIS section

``[]`` from a run that never happened would read as "Ghidra looked at this
binary and found no functions" -- a claim about the ARTIFACT, made by a run
that never opened it. That is the defect this whole card series exists for, and
it is why the shape sketched in the card (``imports: []``) ships ``None``-able.

THE TWO TIMEOUTS ARE NOT THE SAME TIMEOUT, and getting this wrong loses the
report. ``-analysisTimeoutPerFile`` is GHIDRA'S: when it fires, Ghidra stops
analysing, runs the postScript ANYWAY, and this module gets a partial export it
can label ``truncated``. The wall budget is OURS: when it fires the tree is
killed, no JSON exists, and there is nothing to label at all. So the analysis
timeout is set BELOW the wall budget by ``GHIDRA_STARTUP_OVERHEAD_SECONDS``
(JVM start, import, postScript, ``-deleteProject``) -- a partial answer is
strictly better than a dead one.

THE PROCESS TREE IS KILLED, NOT THE PROCESS. ``analyzeHeadless`` is a shell
script / ``.bat`` that execs a JVM, so ``proc.kill()`` terminates the WRAPPER
and leaves the JVM running -- holding the pipes ``communicate()`` is blocked on
and holding the project directory. The helper is
``tools.genesis.reflexes.kanban._kill_process_tree``, IMPORTED and never
re-implemented: two spellings of "kill the tree" is how the two halves of one
policy come to disagree (mfx-mrg-04, mfx-ci-04). It is imported LAZILY, inside
the timeout branch only, because that import costs ~0.5s and 467 modules
(measured 2026-09-12) and a run that does not time out must not pay it. An
import that fails falls back to ``proc.kill()`` and SAYS SO in ``kill_method``
-- a reader of a ``timeout`` report must be able to tell a tree that was reaped
from one that may still be holding the CPU.

THE ARTIFACT IS ANALYSED, NOT RUN -- by Ghidra's loaders, which is a weaker
statement than ``binary_triage``'s and is why the posture is argued separately.
Ghidra is a large Java application parsing attacker-controlled structures, so
the posture is ``sandboxed`` (docs/security/sandbox-coverage.md, Gap 71). On a
stock deployment that means ``sandbox_unavailable`` at the dispatch layer AND
``unavailable`` here, and BOTH of those readings are correct rather than a
fault to be worked around.

GHIDRA 12 RUNS PYTHON THROUGH PYGHIDRA, SO THIS MODULE DOES TOO (rea-eng-02).
Ghidra 12.0 made PyGhidra (CPython 3 over JPype) the default Python engine and
moved Jython out to an optional extension; a ``.py`` postScript handed to a
plain ``analyzeHeadless`` on 12.x has no Python to run in. So the run is
launched the way ``support/pyghidraRun -H`` launches it --
``<python> -m pyghidra.ghidra_launch --install-dir <home>
ghidra.app.util.headless.AnalyzeHeadless ...`` -- but WITHOUT ``pyghidraRun``,
whose first run stops at ``input()`` to offer a pip install and dies with
``EOFError`` under a pipe. The minimum is pinned at ``MIN_GHIDRA_VERSION``
(12.0): an older install is ``unavailable`` with ``ghidra_version_unsupported``,
never a half-run against a script runtime that is not there. The interpreter
that carries the ``pyghidra`` module is LOCATED, not assumed, because it is
rarely ICDEV's own: Arch's ``ghidra`` package puts ``pyghidra`` in the SYSTEM
python, a Windows zip install needs ``pip install --no-index -f
<home>/Ghidra/Features/PyGhidra/pypkg/dist pyghidra`` into one. Install steps:
docs/install/ghidra.md.

EVERY RUN NAMES WHAT IT WAS ALLOWED TO OPEN. Reverse engineering is for
authorized targets only (project ``rea``): a run records the artifact's sha256
and the operator's authorization basis (``owned`` | ``licensed`` |
``customer-authorized``, from ``authorization_basis=`` or
``ICDEV_RE_AUTHORIZATION_BASIS``) under ``provenance``, and a run with NO basis
is REFUSED -- ``error`` / ``authorization_missing`` -- before a JVM starts.
Functions, imports and strings are ``observed`` (read by Ghidra's loaders);
``entry_decompiled`` is ``decompiled`` -- a RECONSTRUCTION with a stated
confidence, never presented as recovered source.

NOTHING IS WRITTEN OUTSIDE A TEMPORARY DIRECTORY. The Ghidra project and the
export JSON live under one ``tempfile.mkdtemp`` removed on EVERY path,
including the timeout path. ``-deleteProject`` is passed as well, so Ghidra
tears its own project down even when the directory removal is what fails.

Usage:
    python -m tools.analyzers.ghidra_headless /path/to/artifact --json
    python -m tools.analyzers.ghidra_headless /path/to/artifact
    python -m tools.analyzers.ghidra_headless /path/to/artifact --timeout 900
    python -m tools.analyzers.ghidra_headless ./fixture --authorization-basis owned --json

    >>> from icdev.tools.analyzers.ghidra_headless import decompile
    >>> report = decompile("/bin/ls")
    >>> report["status"]          # on a host with no Ghidra
    'unavailable'

Exit codes: 0 a report was produced, whatever it says (``unavailable`` IS a
report); 2 no report could be produced at all.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "STATUSES",
    "build_argv",
    "decompile",
    "ghidra_version",
    "locate_ghidra",
    "locate_pyghidra",
]

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_TRUNCATED = "truncated"
STATUS_UNAVAILABLE = "unavailable"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"
STATUSES: Tuple[str, ...] = (
    STATUS_OK,
    STATUS_TRUNCATED,
    STATUS_UNAVAILABLE,
    STATUS_TIMEOUT,
    STATUS_ERROR,
)

#: Why a list is ``None``. Each names a DIFFERENT repair, so none is ever
#: collapsed into a generic "unavailable".
BASIS_PARSED = "parsed"
BASIS_GHIDRA_UNAVAILABLE = "ghidra_unavailable"
BASIS_DISABLED = "disabled_by_env"
BASIS_TIMEOUT = "run_timeout"
BASIS_FAILED = "run_failed"
BASIS_EXPORT_ABSENT = "export_absent"
BASIS_EXPORT_MALFORMED = "export_malformed"
BASIS_SCRIPT_ERROR = "script_error"
BASIS_SOURCE_UNREADABLE = "source_unreadable"
BASIS_AUTHORIZATION_MISSING = "authorization_missing"

#: Why Ghidra could not be located. ``ghidra_home_invalid`` is deliberately NOT
#: merged into ``not_found``: an operator who SET the variable and got the path
#: wrong needs a different sentence from one who never set it.
REASON_NOT_FOUND = "not_found"
REASON_HOME_INVALID = "ghidra_home_invalid"
REASON_DISABLED = "disabled_by_env"
REASON_UNDECLARED = "undeclared_in_tool_index"
#: Ghidra was found and cannot run THIS module's script. Each is its own
#: reason because each is a different install step.
REASON_VERSION_UNDETERMINED = "ghidra_version_undetermined"
REASON_VERSION_UNSUPPORTED = "ghidra_version_unsupported"
REASON_PYGHIDRA_MISSING = "pyghidra_unavailable"
REASON_PYTHON_INVALID = "ghidra_python_invalid"

#: What makes a target one this platform may open (project ``rea`` rails).
#: Closed: a free-text basis is one nobody can audit.
AUTHORIZATION_BASES: Tuple[str, ...] = ("owned", "licensed", "customer-authorized")

#: Where each section's evidence comes from. Functions, imports and strings are
#: READ from the program by Ghidra's loaders; the entry body is RECONSTRUCTED.
EVIDENCE_BASIS: Dict[str, str] = {
    "functions": "observed",
    "imports": "observed",
    "strings": "observed",
    "entry_decompiled": "decompiled",
}

#: Decompiler C is a plausible reading of machine code -- names, types and
#: control flow are inferred -- so it is never better than ``low`` confidence
#: as a statement of what the original source said.
DECOMPILED_CONFIDENCE = "low"

# ---------------------------------------------------------------------------
# Environment / bounds. Every one is reported in ``limits``; a hit bound is
# never a quietly short list.
# ---------------------------------------------------------------------------

ENV_GHIDRA_HOME = "ICDEV_GHIDRA_HOME"
ENV_ENABLED = "ICDEV_GHIDRA_ENABLED"
ENV_TIMEOUT = "ICDEV_GHIDRA_TIMEOUT"
ENV_MAX_CPU = "ICDEV_GHIDRA_MAX_CPU"
ENV_MAX_FUNCTIONS = "ICDEV_GHIDRA_MAX_FUNCTIONS"
ENV_MAX_STRINGS = "ICDEV_GHIDRA_MAX_STRINGS"
ENV_PYTHON = "ICDEV_GHIDRA_PYTHON"
ENV_AUTHORIZATION = "ICDEV_RE_AUTHORIZATION_BASIS"

#: The oldest Ghidra whose default Python engine is PyGhidra (12.0).
MIN_GHIDRA_VERSION: Tuple[int, int] = (12, 0)

#: How PyGhidra starts a Ghidra class -- exactly what ``support/pyghidraRun -H``
#: runs, minus its interactive install prompt.
PYGHIDRA_LAUNCH_MODULE = "pyghidra.ghidra_launch"
HEADLESS_CLASS = "ghidra.app.util.headless.AnalyzeHeadless"

#: Bound on ``<python> -c "import pyghidra"``. It imports JPype, not a JVM.
PYGHIDRA_PROBE_TIMEOUT_SECONDS = 60

#: Package-manager install roots, probed after ICDEV_GHIDRA_HOME and PATH.
#: Arch's ``ghidra`` package installs to /opt/ghidra and puts only
#: ``ghidra-analyzeHeadless`` on PATH, so PATH alone never finds it. A Windows
#: zip has no conventional root: there, ICDEV_GHIDRA_HOME is how it is named.
WELL_KNOWN_HOMES: Tuple[str, ...] = (
    () if os.name == "nt" else ("/opt/ghidra", "/usr/share/ghidra")
)

#: Wall budget for the whole ``analyzeHeadless`` run. Ghidra's import and
#: auto-analysis of a mid-sized binary is minutes, not seconds.
DEFAULT_TIMEOUT_SECONDS = 600

#: JVM start + import + postScript + ``-deleteProject``, subtracted from the
#: wall budget to get Ghidra's own ``-analysisTimeoutPerFile``. Deliberately
#: generous: overshooting it costs the WHOLE report, undershooting it costs
#: some analysis depth.
GHIDRA_STARTUP_OVERHEAD_SECONDS = 60

#: Floor for Ghidra's own analysis budget. Below this a run cannot finish an
#: import at all, so a very small wall budget yields ``timeout`` rather than a
#: Ghidra that gives up before it has read anything.
MIN_ANALYSIS_TIMEOUT_SECONDS = 30

DEFAULT_MAX_CPU = 2
DEFAULT_MAX_FUNCTIONS = 2000
DEFAULT_MAX_STRINGS = 500

#: The name Ghidra's launcher carries. ``shutil.which`` resolves PATHEXT on
#: Windows, so the bare stem suffices for the PATH probe; the explicit suffix
#: matters only for the ``ICDEV_GHIDRA_HOME`` join, which is a filesystem test.
HEADLESS_STEM = "analyzeHeadless"
HEADLESS_WINDOWS = "analyzeHeadless.bat"

#: Ghidra's project name inside the throwaway project directory. Never a shared
#: location: a project directory that outlived one run is a second run's stale
#: state.
PROJECT_NAME = "icdev"

#: The Ghidra script this module runs, and the directory handed to
#: ``-scriptPath``. Module-local on purpose -- it moves WITH this file, so the
#: packaged ``icdev/tools/analyzers/`` copy finds the copy beside IT.
EXPORT_SCRIPT_NAME = "icdev_export.py"
SCRIPT_DIR = Path(__file__).resolve().parent / "ghidra_scripts"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_int(name: str, default: int) -> int:
    """A malformed override is IGNORED, not fatal -- and the value actually
    used is on the report under ``limits``, so a typo stays visible."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def enabled() -> bool:
    """``ICDEV_GHIDRA_ENABLED=0`` switches the backend off, and off is REPORTED
    (``reason: disabled_by_env``) rather than looking like an absent install."""
    return str(os.environ.get(ENV_ENABLED, "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


# ---------------------------------------------------------------------------
# Locating Ghidra
# ---------------------------------------------------------------------------


def _headless_names() -> Tuple[str, ...]:
    """Both spellings, most-specific first. A Ghidra install carries BOTH
    ``analyzeHeadless`` (sh) and ``analyzeHeadless.bat`` on every platform, so
    the Windows probe must PREFER the ``.bat`` rather than take whichever the
    directory yields first -- handing the sh script to Windows fails with a
    message about shell syntax, which reads as a broken install."""
    if os.name == "nt":
        return (HEADLESS_WINDOWS, HEADLESS_STEM)
    return (HEADLESS_STEM,)


def locate_ghidra(ghidra_home: Optional[str] = None) -> Dict[str, Any]:
    """Resolve ``analyzeHeadless``; say WHERE it came from, or WHY it did not.

    Two independent routes, declared home FIRST, because an operator who set
    ``ICDEV_GHIDRA_HOME`` has named the install they mean and a PATH hit on a
    different one would silently ignore them.

    Returns ``{headless, home, source, reason}``. ``headless`` is None when
    nothing was found, and ``reason`` then names WHICH absence it is.
    """
    home_raw = ghidra_home if ghidra_home is not None else os.environ.get(ENV_GHIDRA_HOME)
    if home_raw:
        home = Path(str(home_raw)).expanduser()
        for name in _headless_names():
            candidate = home / "support" / name
            if candidate.is_file():
                return {
                    "headless": str(candidate),
                    "home": str(home),
                    "source": ENV_GHIDRA_HOME,
                    "reason": None,
                }
        # The variable is SET and wrong. Falling through to the PATH probe here
        # would answer with a DIFFERENT install than the operator named.
        return {
            "headless": None,
            "home": str(home),
            "source": ENV_GHIDRA_HOME,
            "reason": REASON_HOME_INVALID,
        }

    # tool_index is the ONE declared list of external binaries this platform
    # shells out to (xrv-route-01). Its `which` raises KeyError for an
    # UNDECLARED name on purpose, so an index that has not declared
    # analyzeHeadless is reported as such rather than read as "not installed".
    try:
        from tools.dx.tool_index import which as _which

        found = _which(HEADLESS_STEM)
    except KeyError:
        return {
            "headless": None,
            "home": None,
            "source": "tool_index",
            "reason": REASON_UNDECLARED,
        }
    except Exception:  # noqa: BLE001 -- an unreadable index is not an install
        found = shutil.which(HEADLESS_STEM)

    if not found:
        for candidate_home in WELL_KNOWN_HOMES:
            for name in _headless_names():
                candidate = Path(candidate_home) / "support" / name
                if candidate.is_file():
                    return {
                        "headless": str(candidate),
                        "home": str(candidate_home),
                        "source": "well_known_home",
                        "reason": None,
                    }
        return {"headless": None, "home": None, "source": "PATH", "reason": REASON_NOT_FOUND}

    # A PATH entry is often a symlink into the install (usr/bin -> opt/...);
    # the home is derived from where it POINTS.
    headless = Path(found).resolve()
    # <home>/support/analyzeHeadless -- the home is two levels up. DERIVED, not
    # assumed: a launcher somewhere else still resolves, and `home` then reads
    # None so `ghidra_version` reports None rather than a guess.
    parent = headless.parent
    home_path = parent.parent if parent.name == "support" else None
    return {
        "headless": str(headless),
        "home": str(home_path) if home_path else None,
        "source": "PATH",
        "reason": None,
    }


def _version_tuple(version: Optional[str]) -> Optional[Tuple[int, ...]]:
    """``"12.1.4"`` -> ``(12, 1, 4)``; a suffix (``12.1_DEV``) is dropped. None
    when no leading number can be read -- never a guessed version."""
    if not version:
        return None
    parts: List[int] = []
    for piece in str(version).split("."):
        digits = ""
        for ch in piece:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
        if len(digits) != len(piece):
            break
    return tuple(parts) or None


def _probe_pyghidra(python: str) -> Tuple[bool, Optional[str]]:
    """Run ``<python> -c "import pyghidra"``. ``(importable, why_not)``."""
    try:
        proc = subprocess.run(  # noqa: S603 -- argv list, never a shell
            [python, "-c", "import pyghidra"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PYGHIDRA_PROBE_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)
    if proc.returncode == 0:
        return True, None
    return False, _tail(proc.stderr, 300) or "exit %s" % proc.returncode


def locate_pyghidra(python: Optional[str] = None) -> Dict[str, Any]:
    """Find an interpreter that can ``import pyghidra``; say which, or why none.

    A DECLARED interpreter (``python=`` or ICDEV_GHIDRA_PYTHON) is the only one
    tried, for the reason ICDEV_GHIDRA_HOME is: an operator who named one must
    not be answered by a different one. Otherwise ICDEV's own interpreter, the
    interpreter its venv was made from, then ``python3`` / ``python`` on PATH
    -- the system python is where Arch's ``ghidra`` package installs
    ``pyghidra``, and ICDEV usually runs from a venv over it.

    Returns ``{python, source, reason, detail}``.
    """
    declared = python if python is not None else (os.environ.get(ENV_PYTHON) or "").strip()
    if declared:
        ok, why = _probe_pyghidra(declared)
        if ok:
            return {"python": declared, "source": ENV_PYTHON, "reason": None, "detail": None}
        return {
            "python": None,
            "source": ENV_PYTHON,
            "reason": REASON_PYTHON_INVALID,
            "detail": "%s=%r cannot import pyghidra (%s)" % (ENV_PYTHON, declared, why),
        }

    tried: List[str] = []
    candidates = [sys.executable, getattr(sys, "_base_executable", None)] + [
        shutil.which(n) for n in ("python3", "python")
    ]
    for candidate in candidates:
        if not candidate:
            continue
        key = os.path.normcase(os.path.abspath(candidate))
        if key in tried:
            continue
        tried.append(key)
        ok, _why = _probe_pyghidra(candidate)
        if ok:
            source = {
                # Base first: outside a venv the two are one path, and then it
                # is ICDEV's own interpreter.
                getattr(sys, "_base_executable", None): "venv_base",
                sys.executable: "sys.executable",
            }.get(candidate, "PATH")
            return {"python": candidate, "source": source, "reason": None, "detail": None}
    return {
        "python": None,
        "source": None,
        "reason": REASON_PYGHIDRA_MISSING,
        "detail": "no interpreter can import pyghidra (tried %s)" % ", ".join(tried),
    }


def _target_sha256(target: Path) -> str:
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ghidra_version(home: Optional[str]) -> Optional[str]:
    """Read the version from ``Ghidra/application.properties``.

    A FILE READ, never ``analyzeHeadless -version``: starting a JVM to learn a
    version string would put tens of seconds onto a call whose whole answer may
    be ``unavailable``. None means NOT DETERMINED -- never a guess, and never a
    placeholder that would later be quoted as the version that produced a
    report.
    """
    if not home:
        return None
    props = Path(home) / "Ghidra" / "application.properties"
    try:
        text = props.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("application.version="):
            return line.split("=", 1)[1].strip() or None
    return None


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def build_argv(
    python: str,
    home: str,
    project_dir: Path,
    target: Path,
    out_json: Path,
    *,
    analysis_timeout: int,
    max_cpu: int,
    max_functions: int,
    max_strings: int,
) -> List[str]:
    """The exact command line, as ONE function, so a test can read it.

    ``-deleteProject`` is passed even though the whole directory is removed in
    a ``finally``: on Windows a live file handle can defeat the directory
    removal, and Ghidra tearing its own project down first is what makes that
    survivable rather than a leaked 400 MB.

    Ghidra's headless arguments follow ``HEADLESS_CLASS`` unchanged:
    ``ghidra_launch`` hands everything it does not parse to ``Ghidra.main``.
    """
    return [
        python,
        "-m",
        PYGHIDRA_LAUNCH_MODULE,
        "--install-dir",
        str(home),
        HEADLESS_CLASS,
        str(project_dir),
        PROJECT_NAME,
        "-import",
        str(target),
        "-postScript",
        EXPORT_SCRIPT_NAME,
        str(out_json),
        str(max_functions),
        str(max_strings),
        "-scriptPath",
        str(SCRIPT_DIR),
        "-deleteProject",
        "-analysisTimeoutPerFile",
        str(analysis_timeout),
        "-max-cpu",
        str(max_cpu),
    ]


def _kill_tree(proc: subprocess.Popen) -> str:
    """Kill the JVM AND its wrapper. Imported lazily; failure is REPORTED."""
    try:
        from tools.genesis.reflexes.kanban import _kill_process_tree

        return _kill_process_tree(proc)
    except Exception as exc:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        return "tree helper unavailable (%s: %s); parent killed only" % (
            type(exc).__name__,
            exc,
        )


def _report(
    path: str,
    status: str,
    *,
    reason: Optional[str],
    reason_detail: Optional[str],
    basis: str,
    located: Dict[str, Any],
    limits: Dict[str, Any],
    functions: Optional[List[Dict[str, Any]]] = None,
    imports: Optional[List[str]] = None,
    strings: Optional[List[str]] = None,
    entry_decompiled: Optional[str] = None,
    entry_basis: Optional[str] = None,
    truncation: Optional[Dict[str, Optional[bool]]] = None,
    exit_code: Optional[int] = None,
    duration_seconds: Optional[float] = None,
    kill_method: Optional[str] = None,
    stderr_tail: Optional[str] = None,
    runtime: Optional[Dict[str, Any]] = None,
    provenance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """One constructor, so EVERY status carries the same keys.

    A caller must never have to ask whether a key exists before reading it --
    that is how ``report.get("functions") or []`` comes to be written, which
    puts the empty list back in by the side door.
    """
    return {
        "path": path,
        "status": status,
        "reason": reason,
        "reason_detail": reason_detail,
        "functions": functions,
        "functions_basis": BASIS_PARSED if functions is not None else basis,
        "imports": imports,
        "imports_basis": BASIS_PARSED if imports is not None else basis,
        "strings": strings,
        "strings_basis": BASIS_PARSED if strings is not None else basis,
        "entry_decompiled": entry_decompiled,
        "entry_decompiled_basis": entry_basis or (BASIS_PARSED if entry_decompiled else basis),
        "entry_decompiled_confidence": DECOMPILED_CONFIDENCE if entry_decompiled else None,
        "evidence_basis": dict(EVIDENCE_BASIS),
        "ghidra_version": ghidra_version(located.get("home")),
        "ghidra_home": located.get("home"),
        "headless": located.get("headless"),
        "headless_source": located.get("source"),
        "truncation": truncation
        if truncation is not None
        else {"functions": None, "strings": None},
        "exit_code": exit_code,
        "duration_seconds": duration_seconds,
        "kill_method": kill_method,
        "stderr_tail": stderr_tail,
        "runtime": runtime
        if runtime is not None
        else {"engine": "pyghidra", "python": None, "python_source": None},
        "provenance": provenance
        if provenance is not None
        else {"target_sha256": None, "authorization_basis": None, "analysis": "local"},
        "limits": limits,
        "generated_at": _iso_now(),
    }


def _unavailable(
    path: str,
    located: Dict[str, Any],
    *,
    reason: str,
    limits: Dict[str, Any],
    detail_extra: Optional[str] = None,
) -> Dict[str, Any]:
    basis = BASIS_DISABLED if reason == REASON_DISABLED else BASIS_GHIDRA_UNAVAILABLE
    detail = {
        REASON_NOT_FOUND: (
            "no %s on PATH and %s is not set. This is not a finding about the "
            "artifact -- nothing was run." % (HEADLESS_STEM, ENV_GHIDRA_HOME)
        ),
        REASON_HOME_INVALID: (
            "%s is set to %r but no support/%s exists under it."
            % (ENV_GHIDRA_HOME, located.get("home"), HEADLESS_STEM)
        ),
        REASON_DISABLED: "%s=0; the backend is switched off on this deployment." % ENV_ENABLED,
        REASON_UNDECLARED: (
            "%s is not declared in args/tool_index.yaml, so PATH was not probed." % HEADLESS_STEM
        ),
        REASON_VERSION_UNDETERMINED: (
            "Ghidra found at %r but its version could not be read from "
            "Ghidra/application.properties; %s.%s or later is required."
            % (located.get("home"), MIN_GHIDRA_VERSION[0], MIN_GHIDRA_VERSION[1])
        ),
        REASON_VERSION_UNSUPPORTED: (
            "Ghidra %s found; %s.%s or later is required (PyGhidra is its default "
            "Python engine). Upgrade the install named by %s."
            % (
                ghidra_version(located.get("home")),
                MIN_GHIDRA_VERSION[0],
                MIN_GHIDRA_VERSION[1],
                ENV_GHIDRA_HOME,
            )
        ),
        REASON_PYGHIDRA_MISSING: (
            "Ghidra is installed but no Python can import pyghidra. Install it from "
            "the bundled wheel: python -m pip install --no-index -f "
            "<ghidra_home>/Ghidra/Features/PyGhidra/pypkg/dist pyghidra -- or set %s."
            % ENV_PYTHON
        ),
        REASON_PYTHON_INVALID: "%s names an interpreter that cannot import pyghidra." % ENV_PYTHON,
    }.get(reason, reason)
    if detail_extra:
        detail = "%s (%s)" % (detail, detail_extra)
    return _report(
        path,
        STATUS_UNAVAILABLE,
        reason=reason,
        reason_detail=detail,
        basis=basis,
        located=located,
        limits=limits,
    )


def decompile(
    path: str,
    *,
    timeout_s: Optional[int] = None,
    max_cpu: Optional[int] = None,
    ghidra_home: Optional[str] = None,
    max_functions: Optional[int] = None,
    max_strings: Optional[int] = None,
    ghidra_python: Optional[str] = None,
    authorization_basis: Optional[str] = None,
) -> Dict[str, Any]:
    """Decompile one artifact with Ghidra headless, or say why it did not.

    Every keyword carries a default, so the analyzer contract binds only the
    observable (``args/analyzer_contract.yaml`` -> ``binding.observable_arg``).
    """
    timeout_s = (
        timeout_s if timeout_s is not None else _env_int(ENV_TIMEOUT, DEFAULT_TIMEOUT_SECONDS)
    )
    max_cpu = max_cpu if max_cpu is not None else _env_int(ENV_MAX_CPU, DEFAULT_MAX_CPU)
    max_functions = (
        max_functions
        if max_functions is not None
        else _env_int(ENV_MAX_FUNCTIONS, DEFAULT_MAX_FUNCTIONS)
    )
    max_strings = (
        max_strings if max_strings is not None else _env_int(ENV_MAX_STRINGS, DEFAULT_MAX_STRINGS)
    )
    analysis_timeout = max(
        MIN_ANALYSIS_TIMEOUT_SECONDS, timeout_s - GHIDRA_STARTUP_OVERHEAD_SECONDS
    )
    limits = {
        "timeout_s": timeout_s,
        "analysis_timeout_s": analysis_timeout,
        "max_cpu": max_cpu,
        "max_functions": max_functions,
        "max_strings": max_strings,
    }

    if not enabled():
        return _unavailable(
            str(path), locate_ghidra(ghidra_home), reason=REASON_DISABLED, limits=limits
        )

    located = locate_ghidra(ghidra_home)
    if not located["headless"]:
        return _unavailable(str(path), located, reason=located["reason"], limits=limits)

    # PyGhidra needs the install ROOT (``--install-dir``) and 12.0+. A launcher
    # found with no derivable root, or no readable version, is not runnable.
    version = _version_tuple(ghidra_version(located.get("home")))
    if version is None:
        return _unavailable(str(path), located, reason=REASON_VERSION_UNDETERMINED, limits=limits)
    if version[:2] < MIN_GHIDRA_VERSION:
        return _unavailable(str(path), located, reason=REASON_VERSION_UNSUPPORTED, limits=limits)

    py = locate_pyghidra(ghidra_python)
    runtime = {"engine": "pyghidra", "python": py["python"], "python_source": py["source"]}
    if not py["python"]:
        report = _unavailable(
            str(path), located, reason=py["reason"], limits=limits, detail_extra=py["detail"]
        )
        report["runtime"] = runtime
        return report

    # The artifact is checked AFTER the backend, deliberately: on a host with no
    # Ghidra the answer is `unavailable` whatever the path says, and reporting
    # `source_unreadable` there would send a reader to look at their file when
    # the thing that is missing is the decompiler.
    target = Path(path).expanduser()
    try:
        readable = target.is_file()
    except OSError:
        readable = False
    if not readable:
        return _report(
            str(path),
            STATUS_ERROR,
            reason=BASIS_SOURCE_UNREADABLE,
            reason_detail="not a readable file; nothing is reported about the artifact.",
            basis=BASIS_SOURCE_UNREADABLE,
            located=located,
            limits=limits,
            runtime=runtime,
        )

    # Authorization is checked once the run is otherwise possible, and BEFORE
    # the JVM: the sha256 is recorded on the refusal too, so the report says
    # WHICH artifact was not opened.
    sha256 = _target_sha256(target)
    basis_raw = (
        authorization_basis
        if authorization_basis is not None
        else os.environ.get(ENV_AUTHORIZATION, "")
    )
    auth = str(basis_raw or "").strip().lower()
    provenance = {
        "target_sha256": sha256,
        "authorization_basis": auth if auth in AUTHORIZATION_BASES else None,
        "analysis": "local",
    }
    if auth not in AUTHORIZATION_BASES:
        return _report(
            str(path),
            STATUS_ERROR,
            reason=BASIS_AUTHORIZATION_MISSING,
            reason_detail=(
                "no authorization basis for this target (%s): pass "
                "authorization_basis= or set %s. Nothing was run."
                % (" | ".join(AUTHORIZATION_BASES), ENV_AUTHORIZATION)
            ),
            basis=BASIS_AUTHORIZATION_MISSING,
            located=located,
            limits=limits,
            runtime=runtime,
            provenance=provenance,
        )

    workdir = Path(tempfile.mkdtemp(prefix="icdev-ghidra-"))
    project_dir = workdir / "project"
    out_json = workdir / "export.json"
    project_dir.mkdir(parents=True, exist_ok=True)
    argv = build_argv(
        py["python"],
        located["home"],
        project_dir,
        target.resolve(),
        out_json,
        analysis_timeout=analysis_timeout,
        max_cpu=max_cpu,
        max_functions=max_functions,
        max_strings=max_strings,
    )

    started = time.monotonic()
    try:
        try:
            proc = subprocess.Popen(  # noqa: S603 -- argv list, never a shell
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # Nothing to read: a launcher that stops to ask a question gets
                # EOF at once rather than holding the wall budget.
                stdin=subprocess.DEVNULL,
                cwd=str(workdir),
                text=True,
                encoding="utf-8",
                errors="replace",
                # POSIX only, ignored on Windows: _kill_process_tree's killpg is
                # safe ONLY because the child is in a session of its own.
                start_new_session=True,
            )
        except OSError as exc:
            return _report(
                str(path),
                STATUS_ERROR,
                reason="spawn_failed",
                reason_detail="%s: %s" % (type(exc).__name__, exc),
                basis=BASIS_FAILED,
                located=located,
                limits=limits,
                runtime=runtime,
                provenance=provenance,
                duration_seconds=round(time.monotonic() - started, 3),
            )

        try:
            _out, err = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            how = _kill_tree(proc)
            try:
                _out, err = proc.communicate(timeout=30)
            except Exception:  # noqa: BLE001 -- the report matters, not the tail
                err = ""
            return _report(
                str(path),
                STATUS_TIMEOUT,
                reason="wall_budget_exceeded",
                reason_detail=(
                    "killed after %ss (%s). No export was written, so nothing is "
                    "reported about the artifact." % (timeout_s, ENV_TIMEOUT)
                ),
                basis=BASIS_TIMEOUT,
                located=located,
                limits=limits,
                runtime=runtime,
                provenance=provenance,
                duration_seconds=round(time.monotonic() - started, 3),
                kill_method=how,
                stderr_tail=_tail(err),
            )

        duration = round(time.monotonic() - started, 3)
        code = proc.returncode
        if code != 0:
            return _report(
                str(path),
                STATUS_ERROR,
                reason="nonzero_exit",
                reason_detail="analyzeHeadless exited %s" % code,
                basis=BASIS_FAILED,
                located=located,
                limits=limits,
                runtime=runtime,
                provenance=provenance,
                exit_code=code,
                duration_seconds=duration,
                stderr_tail=_tail(err),
            )

        if not out_json.is_file():
            # Exit 0 and no JSON. Ghidra's launcher exits 0 for several things
            # that are NOT a successful analysis (an unrecognised loader, a
            # postScript that never ran), so this can never be read as "it ran
            # and there was nothing to report".
            return _report(
                str(path),
                STATUS_ERROR,
                reason="export_absent",
                reason_detail=(
                    "analyzeHeadless exited 0 but wrote no export; the postScript "
                    "did not run, or could not write."
                ),
                basis=BASIS_EXPORT_ABSENT,
                located=located,
                limits=limits,
                runtime=runtime,
                provenance=provenance,
                exit_code=code,
                duration_seconds=duration,
                stderr_tail=_tail(err),
            )

        try:
            payload = json.loads(out_json.read_text(encoding="utf-8", errors="replace"))
            if not isinstance(payload, dict):
                raise ValueError("export root is %s, expected object" % type(payload).__name__)
        except (OSError, ValueError) as exc:
            return _report(
                str(path),
                STATUS_ERROR,
                reason="export_malformed",
                reason_detail="%s: %s" % (type(exc).__name__, exc),
                basis=BASIS_EXPORT_MALFORMED,
                located=located,
                limits=limits,
                runtime=runtime,
                provenance=provenance,
                exit_code=code,
                duration_seconds=duration,
                stderr_tail=_tail(err),
            )

        return _from_payload(
            str(path),
            payload,
            located=located,
            limits=limits,
            exit_code=code,
            duration_seconds=duration,
            stderr_tail=_tail(err),
            runtime=runtime,
            provenance=provenance,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _tail(text: Optional[str], limit: int = 2000) -> Optional[str]:
    """Last ``limit`` characters of a stream. None when there was none -- an
    empty string would read as "it said nothing", which is a measurement."""
    if not text:
        return None
    text = text.strip()
    return text[-limit:] if text else None


def _from_payload(
    path: str,
    payload: Dict[str, Any],
    *,
    located: Dict[str, Any],
    limits: Dict[str, Any],
    exit_code: int,
    duration_seconds: float,
    stderr_tail: Optional[str],
    runtime: Optional[Dict[str, Any]] = None,
    provenance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Turn the export script's JSON into a report.

    A section the script could not produce arrives as ``null`` and STAYS None
    with ``script_error``; ``[]`` from the script is a MEASURED empty and is
    kept as such. The distinction is carried end to end -- collapsing it here
    would undo the whole reason for carrying it through the script.
    """
    script_error = payload.get("error")
    functions = _as_list(payload.get("functions"))
    imports = _as_str_list(payload.get("imports"))
    strings = _as_str_list(payload.get("strings"))

    entry = payload.get("entry_decompiled")
    if entry is not None and not isinstance(entry, str):
        entry = None
    if isinstance(entry, str) and entry:
        entry_basis = BASIS_PARSED
    elif script_error:
        entry_basis = BASIS_SCRIPT_ERROR
    else:
        # The script ran and reported no entry function. A MEASURED absence of
        # an entry point, not a failure to look for one.
        entry_basis = str(payload.get("entry_basis") or "no_entry_point")

    truncation = {
        "functions": _as_bool(payload.get("functions_truncated")),
        "strings": _as_bool(payload.get("strings_truncated")),
    }
    hit_bound = truncation["functions"] is True or truncation["strings"] is True
    status = STATUS_TRUNCATED if hit_bound else STATUS_OK
    reason: Optional[str] = None
    detail: Optional[str] = None
    if hit_bound:
        which = sorted(k for k, v in truncation.items() if v is True)
        reason = "bound_reached"
        detail = (
            "a declared bound was hit (%s); everything reported is a statement "
            "about a prefix of the program." % ", ".join(which)
        )
    if script_error:
        # Ghidra ran, the script wrote JSON, and the SCRIPT failed. Whatever it
        # did produce is kept -- discarding a real function list because the
        # string extractor raised would throw away measured evidence.
        reason = "script_error"
        detail = str(script_error)
        if functions is None and imports is None and strings is None:
            status = STATUS_ERROR

    return _report(
        path,
        status,
        reason=reason,
        reason_detail=detail,
        basis=BASIS_SCRIPT_ERROR,
        located=located,
        limits=limits,
        functions=functions,
        imports=imports,
        strings=strings,
        entry_decompiled=entry,
        entry_basis=entry_basis,
        truncation=truncation,
        exit_code=exit_code,
        duration_seconds=duration_seconds,
        stderr_tail=stderr_tail,
        runtime=runtime,
        provenance=provenance,
    )


def _as_list(value: Any) -> Optional[List[Dict[str, Any]]]:
    """A list of mappings, or None. A wholly malformed section reads as
    UNMEASURED rather than as a short list."""
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, dict)]


def _as_str_list(value: Any) -> Optional[List[str]]:
    if not isinstance(value, list):
        return None
    return [str(item) for item in value if isinstance(item, (str, int, float))]


def _as_bool(value: Any) -> Optional[bool]:
    """None -- never False -- for anything that is not a real boolean. A False
    here would assert "no bound was hit" on behalf of a script that did not say
    so."""
    return value if isinstance(value, bool) else None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render(report: Dict[str, Any]) -> str:
    lines = [
        "ghidra headless: %s" % report["path"],
        "  status         %s%s"
        % (report["status"], "  (%s)" % report["reason"] if report.get("reason") else ""),
    ]
    if report.get("reason_detail"):
        lines.append("                 %s" % report["reason_detail"])
    lines += [
        "  ghidra         %s" % (report["headless"] or "not found"),
        "  version        %s" % (report["ghidra_version"] or "not determined"),
        "  pyghidra       %s" % (report["runtime"].get("python") or "not located"),
    ]
    prov = report["provenance"]
    if prov.get("target_sha256"):
        lines.append(
            "  target         sha256 %s  authorization %s"
            % (prov["target_sha256"], prov.get("authorization_basis") or "NONE")
        )
    if report["status"] == STATUS_UNAVAILABLE:
        lines.append("  nothing was run; nothing is reported about the artifact.")
        return "\n".join(lines)
    for key in ("functions", "imports", "strings"):
        value = report[key]
        if value is None:
            lines.append("  %-14s not measured (%s)" % (key, report["%s_basis" % key]))
        else:
            mark = "  (capped)" if report["truncation"].get(key) is True else ""
            lines.append("  %-14s %d%s" % (key, len(value), mark))
    entry = report["entry_decompiled"]
    if entry:
        lines.append(
            "  entry          %d chars DECOMPILED (confidence %s; a reconstruction, "
            "not recovered source)" % (len(entry), report["entry_decompiled_confidence"])
        )
    else:
        lines.append("  entry          not decompiled (%s)" % report["entry_decompiled_basis"])
    if report.get("duration_seconds") is not None:
        lines.append(
            "  duration       %ss (budget %ss)"
            % (report["duration_seconds"], report["limits"]["timeout_s"])
        )
    if report.get("kill_method"):
        lines.append("  killed         %s" % report["kill_method"])
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.analyzers.ghidra_headless",
        description="Decompile a compiled artifact with Ghidra headless, when Ghidra is present.",
    )
    parser.add_argument("path", help="Path to the artifact")
    parser.add_argument("--json", action="store_true", help="Machine-readable report")
    parser.add_argument("--timeout", type=int, default=None, help="Override " + ENV_TIMEOUT)
    parser.add_argument("--max-cpu", type=int, default=None, help="Override " + ENV_MAX_CPU)
    parser.add_argument("--ghidra-home", default=None, help="Override " + ENV_GHIDRA_HOME)
    parser.add_argument(
        "--max-functions", type=int, default=None, help="Override " + ENV_MAX_FUNCTIONS
    )
    parser.add_argument("--max-strings", type=int, default=None, help="Override " + ENV_MAX_STRINGS)
    parser.add_argument("--ghidra-python", default=None, help="Override " + ENV_PYTHON)
    parser.add_argument(
        "--authorization-basis",
        choices=AUTHORIZATION_BASES,
        default=None,
        help="Why this target may be opened (overrides %s); required to run" % ENV_AUTHORIZATION,
    )
    args = parser.parse_args(argv)

    try:
        report = decompile(
            args.path,
            timeout_s=args.timeout,
            max_cpu=args.max_cpu,
            ghidra_home=args.ghidra_home,
            max_functions=args.max_functions,
            max_strings=args.max_strings,
            ghidra_python=args.ghidra_python,
            authorization_basis=args.authorization_basis,
        )
    except Exception as exc:  # a report could not be produced at all
        print(
            "ghidra headless could not run: %s: %s" % (type(exc).__name__, exc),
            file=sys.stderr,
        )
        return 2

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(_render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
