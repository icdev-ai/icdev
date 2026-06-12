#!/usr/bin/env python3
from __future__ import annotations
"""Manifest verification tool.

Checks that a given tools/ file is registered in the manifest — searching
both the thin index (tools/manifest.md) and all shard files
(tools/manifest/*.md).  The manifest was sharded on 2026-04-14; any check
that only reads the index file will miss entries that live in shards.

CLI:
  python tools/verify_manifest.py tools/supply_chain/rare_earth_cascade.py
  python tools/verify_manifest.py --list-unregistered
  python tools/verify_manifest.py --dump-registered
"""

import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST_INDEX = Path(__file__).resolve().parent / "manifest.md"
_MANIFEST_SHARDS_DIR = Path(__file__).resolve().parent / "manifest"


def _collect_manifest_text() -> str:
    """Return concatenated text of the manifest index + all shard files."""
    parts: list[str] = []

    if _MANIFEST_INDEX.exists():
        parts.append(_MANIFEST_INDEX.read_text(encoding="utf-8"))

    if _MANIFEST_SHARDS_DIR.is_dir():
        for shard in sorted(_MANIFEST_SHARDS_DIR.glob("*.md")):
            parts.append(shard.read_text(encoding="utf-8"))

    return "\n".join(parts)


def _extract_tool_paths(text: str) -> set[str]:
    """Extract all tool file paths referenced in manifest tables.

    Manifest tables use the pattern:
        | Tool Name | tools/path/to/file.py | ... |

    We look for any cell that matches a tools/ path.
    """
    paths: set[str] = set()
    # Match tokens of the form  tools/<anything>  inside table cells.
    for match in re.finditer(r"tools/[\w./\-]+\.py", text):
        path = match.group(0).strip()
        # Normalise separators and strip trailing punctuation
        path = path.rstrip(".,;| ")
        paths.add(path)
    return paths


def _normalise(path_str: str) -> str:
    """Normalise a path string to forward-slash, relative form."""
    p = Path(path_str)
    # If absolute, make relative to repo root
    try:
        p = p.relative_to(_REPO_ROOT)
    except ValueError:
        pass
    return p.as_posix()


def is_registered(target: str) -> bool:
    """Return True if *target* is referenced in the manifest (index or any shard)."""
    target_norm = _normalise(target)
    text = _collect_manifest_text()
    registered = _extract_tool_paths(text)
    return target_norm in {_normalise(p) for p in registered}


def list_registered() -> list[str]:
    """Return sorted list of all paths found across manifests."""
    text = _collect_manifest_text()
    return sorted(_extract_tool_paths(text))


def list_unregistered() -> list[str]:
    """Return tools/ Python files that are not in any manifest."""
    tools_root = Path(__file__).resolve().parent
    all_py = {
        _normalise(str(p))
        for p in tools_root.rglob("*.py")
        if ".tmp" not in p.parts
    }
    registered = {_normalise(p) for p in list_registered()}
    return sorted(all_py - registered)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify that a tools/ file is registered in the manifest (index + shards)."
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Path to verify (e.g. tools/supply_chain/rare_earth_cascade.py)",
    )
    parser.add_argument(
        "--dump-registered",
        action="store_true",
        help="Print all paths found in the manifest and exit.",
    )
    parser.add_argument(
        "--list-unregistered",
        action="store_true",
        help="Print all tools/ .py files not found in the manifest and exit.",
    )
    args = parser.parse_args()

    if args.dump_registered:
        for p in list_registered():
            print(p)
        sys.exit(0)

    if args.list_unregistered:
        missing = list_unregistered()
        if missing:
            print(f"{len(missing)} unregistered file(s):")
            for p in missing:
                print(f"  {p}")
            sys.exit(1)
        else:
            print("All tools/ files are registered in the manifest.")
            sys.exit(0)

    if not args.target:
        parser.error("Provide a target file path to verify, or use --dump-registered / --list-unregistered.")

    if is_registered(args.target):
        print(f"OK: {args.target} is registered in the manifest.")
        sys.exit(0)
    else:
        print(f"FAIL: {args.target} is NOT found in tools/manifest.md or any tools/manifest/*.md shard.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
