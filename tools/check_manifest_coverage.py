#!/usr/bin/env python3
"""
Check that every tools/*.py file has an entry in tools/manifest.md or one of
its shard files (tools/manifest/*.md).

Usage:
    python tools/check_manifest_coverage.py [--json] [--verbose] [--file <stem>]

Exit codes:
    0  all checked files are documented
    1  one or more files are undocumented
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _collect_manifest_text() -> str:
    """Return the concatenated text of manifest.md + all shard files."""
    parts: List[str] = []

    index = PROJECT_ROOT / "tools" / "manifest.md"
    if index.exists():
        parts.append(_read(index))

    shard_dir = PROJECT_ROOT / "tools" / "manifest"
    if shard_dir.is_dir():
        for shard in sorted(shard_dir.glob("*.md")):
            parts.append(_read(shard))

    return "\n".join(parts)


def _documented_stems(manifest_text: str) -> Set[str]:
    """
    Extract every file stem that appears in manifest table cells.

    Handles:
      - | Tool | tools/supply_chain/rare_earth_cascade.py | …
      - backtick-wrapped paths: `tools/foo/bar.py`
      - backslash separators (Windows copy-paste)
    """
    stems: Set[str] = set()
    lower = manifest_text.lower()

    # Table cell: | anything | tools/path/file.py |
    for m in re.finditer(r"\|\s*[^|]*\|\s*`?(tools[/\\][\w/\\.]+?\.py)`?\s*\|", lower):
        path_str = m.group(1).replace("\\", "/")
        stems.add(Path(path_str).stem)

    # Backtick-wrapped paths anywhere in the text
    for m in re.finditer(r"`(tools[/\\][\w/\\.]+?\.py)`", lower):
        path_str = m.group(1).replace("\\", "/")
        stems.add(Path(path_str).stem)

    # Bare filenames like rare_earth_cascade.py that appear anywhere
    for m in re.finditer(r"\b([\w]+\.py)\b", lower):
        stems.add(Path(m.group(1)).stem)

    return stems


def _tool_python_files() -> List[Path]:
    """Yield tool Python files eligible for manifest documentation."""
    tools_dir = PROJECT_ROOT / "tools"
    skip_names = {"__init__.py", "constants.py", "conftest.py"}
    results: List[Path] = []
    for py in sorted(tools_dir.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        if py.name in skip_names or py.name.startswith("_"):
            continue
        rel = py.relative_to(PROJECT_ROOT)
        depth = len(rel.parts) - 1  # depth inside tools/
        if depth > 3:
            continue
        results.append(py)
    return results


def check_coverage(target_stem: str | None = None) -> Dict:
    manifest_text = _collect_manifest_text()
    stems = _documented_stems(manifest_text)

    py_files = _tool_python_files()
    missing: List[str] = []
    found: List[str] = []

    for py in py_files:
        stem = py.stem.lower()
        if target_stem and stem != target_stem.lower():
            continue
        rel = str(py.relative_to(PROJECT_ROOT)).replace("\\", "/")
        if stem in stems:
            found.append(rel)
        else:
            missing.append(rel)

    return {
        "shards_dir": str(PROJECT_ROOT / "tools" / "manifest"),
        "total_checked": len(found) + len(missing),
        "documented": len(found),
        "undocumented": len(missing),
        "missing": missing,
        "found": found,
        "ok": len(missing) == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--verbose", "-v", action="store_true", help="List found entries too")
    parser.add_argument("--file", metavar="STEM", help="Check a single file stem, e.g. rare_earth_cascade")
    args = parser.parse_args()

    result = check_coverage(target_stem=args.file)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if args.verbose and result["found"]:
            print("Documented:")
            for f in result["found"]:
                print(f"  ✓ {f}")
        if result["missing"]:
            print("Undocumented (not found in any manifest shard):")
            for f in result["missing"]:
                print(f"  ✗ {f}")
        summary = (
            f"Checked {result['total_checked']} file(s): "
            f"{result['documented']} documented, {result['undocumented']} missing."
        )
        print(summary)
        if result["ok"]:
            print("OK — all files covered.")
        else:
            print("FAIL — missing manifest entries.")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
