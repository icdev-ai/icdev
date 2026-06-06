#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""ICDEV™ Package Exclusion Registry — single source of truth for pip package exclusions.

Reads args/package_exclusions.yaml and exposes helpers used by sync_package_tree.py,
pyproject.toml tooling, and MANIFEST.in generation.

CLI::

    python tools/installer/package_registry.py --list              # All exclusions
    python tools/installer/package_registry.py --list --json       # JSON output
    python tools/installer/package_registry.py --category marketplace
    python tools/installer/package_registry.py --paths             # Paths only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_EXCLUSIONS_PATH = BASE_DIR / "args" / "package_exclusions.yaml"

VALID_CATEGORIES = frozenset({"marketplace", "child_app", "parent_platform", "runtime_state"})


# ---------------------------------------------------------------------------
# YAML loader (mirrors module_registry.py pattern — stdlib fallback)
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore

        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        pass

    result: Dict[str, Any] = {}
    if not path.exists():
        return result

    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    current_list_key: Optional[str] = None
    current_item: Optional[Dict[str, Any]] = None

    for line in lines:
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        content = stripped.lstrip()

        if indent == 0 and ":" in content and not content.startswith("-"):
            key, _, val = content.partition(":")
            key = key.strip()
            val = val.strip()
            if not val:
                result[key] = []
                current_list_key = key
                current_item = None
            else:
                result[key] = val
            continue

        if indent == 2 and content.startswith("- ") and current_list_key is not None:
            if current_item is not None:
                result[current_list_key].append(current_item)
            # Inline key: value after "- "
            rest = content[2:].strip()
            if ":" in rest:
                k, _, v = rest.partition(":")
                current_item = {k.strip(): v.strip()}
            else:
                current_item = {"path": rest}
            continue

        if indent == 4 and ":" in content and current_item is not None:
            k, _, v = content.partition(":")
            current_item[k.strip()] = v.strip()
            continue

    if current_item is not None and current_list_key is not None:
        result[current_list_key].append(current_item)

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_exclusions(exclusions_path: Optional[Path] = None) -> Dict[str, List[Dict[str, str]]]:
    """Parse package_exclusions.yaml and return ``{category: [entry, ...]}``.

    Each entry is a dict with at least a ``path`` key and optionally ``reason``.

    Args:
        exclusions_path: Override path to the YAML file.

    Returns:
        Dict mapping category name to list of exclusion entry dicts.
    """
    path = exclusions_path or DEFAULT_EXCLUSIONS_PATH
    raw = _load_yaml(path)
    entries: List[Dict[str, str]] = raw.get("exclusions", [])

    by_category: Dict[str, List[Dict[str, str]]] = {cat: [] for cat in VALID_CATEGORIES}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cat = entry.get("category", "")
        if cat in by_category:
            by_category[cat].append(entry)
        else:
            by_category.setdefault(cat, []).append(entry)

    return by_category


def get_excluded_paths(
    category: Optional[str] = None,
    exclusions_path: Optional[Path] = None,
) -> List[str]:
    """Return a flat list of excluded paths, optionally filtered by category.

    Args:
        category: One of ``marketplace``, ``child_app``, ``parent_platform``,
            ``runtime_state``.  Pass ``None`` to return all paths.
        exclusions_path: Override path to the YAML file.

    Returns:
        List of path strings as they appear in the YAML.
    """
    by_cat = load_exclusions(exclusions_path)
    if category is not None:
        entries = by_cat.get(category, [])
    else:
        entries = [entry for entries in by_cat.values() for entry in entries]

    return [e["path"] for e in entries if "path" in e]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="ICDEV™ Package Exclusion Registry")
    parser.add_argument("--list", action="store_true", help="List all exclusions")
    parser.add_argument(
        "--category",
        choices=list(VALID_CATEGORIES),
        help="Filter by category",
    )
    parser.add_argument("--paths", action="store_true", help="Print paths only (one per line)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--exclusions-path",
        type=Path,
        default=None,
        help="Path to package_exclusions.yaml",
    )
    args = parser.parse_args()

    ep = args.exclusions_path

    if args.paths:
        paths = get_excluded_paths(category=args.category, exclusions_path=ep)
        if args.json:
            print(json.dumps(paths, indent=2))
        else:
            for p in paths:
                print(p)
        return 0

    by_cat = load_exclusions(exclusions_path=ep)

    if args.category:
        data = {args.category: by_cat.get(args.category, [])}
    else:
        data = by_cat

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    for cat, entries in data.items():
        if not entries:
            continue
        print(f"\n[{cat}]")
        for e in entries:
            reason = f"  # {e['reason']}" if e.get("reason") else ""
            print(f"  {e.get('path', '?')}{reason}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
