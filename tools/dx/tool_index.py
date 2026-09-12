#!/usr/bin/env python3
# CUI // SP-CTI
"""External tool index -- one declared list of the binaries ICDEV shells to.

WHY (xrv-route-01). ``shutil.which`` is called ad hoc in 30+ modules
(``agents/adapters/*``, ``airgap/detector``, ``browser/driver_manager``,
``compliance/oscal_tools``, ``genesis/launch``, ...) with no central index, so
nothing could answer "which of the external binaries this platform invokes are
actually on this PATH". The two things that look like they answer it do not:
``tools/testing/health_check.py::check_tools`` probes Python IMPORTS, and
``tools/dx/tool_detector.py`` detects AI coding tools from config files.

THREE STATES, AND ``unmeasurable`` IS NEVER FOLDED INTO EITHER OTHER:

    present       the binary resolved from PATH AND answered its version_cmd
    absent        PATH holds no such binary
    unmeasurable  the binary IS on PATH and did not answer -- it timed out,
                  exited non-zero, or printed something the declared regex
                  could not read

That middle line is the whole point of requiring ``version_cmd`` for every
entry: a ``present`` verdict that never ran the binary is a claim about a file
NAME, not about a working tool. Measured live on this host 2026-09-11, TWO
binaries called ``helm`` are on PATH -- a real Kubernetes ``helm.EXE`` from
WinGet, and an extension-less Python console script of an unrelated package
that dies in ``ModuleNotFoundError: No module named 'glib'``. Git Bash's
``command -v`` returns the broken one; ``shutil.which`` returns the working
one, because PATHEXT is consulted first. A name-only probe would have reported
whichever it happened to find and called both ``present``. Running the binary
is what settles it, and on this host the verdict is ``present 4.2.3``.

Nothing here is measured by reading a PATH entry. Absence of a version answer
from a binary that IS on PATH is reported as ``unmeasurable`` with the reason
(exit code, timeout, or output the declared regex could not read), never as
``absent`` -- those two send a reader to different repairs: install it, versus
the one you installed is broken.

REPORT ONLY. Nothing here gates, refuses or installs. An absent optional tool
is a capability this deployment does not have, not a fault.

ARGV[0] IS THE RESOLVED PATH, NEVER THE BARE NAME, and this is measured rather
than assumed. On Windows ``shutil.which("npx")`` returns ``npx.CMD``;
``subprocess.run([resolved, "--version"])`` succeeds and
``subprocess.run(["npx", "--version"])`` raises ``FileNotFoundError``
(WinError 2). The naive spelling is the one that fails.

Usage:
    python -m tools.dx.tool_index --refresh --json
    python -m tools.dx.tool_index --refresh
    python -m tools.dx.tool_index --name git --json
    python -m tools.dx.tool_index --validate
    python -m tools.dx.tool_index --call-sites git

The ONE lookup new code calls::

    from icdev.tools.dx.tool_index import which
    exe = which("gh")          # Path | None -- never a second shutil.which

Exit codes: 0 a report was produced (whatever it says); 1 ``--validate`` found
a broken DECLARATION; 2 a report could not be produced at all, which is never
the same as a clean report.
"""
from __future__ import annotations

import argparse
import ast
import json
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

try:
    from icdev.core.paths import repo_root as _repo_root
except Exception:  # pragma: no cover -- installed kernel absent
    def _repo_root(start: str) -> Path:
        return Path(start).resolve().parents[2]

BASE_DIR: Path = Path(_repo_root(__file__))
INDEX_PATH: Path = BASE_DIR / "args" / "tool_index.yaml"

STATUS_PRESENT = "present"
STATUS_ABSENT = "absent"
STATUS_UNMEASURABLE = "unmeasurable"

#: Why a binary that IS on PATH could not be measured. Each sends a reader to a
#: different repair, so they are never merged into one "broken".
REASON_EXIT = "version_cmd_exit_nonzero"
REASON_UNPARSED = "version_output_unparsed"
REASON_TIMEOUT = "version_cmd_timeout"
REASON_ERROR = "version_cmd_error"

_DEFAULT_REGEX = r"(\d+(?:\.\d+)+)"
_DEFAULT_TIMEOUT = 20

_PLATFORM_KEYS = {"Windows": "windows", "Linux": "linux", "Darwin": "darwin"}

_index_cache: Optional[Dict[str, Any]] = None


