#!/usr/bin/env python3
# CUI // SP-CTI
"""Which declared extension points can actually fire? (hcx-live-03)

``ExtensionPoint`` declares ten hook points. Declaring one costs a line;
*consuming* one costs a dispatcher on a real code path and a handler registered
against it. Nothing measured the gap, so the gap grew: as of 2026-08-16 six of
the ten had never had either, and TOOL_EXECUTE_BEFORE — the one the behavioral
tier exists for — got its first production dispatcher in hcx-live-01, years
after the enum member was written. That is ICDEV's signature defect (CLAUDE.md,
"a declared capability that is never consumed") wearing the extension seam's
clothes.

**What this measures, and what it cannot.** Two independent pieces of evidence
per point:

* **Dispatchers** — static. A file is a dispatcher for point ``P`` when it both
  names ``P`` (as ``ExtensionPoint.P`` or ``ExtensionPoint("p")``) and calls
  ``dispatch``/``dispatch_async``. This is the load-bearing half: a point with
  no dispatcher **cannot fire**, no matter how many handlers register against
  it, and no amount of runtime telemetry would show otherwise.
* **Handlers** — static (``EXTENSION_HOOKS`` keys and ``register(...)`` calls)
  *and* live (:meth:`ExtensionManager.handler_count`, which sees drop-ins this
  checkout does not contain).

Neither is a count of *dispatches*. Runtime dispatch counting is hcx-live-02's
job and belongs inside ``ExtensionManager.dispatch``; this module deliberately
does not touch it. A point reported ``live`` here is wired, not necessarily
exercised.

The scan is honest about its blind spot: ``chat_manager._dispatch_hook`` calls
``ExtensionPoint(hook_name)`` on a *variable*, so no static scan can attribute
it to a point. Those files are reported under ``dynamic_dispatch_sites`` rather
than being silently dropped or optimistically credited to every point.

Usage::

    python tools/extensions/liveness.py                 # human report
    python tools/extensions/liveness.py --json
    python tools/extensions/liveness.py --dead          # only the dead points
    python tools/extensions/liveness.py --gate          # exit 1 on an unlisted dead point
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

def _repo_root() -> Path:
    """The checkout root — the ancestor holding BOTH ``tools/`` and ``icdev/``.

    Not ``parent.parent.parent``: this module is mirrored into ``icdev/tools/``
    (the packaged copy), where three parents up is ``<repo>/icdev`` and the scan
    would silently cover only the mirror — reporting every point in ``tools/``
    dead. ``icdev/`` never contains an ``icdev/``, so the marker is unambiguous
    from either copy. Not ``os.getcwd()`` either: the checker runs from
    worktrees and from CI subdirectories.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "tools").is_dir() and (parent / "icdev").is_dir():
            return parent
    return here.parent.parent.parent


REPO_ROOT = _repo_root()
CONFIG_PATH = REPO_ROOT / "args" / "extension_liveness.yaml"

#: Directories never walked. ``icdev/`` IS walked — it is the packaged mirror of
#: ``tools/`` and a dispatcher that exists in only one of the two copies is a
#: finding, not a duplicate.
_SKIP_DIRS = frozenset(
    {
        ".git", ".tmp", ".venv", "venv", "node_modules", "__pycache__",
        ".pytest_cache", ".ruff_cache", "build", "dist", "playwright",
    }
)

#: Roots whose dispatchers do NOT count as production wiring. A test that
#: dispatches a point proves the plumbing works, not that anything uses it —
#: crediting it would let a point be kept alive by its own test.
_NON_PRODUCTION_ROOTS = ("tests", "features")

#: The methods that actually fire a point.
_DISPATCH_CALLS = frozenset({"dispatch", "dispatch_async"})

#: Status values, in descending order of health.
LIVE = "live"
DISPATCHER_ONLY = "dispatcher_only"
HANDLERS_ONLY = "handlers_only"
DEAD = "dead"


