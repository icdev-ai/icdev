"""
pg_portability_linter.py — ICDEV™ PG Portability Lint Gate (pgp-tx-03)

AST-scans runtime tools/ modules for SQLite-dialect SQL patterns that are
unsafe in PostgreSQL and that the translate_sql shim should NOT handle at
runtime (it is init-only fallback only).

Severity levels
---------------
HIGH   — Must be zero before merge (pgp-tx-03 gate blocks CI)
           * json_each(      — SQLite-only table-valued function
           * json_array_length(json_extract(  — nested SQLite JSON funcs
           * sqlite3.connect(  — bare connection (sibling exemptions apply)
MEDIUM — Tracked; add to baseline to allow (debt-snapshot model)
           * json_extract(   — standalone SQLite JSON extraction
           * json_array_length(  — standalone SQLite JSON length
           * PRAGMA           — SQLite PRAGMA statements
           * sqlite_master    — SQLite system catalogue

Exemptions
----------
- Inline ``# pg-ok`` comment skips the line.
- Files named init_db.py, seed_*.py, migrate_*.py, */schema/*.py,
  */lint/*.py, */tests/*.py, and anything under tests/ are excluded.
- A baseline JSON (tools/lint/pg_portability_baseline.json) snapshots
  existing debt; only findings NOT in the baseline are reported.
  The CI gate (--gate) only blocks on NEW high-severity findings.

Usage
-----
    python tools/lint/pg_portability_linter.py [--path DIR] [--json] [--gate]

Exit codes
----------
    0   No high-severity findings above baseline (or --gate not passed)
    1   High-severity findings above baseline AND --gate was passed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# File-level exemption lists  (mirrors sqlite3_connect_linter sibling logic)
# ---------------------------------------------------------------------------

_EXEMPT_SUFFIXES: tuple[str, ...] = (
    "tools/db/storage.py",
    "tools/db/init_icdev_db.py",
    "tools/db/migration_runner.py",
    "tools/db/backup_manager.py",
    "tools/db/pg_init.py",
    "tools/db/migrate_add_missing_columns.py",
    "tools/db/migrate_to_storage.py",
    "tools/db/migrations/018_memory_db_consolidation/up.py",
    "tools/lint/sqlite3_connect_linter.py",
    "tools/lint/pg_portability_linter.py",
    "tools/saas/platform_db.py",
    "tools/saas/db/db_compat.py",
)

_EXEMPT_NAMES: tuple[str, ...] = (
    "init_db.py",
)

_EXEMPT_PATTERNS: tuple[str, ...] = (
    "seed_",
    "migrate_",
)

_EXEMPT_DIRS: tuple[str, ...] = (
    "/tests/",
    "\\tests\\",
    "/schema/",
    "\\schema\\",
    "/lint/",
    "\\lint\\",
)


def _normalise(path: str) -> str:
    return path.replace("\\", "/")


def _is_exempt(filepath: Path) -> bool:
    name = filepath.name
    # Exempt by exact filename
    if name in _EXEMPT_NAMES:
        return True
    # Exempt by filename prefix
    for prefix in _EXEMPT_PATTERNS:
        if name.startswith(prefix):
            return True
    fp_norm = _normalise(str(filepath))
    # Exempt by suffix (handles absolute and relative paths)
    for suffix in _EXEMPT_SUFFIXES:
        if fp_norm.endswith(suffix):
            return True
    # Exempt by directory segment
    for d in _EXEMPT_DIRS:
        d_norm = _normalise(d)
        if d_norm in fp_norm:
            return True
    return False


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

_EXEMPTION_COMMENT = "# pg-ok"

# Each entry: (pattern_text, severity, fix_hint)
# Order matters: check more-specific HIGH patterns before MEDIUM catch-alls.
_HIGH_PATTERNS: list[tuple[str, str, str]] = [
    (
        "json_array_length(json_extract(",
        "high",
        "Compute in Python: json.loads(col)['key'] or len(json.loads(col))",
    ),
    (
        "json_each(",
        "high",
        "SQLite-only table-valued function; expand JSON in Python and insert rows individually",
    ),
]

# sqlite3.connect is handled separately (needs sibling exemption logic)
_SQLITE3_CONNECT_PATTERN = "sqlite3.connect("

_MEDIUM_PATTERNS: list[tuple[str, str, str]] = [
    (
        "json_extract(",
        "medium",
        "Use jsonb operators in PG (data->'key') or compute in Python via json.loads()",
    ),
    (
        "json_array_length(",
        "medium",
        "Use jsonb_array_length() in PG or compute via len(json.loads(col))",
    ),
    (
        "PRAGMA ",
        "medium",
        "SQLite PRAGMA has no PG equivalent; remove or guard behind is_sqlite check",
    ),
    (
        "sqlite_master",
        "medium",
        "SQLite system catalogue; use information_schema.tables in PG",
    ),
]


def _in_docstring_context(lines: list[str], lineno_0: int) -> bool:
    """Return True if *lineno_0* (0-based) is inside a triple-quoted string."""
    in_triple_single = False
    in_triple_double = False
    for i, line in enumerate(lines):
        if i >= lineno_0:
            break
        stripped = line.strip()
        # Count triple-quote toggles (simplified; handles typical docstrings)
        in_triple_double ^= stripped.count('"""') % 2 == 1
        in_triple_single ^= stripped.count("'''") % 2 == 1
    return in_triple_double or in_triple_single


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def scan_file(filepath: Path) -> list[dict]:
    """Scan *filepath* and return a list of PG-portability finding dicts.

    Each dict has keys: file, line, col, pattern, severity, fix.
    Lines containing ``# pg-ok`` are skipped.
    Lines that are pure comments (stripped starts with ``#``) are skipped.
    """
    findings: list[dict] = []
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings

    lines = source.splitlines()
    fp_norm = _normalise(str(filepath))

    for lineno_0, line in enumerate(lines):
        lineno = lineno_0 + 1
        stripped = line.strip()

        # Skip blank lines
        if not stripped:
            continue
        # Skip pure comment lines
        if stripped.startswith("#"):
            continue
        # Respect the pg-ok opt-out
        if _EXEMPTION_COMMENT in line:
            continue

        # --- HIGH: json_array_length(json_extract(...)) -------------------------
        # Check before the standalone json_array_length pattern
        for pattern, severity, fix in _HIGH_PATTERNS:
            if pattern in line:
                findings.append(
                    {
                        "file": fp_norm,
                        "line": lineno,
                        "col": line.index(pattern) + 1,
                        "pattern": pattern,
                        "severity": severity,
                        "fix": fix,
                        "text": stripped,
                    }
                )
                break  # Only emit the most severe match per line

        else:
            # --- HIGH: sqlite3.connect (with sibling exemptions) ---------------
            if _SQLITE3_CONNECT_PATTERN in line:
                findings.append(
                    {
                        "file": fp_norm,
                        "line": lineno,
                        "col": line.index(_SQLITE3_CONNECT_PATTERN) + 1,
                        "pattern": _SQLITE3_CONNECT_PATTERN,
                        "severity": "high",
                        "fix": "Use get_connection() from tools.db.storage instead",
                        "text": stripped,
                    }
                )
                continue  # Already flagged high — skip medium checks

            # --- MEDIUM patterns ------------------------------------------------
            for pattern, severity, fix in _MEDIUM_PATTERNS:
                if pattern in line:
                    # Don't double-report lines already caught by a HIGH json pattern
                    already_high = any(hp in line for hp, _, _ in _HIGH_PATTERNS)
                    if already_high:
                        break
                    findings.append(
                        {
                            "file": fp_norm,
                            "line": lineno,
                            "col": line.index(pattern) + 1,
                            "pattern": pattern,
                            "severity": severity,
                            "fix": fix,
                            "text": stripped,
                        }
                    )
                    break  # One finding per line

    return findings


