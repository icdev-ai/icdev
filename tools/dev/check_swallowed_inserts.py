#!/usr/bin/env python3
# [TEMPLATE: CUI // SP-CTI]
"""Standalone CLI gate for swallowed INSERTs (swp-swallow-01-d1).

Finds every ``except Exception: pass`` block guarding a ``try`` that runs an
``INSERT``, under ``tools/`` and excluding ``migrations/``. Prints one
``file:line`` per violation and exits 1; exits 0 when the tree is clean.

Why this is a wrapper and not a second scanner
----------------------------------------------
The AST detection rules already live in
:mod:`tools.refactor.swallowed_persistence`, which is imported by *both* the
codemod (``tools/refactor/fix_swallowed_persistence.py``) and the coherence
gate (``coherence_checker.check_swallowed_persistence``). Re-implementing the
walk here would create a third, drifting definition of "violation" — the exact
failure the shared detector was written to prevent. This module owns only the
command-line contract: argument parsing, ``file:line`` rendering, exit codes.

Relationship to the coherence gate
----------------------------------
``coherence_checker.py --check swallowed_persistence`` is the in-pipeline gate
and scans ``tools/`` plus the ``icdev/tools/`` mirror. This script is the
standalone equivalent for a shell, a pre-commit hook, or an air-gapped CI stage
that cannot load the whole coherence harness. Same detector, same verdict.

Usage
-----
    python tools/dev/check_swallowed_inserts.py                  # scan tools/
    python tools/dev/check_swallowed_inserts.py --path tools/govcon
    python tools/dev/check_swallowed_inserts.py --json

Exit codes: 0 clean, 1 violations found, 2 bad invocation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Own checkout wins over any ambient PYTHONPATH: running this by path from a
# worktree must scan *that* worktree's detector, not a shared checkout's.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.refactor.swallowed_persistence import SwallowSite, find_sites  # noqa: E402

#: Scanned when the caller names no path. ``migrations/`` (and ``tests/``,
#: ``__pycache__``, ``.tmp``, ``node_modules``) are dropped by the shared
#: detector itself, so this stays a plain directory.
DEFAULT_PATHS: Sequence[str] = ("tools",)

EXIT_CLEAN = 0
EXIT_VIOLATIONS = 1
EXIT_USAGE = 2


def format_site(site: SwallowSite) -> str:
    """Render one violation as ``file:line: <what is wrong>``.

    The line reported is the ``except`` clause, not the ``try`` — that is the
    line an author has to edit to fix it.
    """
    where = site.func_name or "<module>"
    table = site.table or "<unknown table>"
    return (
        f"{site.rel}:{site.handler_lineno}: "
        f"`except Exception: pass` swallows INSERT INTO {table} "
        f"(in {where})"
    )


def scan(paths: Sequence[Path], root: Path) -> List[SwallowSite]:
    """Return every swallowed-INSERT site under ``paths``, sorted for stable output."""
    sites = find_sites(list(paths), root)
    return sorted(sites, key=lambda s: (s.rel, s.handler_lineno))


def _resolve_paths(raw: Sequence[str], root: Path) -> List[Path]:
    """Turn CLI path arguments into absolute paths, rejecting ones that don't exist."""
    resolved: List[Path] = []
    for item in raw:
        candidate = Path(item)
        if not candidate.is_absolute():
            candidate = root / candidate
        if not candidate.exists():
            raise FileNotFoundError(item)
        resolved.append(candidate)
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_swallowed_inserts",
        description=(
            "Fail when an `except Exception: pass` block guards an INSERT. "
            "Best-effort persistence is fine; silent best-effort persistence "
            "is a write that can fail forever with nothing to show for it."
        ),
    )
    parser.add_argument(
        "--path",
        action="append",
        dest="paths",
        metavar="PATH",
        help=(
            "File or directory to scan, relative to the repo root. Repeatable. "
            f"Default: {', '.join(DEFAULT_PATHS)}"
        ),
    )
    parser.add_argument(
        "--root",
        default=str(PROJECT_ROOT),
        help="Repo root used to compute reported relative paths (default: this checkout).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit a machine-readable report instead of text.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()

    try:
        targets = _resolve_paths(args.paths or list(DEFAULT_PATHS), root)
    except FileNotFoundError as exc:
        print(f"error: no such path: {exc}", file=sys.stderr)
        return EXIT_USAGE

    sites = scan(targets, root)
    violations = [format_site(site) for site in sites]

    if args.as_json:
        print(
            json.dumps(
                {
                    "clean": not sites,
                    "count": len(sites),
                    "scanned": [p.as_posix() for p in targets],
                    "violations": [
                        {
                            "file": site.rel,
                            "line": site.handler_lineno,
                            "try_line": site.try_lineno,
                            "function": site.func_name,
                            "table": site.table,
                        }
                        for site in sites
                    ],
                },
                indent=2,
            )
        )
    else:
        for line in violations:
            print(line)
        if sites:
            print(
                f"\n{len(sites)} swallowed INSERT site(s). Keep the best-effort "
                "behaviour and add a logger.warning (see "
                "tools/canvas/event_bus.py::_audit_event), or narrow the handler.",
                file=sys.stderr,
            )
        else:
            print("clean: no `except Exception: pass` guarding an INSERT")

    return EXIT_VIOLATIONS if sites else EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