# ---------------------------------------------------------------------------
# Declared points
# ---------------------------------------------------------------------------
def declared_points() -> "list[str]":
    """The declared hook-point values, in declaration order.

    Read off the live enum rather than a hardcoded list so a point added to
    ``ExtensionPoint`` is measured from its first commit instead of from
    whenever somebody remembers to update this file.
    """
    from tools.extensions.extension_manager import ExtensionPoint

    return [p.value for p in ExtensionPoint]


def _member_to_value() -> "dict[str, str]":
    """``{"AGENT_START": "agent_start", ...}`` for resolving attribute access."""
    from tools.extensions.extension_manager import ExtensionPoint

    return {p.name: p.value for p in ExtensionPoint}


# ---------------------------------------------------------------------------
# Static scan
# ---------------------------------------------------------------------------
@dataclass
class _FileScan:
    """What one Python file says about extension points."""

    named: set = field(default_factory=set)       # point values named literally
    registered: set = field(default_factory=set)  # point values a handler declares
    dispatches: bool = False                      # calls dispatch/dispatch_async
    dynamic: bool = False                         # ExtensionPoint(<non-literal>)
    declares_enum: bool = False                   # this file defines ExtensionPoint


def _iter_python_files(root: Path) -> "Iterable[Path]":
    # Skip-dir matching is done on the path RELATIVE to the root. Matching on
    # absolute parts would make the whole scan depend on where the checkout
    # happens to live — a worktree under ``.tmp/worktrees/`` would skip every
    # file in the repository and report all ten points dead.
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def _mentions_a_point(source: str, values: "set[str]") -> bool:
    """Cheap substring pre-filter, applied before the expensive ``ast.parse``.

    Sound because a dispatcher must name its point *somehow*: as
    ``ExtensionPoint.X``, as the bare string value, or — when the point arrives
    in a variable, as in ``chat_manager._dispatch_hook`` — by importing
    ``ExtensionPoint`` to resolve it. A handler declaration names it as an
    ``EXTENSION_HOOKS`` key or a ``register`` argument. A file matching none of
    these cannot contribute a site, and parsing every ``.py`` in the tree
    instead costs ~25s per report.
    """
    if "ExtensionPoint" in source or "EXTENSION_HOOKS" in source:
        return True
    return any(value in source for value in values)


def _scan_file(path: Path, members: "dict[str, str]", values: "set[str]") -> _FileScan:
    """Parse one file. A syntax error yields an empty scan, never an exception."""
    scan = _FileScan()
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return scan
    if not _mentions_a_point(source, values):
        return scan
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return scan

    for node in ast.walk(tree):
        # The module that DEFINES ExtensionPoint names every point by
        # construction. A declaration is not a consumption, so it is never
        # credited as a dispatcher.
        if isinstance(node, ast.ClassDef) and node.name == "ExtensionPoint":
            scan.declares_enum = True
            continue

        # ExtensionPoint.AGENT_START
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "ExtensionPoint" and node.attr in members:
                scan.named.add(members[node.attr])
            continue

        # A bare "agent_start" literal. ``chat_manager._dispatch_hook`` takes the
        # point as a *string* and resolves ``ExtensionPoint(hook_name)`` on a
        # variable, so its four call sites are invisible to attribute matching.
        # Only credited when the file dispatches at all, and matched by exact
        # equality — prose mentioning a point in a docstring does not match.
        if isinstance(node, ast.Constant) and node.value in values:
            scan.named.add(node.value)
            continue

        if isinstance(node, ast.Call):
            func = node.func
            # ExtensionPoint("agent_start") / ExtensionPoint(hook_name)
            if isinstance(func, ast.Name) and func.id == "ExtensionPoint":
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant) and arg.value in values:
                    scan.named.add(arg.value)
                else:
                    scan.dynamic = True
            # manager.dispatch(...) / self.dispatch_async(...)
            if isinstance(func, ast.Attribute) and func.attr in _DISPATCH_CALLS:
                scan.dispatches = True
            # register(hook_point=ExtensionPoint.X, ...) — a handler declaration
            if isinstance(func, ast.Attribute) and func.attr == "register":
                for value in list(node.args) + [kw.value for kw in node.keywords]:
                    if (
                        isinstance(value, ast.Attribute)
                        and isinstance(value.value, ast.Name)
                        and value.value.id == "ExtensionPoint"
                        and value.attr in members
                    ):
                        scan.registered.add(members[value.attr])
            continue

        # EXTENSION_HOOKS = {"chat_message_after": {...}} — the builtins' form
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "EXTENSION_HOOKS" in targets and isinstance(node.value, ast.Dict):
                for key in node.value.keys:
                    if isinstance(key, ast.Constant) and key.value in values:
                        scan.registered.add(key.value)

    return scan