# ---------------------------------------------------------------------------
# Baseline helpers
# ---------------------------------------------------------------------------

_DEFAULT_BASELINE = "tools/lint/pg_portability_baseline.json"


def _load_baseline(baseline_path: Path) -> set[tuple]:
    """Load baseline as a set of (file, line, pattern) tuples."""
    if not baseline_path.exists():
        return set()
    try:
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        return {(e["file"], e["line"], e["pattern"]) for e in data.get("findings", [])}
    except (json.JSONDecodeError, KeyError, OSError):
        return set()


def _finding_key(f: dict) -> tuple:
    return (f["file"], f["line"], f["pattern"])


def _rel(file: str, root) -> str:
    """Repo-relative posix path — keeps the baseline portable across checkouts/CI."""
    try:
        return os.path.relpath(file, str(root)).replace("\\", "/")
    except ValueError:  # e.g. different drive on Windows
        return _normalise(file)


def _rel_key(f: dict, root) -> tuple:
    """Root-relative finding key so a committed baseline matches any checkout."""
    return (_rel(f["file"], root), f["line"], f["pattern"])


# ---------------------------------------------------------------------------
# Filesystem walk
# ---------------------------------------------------------------------------


def scan_tree(root: Path) -> list[dict]:
    """Recursively scan all .py files under *root/tools/* for PG portability issues."""
    tools_root = root / "tools"
    if not tools_root.is_dir():
        tools_root = root

    all_findings: list[dict] = []
    for py_file in sorted(tools_root.rglob("*.py")):
        if _is_exempt(py_file):
            continue
        all_findings.extend(scan_file(py_file))
    return all_findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Lint tools/ for PG-unsafe SQLite-dialect SQL patterns (pgp-tx-03)."
    )
    p.add_argument("--path", default=".", help="Project root (default: cwd)")
    p.add_argument(
        "--files",
        nargs="+",
        metavar="FILE",
        help="Scan specific files only (incremental / pre-commit mode)",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Emit JSON output")
    p.add_argument(
        "--baseline",
        default=None,
        help=f"Baseline JSON path (default: <root>/{_DEFAULT_BASELINE})",
    )
    p.add_argument(
        "--write-baseline",
        action="store_true",
        help="Write current findings to baseline and exit 0",
    )
    p.add_argument(
        "--gate",
        action="store_true",
        help="Exit 1 when high-severity findings exceed baseline (CI gate mode)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(args.path).resolve()
    baseline_path = Path(args.baseline) if args.baseline else root / _DEFAULT_BASELINE

    if args.files:
        findings: list[dict] = []
        for f in args.files:
            fp = Path(f)
            if not fp.is_absolute():
                fp = root / fp
            if fp.suffix != ".py" or _is_exempt(fp):
                continue
            fp_norm = _normalise(str(fp))
            if "tools/" not in fp_norm:
                continue
            findings.extend(scan_file(fp))
    else:
        findings = scan_tree(root)

    if args.write_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        # Store repo-relative paths so the committed baseline matches any checkout
        # (worktrees, CI) rather than this machine's absolute paths.
        rel_findings = [{**f, "file": _rel(f["file"], root)} for f in findings]
        data = {
            "linter": "pg_portability_linter",
            "root": ".",
            "finding_count": len(rel_findings),
            "findings": rel_findings,
        }
        baseline_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"pg_portability_linter: baseline written to {baseline_path} ({len(rel_findings)} findings)")
        return 0

    baseline = _load_baseline(baseline_path)
    new_findings = [f for f in findings if _rel_key(f, root) not in baseline]
    new_highs = [f for f in new_findings if f["severity"] == "high"]

    if args.json_output:
        result = {
            "linter": "pg_portability_linter",
            "root": _normalise(str(root)),
            "total_findings": len(findings),
            "new_findings": len(new_findings),
            "new_high_count": len(new_highs),
            "findings": new_findings,
            "status": "FAIL" if new_highs else "PASS",
        }
        print(json.dumps(result, indent=2))
    else:
        if new_findings:
            highs = [f for f in new_findings if f["severity"] == "high"]
            mediums = [f for f in new_findings if f["severity"] == "medium"]
            if highs:
                print(
                    f"pg_portability_linter: {len(highs)} HIGH finding(s) — "
                    "add '# pg-ok' to exempt a line or fix the pattern.\n"
                )
                for f in highs:
                    print(f"  HIGH  {f['file']}:{f['line']}  [{f['pattern']}]")
                    print(f"        {f['text']}")
                    print(f"        Fix: {f['fix']}")
                print()
            if mediums:
                print(f"pg_portability_linter: {len(mediums)} MEDIUM finding(s) (tracked against baseline).")
                for f in mediums:
                    print(f"  MED   {f['file']}:{f['line']}  [{f['pattern']}]")
        else:
            print("pg_portability_linter: PASS — no new PG-portability findings.")

    if args.gate and new_highs:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
