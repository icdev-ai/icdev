#!/usr/bin/env python3
# [TEMPLATE: CUI // SP-CTI]
"""Fail if any ``except Exception: pass`` guards an INSERT in ``tools/``.

Standalone counterpart to the coherence gate
(``coherence_checker.check_swallowed_persistence``). The gate walks the whole
repository (7,245 files, ~93s); this defaults to ``tools/`` (~71s) and takes
``--path`` to scope a single subtree — ``--path tools/govcon`` is ~1s, which is
what makes it usable as an inner-loop check while editing one subsystem.

The gate remains the thing that blocks a build. This is the developer-facing
front end to the same detector, with ``file:line`` output and a plain exit code.

It deliberately owns **no** detection logic of its own. Both the fixer, the
coherence gate and this script import :func:`find_sites` from
:mod:`tools.refactor.swallowed_persistence`, so the three can never disagree
about what counts as a violation. Adding a second definition of the pattern
here is the one change that would defeat the purpose of the check.

Exit codes:
    0 — clean
    1 — violations found (each printed as ``file:line``)
    2 — the check could not run (detector missing, or a ``--path`` that does
        not exist)

Exit 2 is distinct on purpose: a check that reports "clean" because its
detector vanished — or because the path it was pointed at was a typo — is the
exact silent-failure mode this card exists to close. Both are "we did not
look", which must never be reported as "we looked and it was fine".

Usage::

    python tools/dev/check_swallowed_inserts.py
    python tools/dev/check_swallowed_inserts.py --json
    python tools/dev/check_swallowed_inserts.py --path tools/govcon
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Sequence

#: Repo root resolved from this file, never from ``os.getcwd()`` — the CLI is
#: routinely run from a git worktree or a subdirectory (see CLAUDE.md).
REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EXIT_CLEAN = 0
EXIT_VIOLATIONS = 1
#: Also covers a ``--path`` that does not exist: see the module docstring on why
#: "we did not look" must not share an exit code with "we looked and it was fine".
EXIT_NO_DETECTOR = 2
EXIT_CANNOT_RUN = EXIT_NO_DETECTOR


def _resolve_paths(raw: Sequence[str]) -> List[Path]:
    """Turn CLI ``--path`` values into absolute paths, defaulting to ``tools/``."""
    if not raw:
        return [REPO_ROOT / "tools"]
    resolved = []
    for item in raw:
        candidate = Path(item)
        resolved.append(candidate if candidate.is_absolute() else REPO_ROOT / candidate)
    return resolved


def _rel(path: Path) -> str:
    """Repo-relative POSIX path, falling back to the absolute path when outside."""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def scan(paths: Sequence[Path]) -> List[dict]:
    """Return one dict per swallowed-INSERT site, sorted by file then line.

    :raises ImportError: if the shared detector is unavailable.
    :raises FileNotFoundError: if any requested path does not exist. Skipping it
        would report "clean" for a subtree that was never opened.
    """
    from tools.refactor.swallowed_persistence import find_sites

    missing = [p for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(", ".join(_rel(p) for p in missing))

    sites = find_sites(list(paths), REPO_ROOT)
    findings = [
        {
            "file": site.rel,
            "line": site.handler_lineno,
            "pass_line": site.pass_lineno,
            "function": site.func_name,
            "table": site.table,
        }
        for site in sites
    ]
    findings.sort(key=lambda f: (f["file"], f["line"]))
    return findings


def format_text(findings: Sequence[dict], scanned: Sequence[Path]) -> str:
    """Render findings as ``file:line`` lines plus a one-line summary."""
    lines = [
        "{}:{}: except Exception: pass guards INSERT INTO {}{}".format(
            f["file"],
            f["line"],
            f["table"] or "?",
            " (in {})".format(f["function"]) if f["function"] else "",
        )
        for f in findings
    ]
    where = ", ".join(p.name for p in scanned) or "tools"
    if findings:
        lines.append("")
        lines.append(
            "FAIL: {} swallowed INSERT site(s) in {}. "
            "Log the exception instead of passing; "
            "`python tools/refactor/fix_swallowed_persistence.py --write` applies the rewrite.".format(
                len(findings), where
            )
        )
    else:
        lines.append("OK: no swallowed INSERT sites in {}.".format(where))
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail if an `except Exception: pass` guards an INSERT in tools/.",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        metavar="PATH",
        help="File or directory to scan, relative to the repo root (repeatable). Default: tools/",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    paths = _resolve_paths(args.path)

    try:
        findings = scan(paths)
    except ImportError as exc:
        message = (
            "cannot import tools.refactor.swallowed_persistence — the detector "
            "this check depends on is missing: {}".format(exc)
        )
        if args.json:
            print(json.dumps({"status": "error", "error": message, "violations": []}, indent=2))
        else:
            print("ERROR: {}".format(message), file=sys.stderr)
        return EXIT_NO_DETECTOR
    except FileNotFoundError as exc:
        message = "no such path: {} — nothing was scanned".format(exc)
        if args.json:
            print(json.dumps({"status": "error", "error": message, "violations": []}, indent=2))
        else:
            print("ERROR: {}".format(message), file=sys.stderr)
        return EXIT_CANNOT_RUN

    if args.json:
        print(
            json.dumps(
                {
                    "status": "fail" if findings else "pass",
                    "count": len(findings),
                    "scanned": [_rel(p) for p in paths],
                    "violations": findings,
                },
                indent=2,
            )
        )
    else:
        print(format_text(findings, paths))

    return EXIT_VIOLATIONS if findings else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