def _is_production(rel: str) -> bool:
    head = rel.split("/", 1)[0]
    return head not in _NON_PRODUCTION_ROOTS


#: Process-local memo of the static scan, keyed by root. The tree does not
#: change under a running process, and a full walk is seconds. Deliberately
#: memoises only the STATIC half — :func:`live_handler_counts` reads the live
#: singleton on every report, so a handler registered mid-process is seen.
_scan_cache: "dict[str, dict]" = {}


def scan_tree(root: Optional[Path] = None) -> dict:
    """Walk ``root`` and attribute every point reference to a file.

    Returns ``{"dispatchers": {point: [...]}, "handler_sites": {point: [...]},
    "test_dispatchers": {point: [...]}, "dynamic": [...]}``.
    """
    root = root or REPO_ROOT
    cached = _scan_cache.get(str(root))
    if cached is not None:
        return cached
    members = _member_to_value()
    values = set(members.values())

    dispatchers: dict[str, list[str]] = {v: [] for v in values}
    test_dispatchers: dict[str, list[str]] = {v: [] for v in values}
    handler_sites: dict[str, list[str]] = {v: [] for v in values}
    dynamic: list[str] = []

    for path in _iter_python_files(root):
        rel = path.relative_to(root).as_posix()
        scan = _scan_file(path, members, values)
        if not (scan.named or scan.registered or scan.dynamic):
            continue
        production = _is_production(rel)
        fires = scan.dispatches and not scan.declares_enum
        if scan.dynamic and fires and production:
            dynamic.append(rel)
        for value in sorted(scan.named):
            if not fires:
                continue
            (dispatchers if production else test_dispatchers)[value].append(rel)
        for value in sorted(scan.registered):
            handler_sites[value].append(rel)

    result = {
        "dispatchers": dispatchers,
        "handler_sites": handler_sites,
        "test_dispatchers": test_dispatchers,
        "dynamic": sorted(dynamic),
    }
    _scan_cache[str(root)] = result
    return result


# ---------------------------------------------------------------------------
# Runtime evidence
# ---------------------------------------------------------------------------
def live_handler_counts() -> "dict[str, int]":
    """Handlers registered with the live singleton, per point.

    This is the half a static scan cannot do: ``scan_directories`` in
    ``args/extension_config.yaml`` includes a project-root ``extensions/`` tree
    that is not in this repository, and a tenant or site-local drop-in is
    invisible to any grep of this checkout. A point with zero static handlers
    but a live one is wired by something outside the tree.
    """
    from tools.extensions.extension_manager import ExtensionPoint, extension_manager

    return {p.value: extension_manager.handler_count(p) for p in ExtensionPoint}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _status(dispatchers: int, handlers: int) -> str:
    if dispatchers and handlers:
        return LIVE
    if dispatchers:
        return DISPATCHER_ONLY
    if handlers:
        return HANDLERS_ONLY
    return DEAD


