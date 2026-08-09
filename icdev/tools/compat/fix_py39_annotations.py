#!/usr/bin/env python3
# CUI // SP-CTI
"""One-shot fix: add 'from __future__ import annotations' to files that use
PEP 604 union syntax (str | None) without the future import.

PEP 604 union syntax is valid at RUNTIME only on Python 3.10+.
pyproject.toml declares requires-python = ">=3.9", so any file using
X | Y in type annotations without the future import will raise TypeError
on Python 3.9.

Run once, then optionally delete this script.
Usage: python tools/compat/fix_py39_annotations.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# Detect PEP 604 union in annotation contexts:
# - return type:   -> SomeType | OtherType
# - param/var:     param: SomeType | OtherType
# Anchored to annotation-like positions; avoids bare bitwise-OR in logic.
_UNION_RE = re.compile(
    r"(?:"
    r"->[ \t]*[\w\[\]\., ]+\|[ \t]*[\w\[\]\., ]+"  # return type hint
    r"|"
    r":[ \t]*[\w\[\]\., ]+\|[ \t]*[\w\[\]\., ]+"   # param / variable annotation
    r")"
)

_SKIP_DIRS = {
    ".tmp", ".git", "__pycache__", ".venv", "venv", "env",
    "build", "dist", ".eggs", "node_modules", "site-packages",
}


def _should_skip(path: Path) -> bool:
    for part in path.parts:
        if part in _SKIP_DIRS:
            return True
    return False


def _insert_future_import(src: str) -> str:
    """Insert 'from __future__ import annotations' after any shebang/encoding comment."""
    lines = src.splitlines(keepends=True)
    insert_at = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#!") or (
            stripped.startswith("#") and "coding" in stripped.lower()
        ):
            insert_at = i + 1
        else:
            break
    lines.insert(insert_at, "from __future__ import annotations\n")
    return "".join(lines)


def main(dry_run: bool = False) -> int:
    fixed = []
    skipped_errors = []

    for path in sorted(ROOT.rglob("*.py")):
        if _should_skip(path):
            continue
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if "from __future__ import annotations" in src:
            continue
        if not _UNION_RE.search(src):
            continue

        fixed.append(path)
        rel = path.relative_to(ROOT)
        if dry_run:
            print(f"[DRY-RUN] would fix: {rel}")
        else:
            try:
                new_src = _insert_future_import(src)
                path.write_text(new_src, encoding="utf-8", newline="")
                print(f"Fixed: {rel}")
            except OSError as exc:
                skipped_errors.append((rel, str(exc)))
                print(f"ERROR writing {rel}: {exc}", file=sys.stderr)

    action = "Would fix" if dry_run else "Fixed"
    print(f"\n{action} {len(fixed)} file(s).")
    if skipped_errors:
        print(f"Errors: {len(skipped_errors)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Show files without modifying")
    args = p.parse_args()
    sys.exit(main(dry_run=args.dry_run))
