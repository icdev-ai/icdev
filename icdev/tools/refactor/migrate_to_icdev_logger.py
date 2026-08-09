#!/usr/bin/env python3
"""Bulk migrate tools/ modules from logging.getLogger() to icdev_logger.get_logger().

Safe guards:
  - Skips tools/logging/ (implementation package)
  - Skips tests/, __pycache__, conftest.py, setup.py
  - Only touches files that contain logging.getLogger(
  - Adds import only if not already present
  - Does NOT remove import logging (may be needed for other constants)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
IMPORT_LINE = "from tools.logging.icdev_logger import get_logger"


def migrate_file(py_file: Path) -> bool:
    src = py_file.read_text(encoding="utf-8")

    # Skip if no logging.getLogger( usage
    if not re.search(r"\blogging\.getLogger\s*\(", src):
        return False

    # Skip if already imports get_logger from icdev_logger
    if re.search(r"from\s+tools\.logging\.icdev_logger\s+import\s+.*\bget_logger\b", src):
        # Already imported — just replace usages
        new_src = re.sub(r"\blogging\.getLogger\s*\(", "get_logger(", src)
        if new_src != src:
            py_file.write_text(new_src, encoding="utf-8", newline="")
            return True
        return False

    # Also skip if get_logger is already used (imported via __init__ or other path)
    if re.search(r"\bget_logger\s*\(", src):
        return False

    # Add import line after any __future__ import, otherwise after first shebang/encoding line
    lines = src.splitlines(keepends=True)
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("from __future__"):
            insert_idx = i + 1
        elif line.strip().startswith("#!/") or line.strip().startswith("# -*- coding"):
            insert_idx = i + 1

    # Insert blank line before import if needed
    if insert_idx < len(lines) and lines[insert_idx].strip():
        lines.insert(insert_idx, "\n")
        insert_idx += 1

    lines.insert(insert_idx, IMPORT_LINE + "\n")

    new_src = "".join(lines)
    new_src = re.sub(r"\blogging\.getLogger\s*\(", "get_logger(", new_src)

    py_file.write_text(new_src, encoding="utf-8", newline="")
    return True


def main() -> int:
    changed = 0
    checked = 0
    for py_file in TOOLS_DIR.rglob("*.py"):
        rel = py_file.relative_to(PROJECT_ROOT)
        parts = rel.parts
        if "logging" in parts or "tests" in parts or "__pycache__" in parts:
            continue
        if py_file.name in ("conftest.py", "setup.py", "migrate_to_icdev_logger.py"):
            continue
        checked += 1
        if migrate_file(py_file):
            changed += 1
            print(f"  migrated {rel}")

    print(f"\nDone: {changed} file(s) changed out of {checked} checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