def build_report(root: Optional[Path] = None) -> dict:
    """Full liveness report over every declared point."""
    scan = scan_tree(root)
    live = live_handler_counts()
    points = []
    for value in declared_points():
        dispatchers = scan["dispatchers"][value]
        handler_sites = scan["handler_sites"][value]
        handlers = len(handler_sites) + live.get(value, 0)
        points.append(
            {
                "point": value,
                "status": _status(len(dispatchers), handlers),
                "dispatchers": dispatchers,
                "dispatcher_count": len(dispatchers),
                "handler_sites": handler_sites,
                "registered_handlers": live.get(value, 0),
                "test_only_dispatchers": scan["test_dispatchers"][value],
            }
        )
    dead = [p["point"] for p in points if p["status"] == DEAD]
    return {
        "classification": "CUI // SP-CTI",
        "root": str(root or REPO_ROOT),
        "declared": len(points),
        "points": points,
        "dead": dead,
        "dead_count": len(dead),
        "dynamic_dispatch_sites": scan["dynamic"],
    }


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------
def _load_known_dead() -> dict:
    """``{point: {"reason": ..., "follow_up": ...}}`` from the census file."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — a missing/broken census is "nothing known"
        return {}
    known = data.get("known_dead") or {}
    return known if isinstance(known, dict) else {}


def evaluate_gate(report: dict) -> dict:
    """Compare the measured dead set against the enumerated one.

    ``unlisted`` is a point that is dead and was not written down — the
    regression this gate exists to catch. ``resolved`` is a listed point that
    has since been wired; it is reported so the entry can be removed, and it
    never fails the gate.
    """
    known = _load_known_dead()
    dead = set(report["dead"])
    listed = set(known)
    unlisted = sorted(dead - listed)
    return {
        "ok": not unlisted,
        "dead": sorted(dead),
        "unlisted": unlisted,
        "resolved": sorted(listed - dead),
        "known_dead": {
            k: {
                "reason": (known[k] or {}).get("reason", ""),
                "follow_up": (known[k] or {}).get("follow_up", ""),
            }
            for k in sorted(listed & dead)
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _render(report: dict, gate: Optional[dict]) -> str:
    lines = ["Extension point liveness (hcx-live-03)", ""]
    width = max(len(p["point"]) for p in report["points"])
    for p in report["points"]:
        lines.append(
            f"  {p['point']:<{width}}  {p['status']:<16} "
            f"dispatchers={p['dispatcher_count']} "
            f"handlers={len(p['handler_sites']) + p['registered_handlers']}"
        )
        for site in p["dispatchers"]:
            lines.append(f"      dispatched by {site}")
    lines.append("")
    lines.append(f"  {report['dead_count']} of {report['declared']} declared points are DEAD")
    for value in report["dead"]:
        lines.append(f"      {value}: no dispatcher and no handler — it cannot fire")
    if report["dynamic_dispatch_sites"]:
        lines.append("")
        lines.append("  Dispatch by computed point (not attributable to a point):")
        for site in report["dynamic_dispatch_sites"]:
            lines.append(f"      {site}")
    if gate is not None:
        lines.append("")
        if gate["unlisted"]:
            lines.append(
                "  GATE FAIL — dead and not enumerated in "
                f"args/extension_liveness.yaml: {', '.join(gate['unlisted'])}"
            )
        else:
            lines.append("  GATE OK — every dead point is enumerated with a reason")
        for value in gate["resolved"]:
            lines.append(f"  RESOLVED — {value} is now wired; drop it from the census")
    return "\n".join(lines)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--dead", action="store_true", help="only the dead points")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="exit 1 if a dead point is not enumerated in args/extension_liveness.yaml",
    )
    parser.add_argument("--root", default=None, help="tree to scan (default: repo root)")
    args = parser.parse_args(argv)

    report = build_report(Path(args.root).resolve() if args.root else None)
    gate = evaluate_gate(report) if args.gate else None

    if args.dead:
        payload: dict[str, Any] = {
            "classification": report["classification"],
            "dead": report["dead"],
            "dead_count": report["dead_count"],
            "points": [p for p in report["points"] if p["status"] == DEAD],
        }
    else:
        payload = report
    if gate is not None:
        payload["gate"] = gate

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(_render(report, gate))

    return 1 if (gate is not None and not gate["ok"]) else 0


if __name__ == "__main__":
    sys.exit(main())
