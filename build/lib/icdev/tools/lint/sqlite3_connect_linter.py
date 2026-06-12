"""
sqlite3_connect_linter.py — ICDEV™ Lint Gate

Scans Python files under tools/ for direct ``sqlite3.connect()`` calls that
bypass the dual-backend storage abstraction (``get_connection()`` in
tools/db/storage.py).  Production deployments use PostgreSQL; a raw
``sqlite3.connect()`` call would silently fall back to a local file and go
undetected in CI unless this linter blocks it.

Usage
-----
    python tools/lint/sqlite3_connect_linter.py [--path <root>] [--json] [--gate]

Exit codes
----------
    0   No violations found (or --gate not set)
    1   Violations found and --gate flag was passed (blocks CI)

Exemptions
----------
Lines are skipped when they contain the inline comment ``# sqlite3-ok``.
Whole files are exempt if they appear in EXEMPT_SUFFIXES / EXEMPT_NAMES
(see constants below).  Child-app database initialisation scripts named
``init_db.py`` are also exempt.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Exemption lists
# ---------------------------------------------------------------------------

EXEMPT_SUFFIXES: tuple[str, ...] = (
    # Storage infrastructure — legitimately need raw sqlite3
    "tools/db/storage.py",
    "tools/db/init_icdev_db.py",
    "tools/db/migration_runner.py",
    "tools/db/backup_manager.py",
    "tools/db/pg_init.py",                       # reads SQLite to migrate data into PostgreSQL
    "tools/db/migrate_add_missing_columns.py",   # DDL migration — runs before storage abstraction
    "tools/db/migrate_to_storage.py",            # mentions the pattern in docstrings/comments
    "tools/db/migrations/018_memory_db_consolidation/up.py",  # one-time migration script
    # Tooling files — mention the pattern in strings/docstrings, not as real calls
    "tools/lint/sqlite3_connect_linter.py",      # the linter itself
    # SaaS platform — intentional SQLite for per-tenant isolation
    "tools/saas/platform_db.py",
    "tools/saas/db/db_compat.py",
)

EXEMPT_NAMES: tuple[str, ...] = (
    "init_db.py",  # child-app per-module DB initialisation scripts
)

# Cross-platform: match forward-slash and back-slash forms
def _normalise(path: str) -> str:
    return path.replace("\\", "/")


def _is_exempt(filepath: Path, root: Path) -> bool:
    """Return True if *filepath* is in the exemption list."""
    # Match by filename
    if filepath.name in EXEMPT_NAMES:
        return True
    # Match by suffix (handles both absolute and relative paths)
    fp_norm = _normalise(str(filepath))
    for suffix in EXEMPT_SUFFIXES:
        if fp_norm.endswith(suffix):
            return True
    return False


# ---------------------------------------------------------------------------
# Per-line detection
# ---------------------------------------------------------------------------

PATTERN = "sqlite3.connect("
EXEMPTION_COMMENT = "# sqlite3-ok"


def scan_file(filepath: Path) -> list[dict]:
    """Return a list of violation dicts for *filepath*.

    Each dict has keys: file, line, col, text.
    """
    violations: list[dict] = []
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return violations

    for lineno, line in enumerate(source.splitlines(), start=1):
        if PATTERN in line and EXEMPTION_COMMENT not in line:
            violations.append(
                {
                    "file": _normalise(str(filepath)),
                    "line": lineno,
                    "col": line.index(PATTERN) + 1,
                    "text": line.strip(),
                }
            )
    return violations


# ---------------------------------------------------------------------------
# Filesystem walk
# ---------------------------------------------------------------------------

def scan_tree(root: Path) -> list[dict]:
    """Recursively scan all .py files under *root/tools/* for violations."""
    tools_root = root / "tools"
    if not tools_root.is_dir():
        tools_root = root  # fallback: scan from root

    all_violations: list[dict] = []
    for py_file in sorted(tools_root.rglob("*.py")):
        if _is_exempt(py_file, root):
            continue
        all_violations.extend(scan_file(py_file))

    return all_violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Lint tools/ for bare sqlite3.connect() calls (use get_connection() instead)."
    )
    p.add_argument(
        "--path",
        default=".",
        help="Project root directory to scan (default: current working directory).",
    )
    p.add_argument(
        "--files",
        nargs="+",
        metavar="FILE",
        help=(
            "Scan only the specified files instead of the full tools/ tree. "
            "Pass git-changed paths here for pre-commit / CI incremental checks "
            "so only *new* violations are flagged."
        ),
    )
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit machine-readable JSON instead of human-readable text.",
    )
    p.add_argument(
        "--gate",
        action="store_true",
        help="Exit with code 1 when violations are found (CI gate mode).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(args.path).resolve()

    if args.files:
        # Incremental mode: only scan the explicitly listed files.
        # Used by pre-commit and CI to flag *new* violations without requiring
        # a mass-fix of the existing baseline.
        violations: list[dict] = []
        for f in args.files:
            fp = Path(f)
            if not fp.is_absolute():
                fp = root / fp
            if not fp.suffix == ".py":
                continue
            if _is_exempt(fp, root):
                continue
            # Only flag files that are actually inside tools/
            fp_norm = _normalise(str(fp))
            if "tools/" not in fp_norm:
                continue
            violations.extend(scan_file(fp))
    else:
        violations = scan_tree(root)

    if args.json_output:
        result = {
            "linter": "sqlite3_connect_linter",
            "root": _normalise(str(root)),
            "violation_count": len(violations),
            "violations": violations,
            "status": "FAIL" if violations else "PASS",
        }
        print(json.dumps(result, indent=2))
    else:
        if violations:
            print(
                f"sqlite3_connect_linter: {len(violations)} violation(s) found.\n"
                "Use get_connection() from tools.db.storage instead of sqlite3.connect().\n"
                "Add '# sqlite3-ok' to the line only if a direct connection is genuinely required.\n"
            )
            for v in violations:
                print(f"  {v['file']}:{v['line']}:{v['col']}  {v['text']}")
            print()
        else:
            print("sqlite3_connect_linter: PASS — no bare sqlite3.connect() calls found.")

    if args.gate and violations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
