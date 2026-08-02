"""
citation_type_linter.py — ICDEV™ Lint Gate (cxo-trust-02)

Scans Python files under ``tools/`` for ``citation_type="..."`` literals whose
value is not in :data:`tools.provenance.citation_types.CITATION_TYPES`.

Why this gate exists
--------------------
``register_citation()`` validates the vocabulary BEFORE the INSERT and raises
``ValueError``. Every caller swallows that exception, so an unknown value means
the citation is never written — silently, forever, with no error surfacing
anywhere.

Two independent subsystems shipped exactly that bug and neither was noticed:

  * ``tools/cortex/governance.py`` passed ``citation_type="cortex"``. The
    governance gate caught the exception and recorded ``provenance="warn"``.
    Measured 2026-08-02: 0 of 285 registry rows were type ``cortex``, and no
    Cortex operation had ever recorded a clean pass.
  * ``tools/blockchain/asset_ledger.py`` passed ``"asset_token"``. There the
    raise was swallowed by a ``try`` guarded on ``if reg_id:``, so
    ``anchor_status`` stayed ``"skipped"`` — GovChain asset tokenization had
    never anchored to the chain.

Two subsystems shipping the same silent failure makes this a MISSING GATE
rather than two defects. A test that asserts the specific two call sites are
now correct would not have caught either of them before they shipped, and will
not catch the third.

Usage
-----
    python tools/lint/citation_type_linter.py [--path <root>] [--json] [--gate]

Exit codes
----------
    0   No violations found (or --gate not set)
    1   Violations found and --gate was passed (blocks CI)

Exemptions
----------
Lines containing ``# citation-type-ok`` are skipped — for a value constructed
dynamically or validated elsewhere. Tests are exempt by directory: a test may
legitimately assert that a BOGUS type is rejected, and flagging that would
punish exactly the coverage this gate wants to exist.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Exemptions
# ---------------------------------------------------------------------------

#: Directories whose files are never scanned.
EXEMPT_DIRS: tuple[str, ...] = (
    "tests/",
    "/tests/",
    "__pycache__",
    ".tmp/",
    "/migrations/",   # a migration records history; the constant is authoritative
)

#: This file necessarily contains the pattern it searches for — in its docstring
#: and in PATTERN itself. Without this it reports itself, which is the fastest
#: way to get a gate switched off.
EXEMPT_FILES: tuple[str, ...] = ("tools/lint/citation_type_linter.py",)

EXEMPTION_COMMENT = "# citation-type-ok"

#: ``citation_type="value"`` / ``citation_type='value'``. Deliberately does NOT
#: match ``citation_type=variable`` — a dynamic value cannot be checked
#: statically, and flagging it would produce noise the gate would be muted for.
PATTERN = re.compile(r"""citation_type\s*=\s*["']([^"']+)["']""")


def _normalise(path: str) -> str:
    return path.replace("\\", "/")


def _rel(filepath: Path, root: Path) -> str:
    """Repo-relative path, so output is stable across checkouts and worktrees."""
    try:
        return _normalise(str(filepath.relative_to(root)))
    except ValueError:
        return _normalise(str(filepath))


def _is_exempt(filepath: Path, root: Path) -> bool:
    rel = _rel(filepath, root)
    if rel in EXEMPT_FILES:
        return True
    return any(part in f"/{rel}" for part in EXEMPT_DIRS)


def known_types() -> tuple[str, ...]:
    """The authoritative vocabulary.

    Imported rather than duplicated: a linter carrying its own copy of the list
    is a second source of truth that drifts from the first.
    """
    from tools.provenance.citation_types import CITATION_TYPES

    return tuple(CITATION_TYPES)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def scan_file(filepath: Path, valid: tuple[str, ...], root: Path | None = None) -> list[dict]:
    """Return violation dicts for *filepath*."""
    violations: list[dict] = []
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return violations

    for lineno, line in enumerate(source.splitlines(), start=1):
        if EXEMPTION_COMMENT in line:
            continue
        for match in PATTERN.finditer(line):
            value = match.group(1)
            if value not in valid:
                violations.append(
                    {
                        "file": _rel(filepath, root) if root else _normalise(str(filepath)),
                        "line": lineno,
                        "col": match.start() + 1,
                        "citation_type": value,
                        "text": line.strip()[:160],
                    }
                )
    return violations


def scan_tree(root: Path) -> list[dict]:
    """Scan every .py under *root/tools/* for unknown citation types."""
    valid = known_types()
    tools_root = root / "tools"
    if not tools_root.is_dir():
        tools_root = root

    out: list[dict] = []
    for py_file in sorted(tools_root.rglob("*.py")):
        if _is_exempt(py_file, root):
            continue
        out.extend(scan_file(py_file, valid, root))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Lint tools/ for citation_type values missing from CITATION_TYPES."
    )
    p.add_argument("--path", type=Path, default=REPO_ROOT, help="Repository root to scan")
    p.add_argument("--json", action="store_true", help="Emit JSON")
    p.add_argument("--gate", action="store_true", help="Exit 1 when violations exist")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = args.path.resolve()
    violations = scan_tree(root)
    valid = known_types()

    if args.json:
        print(json.dumps(
            {
                "violations": violations,
                "count": len(violations),
                "valid_types": list(valid),
                "clean": not violations,
            },
            indent=2,
        ))
    else:
        if violations:
            print(f"{len(violations)} unknown citation_type value(s):\n")
            for v in violations:
                print(f"  {v['file']}:{v['line']}  citation_type={v['citation_type']!r}")
                print(f"      {v['text']}")
            print(
                "\nregister_citation() raises ValueError on an unknown type and every "
                "caller swallows it, so the citation is never written and nothing "
                "reports the failure.\n"
                "Fix: add the value to tools/provenance/citation_types.py::CITATION_TYPES "
                "and ship a migration rendered from check_constraint_sql() — or correct "
                "the typo.\n"
                f"Valid: {', '.join(valid)}"
            )
        else:
            print(f"citation_type linter: clean ({len(valid)} known types)")

    return 1 if (violations and args.gate) else 0


if __name__ == "__main__":
    raise SystemExit(main())