class ToolIndexError(RuntimeError):
    """The declaration itself could not be read."""


# ---------------------------------------------------------------------------
# Declaration
# ---------------------------------------------------------------------------


def load_index(path: Optional[Path] = None, *, refresh: bool = False) -> Dict[str, Any]:
    """Load args/tool_index.yaml. Raises ToolIndexError when it cannot be read."""
    global _index_cache
    if path is None and _index_cache is not None and not refresh:
        return _index_cache
    target = Path(path) if path is not None else INDEX_PATH
    if not target.exists():
        raise ToolIndexError(f"tool index not found: {target}")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover -- pyyaml is a hard dep
        raise ToolIndexError(f"pyyaml unavailable: {exc}") from exc
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ToolIndexError(f"tool index unreadable ({target}): {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("tools"), list):
        raise ToolIndexError(f"tool index malformed ({target}): no `tools:` list")
    if path is None:
        _index_cache = data
    return data


def entries(index: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    return list((index or load_index()).get("tools") or [])


def entry_for(name: str, index: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return the declared entry, or raise KeyError naming the file to edit."""
    idx = index or load_index()
    for item in entries(idx):
        if item.get("name") == name:
            return item
    excluded = {e.get("name"): e.get("reason") for e in (idx.get("excluded") or [])}
    if name in excluded:
        raise KeyError(
            f"{name!r} is DECLARED ABSENT from the tool index, on purpose: "
            f"{(excluded[name] or '').strip()}"
        )
    raise KeyError(
        f"{name!r} is not declared in args/tool_index.yaml. Declare the binary "
        f"there (name, binary, version_cmd, used_by) rather than calling "
        f"shutil.which directly."
    )


def binary_for(entry: Dict[str, Any], system: Optional[str] = None) -> str:
    """Resolve the per-OS binary STEM. Only a differing stem needs an override
    -- shutil.which already resolves PATHEXT (.exe/.cmd/.bat) on Windows."""
    declared = entry.get("binary") or entry.get("name")
    if isinstance(declared, dict):
        key = _PLATFORM_KEYS.get(system or platform.system(), "")
        return str(declared.get(key) or declared.get("default") or entry.get("name"))
    return str(declared)


def os_overrides_declared(index: Optional[Dict[str, Any]] = None) -> int:
    """How many entries carry a per-OS stem override. Reported so that a zero
    is MEASURED rather than assumed."""
    return sum(1 for e in entries(index) if isinstance(e.get("binary"), dict))


# ---------------------------------------------------------------------------
# Lookup -- the ONE call new code makes
# ---------------------------------------------------------------------------


def which(name: str, index: Optional[Dict[str, Any]] = None,
          path: Optional[str] = None) -> Optional[Path]:
    """Resolve a DECLARED external binary from PATH.

    Returns the resolved Path, or None when PATH holds no such binary. Raises
    KeyError for an undeclared name -- an index that silently answers for
    anything can never grow, and the whole point is that the list is declared.
    """
    entry = entry_for(name, index)
    found = shutil.which(binary_for(entry), path=path)
    return Path(found) if found else None


# ---------------------------------------------------------------------------
# Version handling
# ---------------------------------------------------------------------------


def _parse_version(text: str) -> Optional[tuple]:
    """"2.55.0.windows.5" -> (2, 55, 0). None when nothing numeric is there."""
    if not text:
        return None
    parts: List[int] = []
    for chunk in str(text).split("."):
        m = re.match(r"\d+", chunk)
        if not m:
            break
        parts.append(int(m.group(0)))
    return tuple(parts) or None


def meets_minimum(version: Optional[str], minimum: Optional[str]) -> Optional[bool]:
    """True/False, or None when the comparison cannot be MADE -- no declared
    minimum, or a version neither side can parse. Never False for unknown."""
    if not minimum or not version:
        return None
    have, want = _parse_version(version), _parse_version(minimum)
    if have is None or want is None:
        return None
    width = max(len(have), len(want))
    have = have + (0,) * (width - len(have))
    want = want + (0,) * (width - len(want))
    return have >= want


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


def probe(name: str, index: Optional[Dict[str, Any]] = None,
          path: Optional[str] = None) -> Dict[str, Any]:
    """Resolve one declared binary and ASK IT its version.

    Returns {name, binary, status, path, version, meets_min, min_version,
    optional, reason, used_by}. ``status`` is present | absent | unmeasurable
    and ``meets_min`` is True | False | None -- None whenever the comparison
    could not be made, never False.
    """
    idx = index or load_index()
    entry = entry_for(name, idx)
    defaults = idx.get("defaults") or {}
    binary = binary_for(entry)
    minimum = entry.get("min_version")
    result: Dict[str, Any] = {
        "name": name,
        "binary": binary,
        "status": STATUS_ABSENT,
        "path": None,
        "version": None,
        "min_version": minimum,
        "meets_min": None,
        "optional": bool(entry.get("optional", True)),
        "reason": None,
        "used_by": list(entry.get("used_by") or []),
    }

    found = shutil.which(binary, path=path)
    if not found:
        return result
    result["path"] = str(Path(found))

    version_cmd = list(entry.get("version_cmd") or [])
    if not version_cmd:
        # The declaration is broken, not the host. --validate says so by name.
        result["status"] = STATUS_UNMEASURABLE
        result["reason"] = REASON_ERROR
        return result

    # argv[0] is the RESOLVED path. The bare name raises WinError 2 for a .CMD
    # shim on Windows -- measured, see the module docstring.
    argv = [str(Path(found))] + [str(a) for a in version_cmd[1:]]
    timeout = int(entry.get("timeout_seconds") or defaults.get("timeout_seconds")
                  or _DEFAULT_TIMEOUT)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        result["status"] = STATUS_UNMEASURABLE
        result["reason"] = REASON_TIMEOUT
        return result
    except OSError:
        result["status"] = STATUS_UNMEASURABLE
        result["reason"] = REASON_ERROR
        return result

    if proc.returncode != 0:
        result["status"] = STATUS_UNMEASURABLE
        result["reason"] = REASON_EXIT
        return result

    # stdout AND stderr: `java -version` writes its banner to stderr.
    blob = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    pattern = entry.get("version_regex") or defaults.get("version_regex") or _DEFAULT_REGEX
    try:
        match = re.search(pattern, blob)
    except re.error:
        match = None
    if not match:
        result["status"] = STATUS_UNMEASURABLE
        result["reason"] = REASON_UNPARSED
        return result

    result["status"] = STATUS_PRESENT
    result["version"] = match.group(1)
    result["meets_min"] = meets_minimum(result["version"], minimum)
    return result


def _rate(part: int, total: int) -> Optional[float]:
    """None -- never 0.0 and never 100.0 -- over an empty denominator."""
    if not total:
        return None
    return round(part / total * 100, 1)


def probe_all(names: Optional[Iterable[str]] = None,
              index: Optional[Dict[str, Any]] = None,
              path: Optional[str] = None) -> Dict[str, Any]:
    """Probe every declared tool (or the named subset) and count the verdicts."""
    idx = index or load_index()
    wanted = list(names) if names else [e.get("name") for e in entries(idx)]
    results = [probe(n, idx, path=path) for n in wanted]

    counts = {
        STATUS_PRESENT: sum(1 for r in results if r["status"] == STATUS_PRESENT),
        STATUS_ABSENT: sum(1 for r in results if r["status"] == STATUS_ABSENT),
        STATUS_UNMEASURABLE: sum(1 for r in results if r["status"] == STATUS_UNMEASURABLE),
    }
    required_absent = [r["name"] for r in results
                       if not r["optional"] and r["status"] == STATUS_ABSENT]
    below_min = [r["name"] for r in results if r["meets_min"] is False]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "index_path": str(INDEX_PATH),
        "platform": platform.system(),
        "declared": len(results),
        "counts": counts,
        # None over an empty denominator; never a fabricated 0.0 or 100.0.
        "present_pct": _rate(counts[STATUS_PRESENT], len(results)),
        "required_absent": required_absent,
        "below_min_version": below_min,
        "os_overrides_declared": os_overrides_declared(idx),
        "excluded": [{"name": e.get("name"), "reason": " ".join(str(e.get("reason", "")).split())}
                     for e in (idx.get("excluded") or [])],
        "tools": results,
    }


# ---------------------------------------------------------------------------
# Declaration validation (of OUR data, not of the host's PATH)
# ---------------------------------------------------------------------------


def validate_index(index: Optional[Dict[str, Any]] = None,
                   root: Optional[Path] = None) -> List[str]:
    """Return a list of problems with the DECLARATION. Empty list = valid.

    This checks our own YAML -- required keys, unique names, and that every
    ``used_by`` module still exists. It says nothing about what is on PATH.
    """
    idx = index or load_index()
    base = Path(root) if root else BASE_DIR
    problems: List[str] = []
    seen: Dict[str, int] = {}

    for i, entry in enumerate(entries(idx)):
        label = entry.get("name") or f"<entry {i}>"
        if not isinstance(entry, dict):
            problems.append(f"{label}: entry is not a mapping")
            continue
        if not entry.get("name"):
            problems.append(f"entry {i}: missing `name`")
        seen[label] = seen.get(label, 0) + 1
        version_cmd = entry.get("version_cmd")
        if not isinstance(version_cmd, list) or not version_cmd:
            problems.append(f"{label}: `version_cmd` is required and must be a non-empty list")
        binary = entry.get("binary")
        if isinstance(binary, dict):
            if not (binary.get("default") or any(binary.get(k) for k in _PLATFORM_KEYS.values())):
                problems.append(f"{label}: per-OS `binary` mapping names no platform")
        elif binary is not None and not isinstance(binary, str):
            problems.append(f"{label}: `binary` must be a string or a per-OS mapping")
        used_by = entry.get("used_by")
        if not isinstance(used_by, list) or not used_by:
            problems.append(f"{label}: `used_by` is required and must be a non-empty list")
        else:
            for module in used_by:
                if not (base / str(module)).exists():
                    problems.append(f"{label}: used_by module does not exist: {module}")
        regex = entry.get("version_regex")
        if regex:
            try:
                re.compile(regex)
            except re.error as exc:
                problems.append(f"{label}: version_regex does not compile: {exc}")

    for name, count in seen.items():
        if count > 1:
            problems.append(f"{name}: declared {count} times")

    for item in (idx.get("excluded") or []):
        if not item.get("name"):
            problems.append("excluded entry: missing `name`")
        if not str(item.get("reason") or "").strip():
            problems.append(f"excluded {item.get('name')!r}: a reason is required")
        if item.get("name") in seen:
            problems.append(f"{item.get('name')!r} is both declared and excluded")

    default_regex = (idx.get("defaults") or {}).get("version_regex")
    if default_regex:
        try:
            re.compile(default_regex)
        except re.error as exc:
            problems.append(f"defaults.version_regex does not compile: {exc}")
    return problems


# ---------------------------------------------------------------------------
# Call-site re-derivation -- `used_by` is principal consumers, not exhaustive
# ---------------------------------------------------------------------------

_SUBPROCESS_CALLS = {"run", "Popen", "check_output", "call", "check_call"}


def call_sites_many(names: Sequence[str], index: Optional[Dict[str, Any]] = None,
                    root: Optional[Path] = None) -> Dict[str, List[str]]:
    """Re-derive, in ONE walk of the tree, which modules invoke each binary.

    Reads source with ``ast`` and never imports it: importing 1,300 modules to
    answer a question about argv would drag in the LLM router and the retrieval
    stack, and would go unmeasurable on exactly the deployment where something
    is broken. Finds ``shutil.which("<binary>")`` and any ``subprocess.*`` call
    whose argv[0] is the literal binary name.

    One walk rather than one per name: the walk is ~16s over this tree, so
    asking three questions separately costs three times as much for no extra
    information.
    """
    idx = index or load_index()
    wanted = {binary_for(entry_for(n, idx)): n for n in names}
    base = Path(root) if root else BASE_DIR
    hits: Dict[str, set] = {n: set() for n in names}

    def _const(node: ast.AST) -> Optional[str]:
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    for py in sorted((base / "tools").rglob("*.py")):
        try:
            source = py.read_text(encoding="utf-8")
        except OSError:
            continue
        # Cheap reject before the parse: a file naming none of the binaries
        # cannot invoke one, and parsing 2,000 modules is the whole cost.
        if not any(b in source for b in wanted):
            continue
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        rel = py.relative_to(base).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            head = None
            if fn == "which":
                head = _const(node.args[0])
            elif fn in _SUBPROCESS_CALLS:
                first = node.args[0]
                head = (_const(first.elts[0]) if isinstance(first, (ast.List, ast.Tuple))
                        and first.elts else _const(first))
            if head in wanted:
                hits[wanted[head]].add(rel)
    return {n: sorted(v) for n, v in hits.items()}


def call_sites(name: str, index: Optional[Dict[str, Any]] = None,
               root: Optional[Path] = None) -> List[str]:
    """Re-derive from the tree every module that resolves or invokes the binary."""
    return call_sites_many([name], index, root)[name]


# ---------------------------------------------------------------------------
# Human rendering / CLI
# ---------------------------------------------------------------------------


def human(report: Dict[str, Any]) -> str:
    counts = report["counts"]
    pct = report.get("present_pct")
    lines = [
        f"External tool index -- {report['declared']} declared "
        f"({report['platform']}, {report['generated_at']})",
        f"  present {counts[STATUS_PRESENT]}   absent {counts[STATUS_ABSENT]}   "
        f"unmeasurable {counts[STATUS_UNMEASURABLE]}   "
        f"present_pct {'not measured' if pct is None else f'{pct}%'}",
        "",
    ]
    for row in report["tools"]:
        mark = {STATUS_PRESENT: "+", STATUS_ABSENT: "-", STATUS_UNMEASURABLE: "?"}[row["status"]]
        detail = row["version"] or ""
        if row["status"] == STATUS_UNMEASURABLE:
            detail = f"on PATH, {row['reason']}"
        elif row["status"] == STATUS_ABSENT:
            detail = "not on PATH" + ("" if row["optional"] else "  [REQUIRED]")
        if row["meets_min"] is False:
            detail += f"  BELOW MIN {row['min_version']}"
        lines.append(f"  {mark} {row['name']:<16} {detail}")
    if report["required_absent"]:
        lines += ["", "REQUIRED AND ABSENT: " + ", ".join(report["required_absent"])]
    if report["below_min_version"]:
        lines += ["BELOW DECLARED MINIMUM: " + ", ".join(report["below_min_version"])]
    lines += ["", f"per-OS stem overrides declared: {report['os_overrides_declared']}"]
    if report["excluded"]:
        lines += ["", "Declared absences (named candidates that are NOT PATH binaries):"]
        lines += [f"  x {e['name']}: {e['reason']}" for e in report["excluded"]]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="External tool index -- probe the declared binaries on PATH.")
    parser.add_argument("--refresh", action="store_true",
                        help="probe every declared tool (default action)")
    parser.add_argument("--name", help="probe one declared tool by name")
    parser.add_argument("--validate", action="store_true",
                        help="validate the DECLARATION; exit 1 on a problem")
    parser.add_argument("--call-sites", metavar="NAME",
                        help="re-derive from the tree which modules invoke this binary")
    parser.add_argument("--index", help="path to an alternative tool_index.yaml")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        idx = load_index(Path(args.index) if args.index else None, refresh=True)
    except ToolIndexError as exc:
        payload = {"error": str(exc), "state": STATUS_UNMEASURABLE}
        print(json.dumps(payload, indent=2) if args.json else f"ERROR: {exc}")
        return 2

    if args.validate:
        problems = validate_index(idx)
        if args.json:
            print(json.dumps({"valid": not problems, "problems": problems,
                              "declared": len(entries(idx))}, indent=2))
        elif problems:
            print(f"INVALID -- {len(problems)} problem(s):")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"VALID -- {len(entries(idx))} declared tools")
        return 1 if problems else 0

    if args.call_sites:
        try:
            sites = call_sites(args.call_sites, idx)
        except KeyError as exc:
            print(json.dumps({"error": str(exc)}, indent=2) if args.json else f"ERROR: {exc}")
            return 2
        declared = list(entry_for(args.call_sites, idx).get("used_by") or [])
        payload = {"name": args.call_sites, "declared_used_by": declared,
                   "derived_call_sites": sites,
                   "declared_not_derived": sorted(set(declared) - set(sites)),
                   "derived_not_declared": sorted(set(sites) - set(declared))}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"{args.call_sites}: {len(sites)} invocation site(s) in the tree")
            for s in sites:
                print(f"  {'*' if s in declared else ' '} {s}")
            print("\n(* = also named in used_by; used_by is principal consumers, "
                  "not exhaustive)")
        return 0

    try:
        report = probe_all([args.name] if args.name else None, idx)
    except KeyError as exc:
        print(json.dumps({"error": str(exc)}, indent=2) if args.json else f"ERROR: {exc}")
        return 2
    print(json.dumps(report, indent=2) if args.json else human(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
