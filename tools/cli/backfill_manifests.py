#!/usr/bin/env python3
# CUI // SP-CTI
"""Backfill manifest stubs for all registered canvases.

For each canvas in args/component_registry.yaml, creates a minimal
data/templates/canvases/{key}/manifest.yaml that declares:
  - kind: canvas_stub
  - The component's current variable values as defaults
  - File references (mode: reference) pointing at existing files

This makes every existing canvas discoverable, diff-able against a
template baseline, and reproducible via `icdev scaffold canvas <key>
--from-existing` in a future release.

Usage:
    python tools/cli/backfill_manifests.py [--dry-run] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if REPO_ROOT.name == "icdev":
    REPO_ROOT = REPO_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TEMPLATES_DIR = REPO_ROOT / "data" / "templates" / "canvases"


def _build_stub_manifest(comp) -> dict:
    """Build a manifest stub dict for a canvas component."""
    module_package = comp.module.rsplit(".", 1)[0] if comp.module else f"tools.{comp.key}_canvas"
    module_path = module_package.replace(".", "/")

    files = [
        {
            "src": f"{module_path}/blueprint.py",
            "dest": f"{module_path}/blueprint.py",
            "mode": "reference",
        },
        {
            "src": f"{module_path}/constants.py",
            "dest": f"{module_path}/constants.py",
            "mode": "reference",
        },
        {
            "src": f"tools/dashboard/templates/{comp.key}/page.html",
            "dest": f"tools/dashboard/templates/{comp.key}/page.html",
            "mode": "reference",
        },
    ]

    if comp.completeness.get("db_migration"):
        files.append({
            "src": comp.completeness["db_migration"],
            "dest": comp.completeness["db_migration"],
            "mode": "reference",
        })

    if comp.completeness.get("iqe_adapter"):
        files.append({
            "src": f"tools/iqe/adapters/{comp.key}.py",
            "dest": f"tools/iqe/adapters/{comp.key}.py",
            "mode": "reference",
        })

    seed_dir = comp.completeness.get("seed_queries", "")
    if seed_dir:
        files.append({
            "src": seed_dir,
            "dest": seed_dir,
            "mode": "reference",
        })

    return {
        "name": comp.key,
        "kind": "canvas_stub",
        "description": f"Reference stub for existing {comp.display_name} canvas.",
        "variables": {
            "key": {
                "type": "string",
                "default": comp.key,
                "description": "Canvas key",
            },
            "display_name": {
                "type": "string",
                "default": comp.display_name,
                "description": "Human-facing canvas name",
            },
            "env_flag": {
                "type": "string",
                "default": comp.env_flag,
                "description": "Primary .env toggle",
            },
            "url_prefix": {
                "type": "string",
                "default": comp.url_prefix or f"/{comp.key}",
                "description": "Flask url_prefix",
            },
            "module_package": {
                "type": "string",
                "default": module_package,
                "description": "Python package path",
            },
        },
        "files": files,
        "validators": [],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--json", action="store_true", dest="emit_json", help="JSON output")
    parser.add_argument("--key", default=None, help="Only backfill a specific canvas key")
    args = parser.parse_args(argv)

    try:
        import yaml
    except ImportError:
        print("PyYAML required. Run: pip install pyyaml", file=sys.stderr)
        return 1

    try:
        from tools.config.component_registry import get_registry
        registry = get_registry()
    except Exception as exc:
        print(f"Cannot load registry: {exc}", file=sys.stderr)
        return 1

    results = []
    for comp in registry.iter_canvases():
        if args.key and comp.key != args.key:
            continue

        stub_dir = TEMPLATES_DIR / comp.key
        stub_path = stub_dir / "manifest.yaml"

        if stub_path.exists():
            results.append({"key": comp.key, "status": "already_exists", "path": str(stub_path)})
            continue

        manifest_data = _build_stub_manifest(comp)

        if args.dry_run:
            results.append({"key": comp.key, "status": "would_create", "path": str(stub_path)})
            continue

        stub_dir.mkdir(parents=True, exist_ok=True)
        with open(stub_path, "w", encoding="utf-8") as fh:
            fh.write("# CUI // SP-CTI\n")
            fh.write("# Canvas stub manifest — auto-generated by backfill_manifests.py\n")
            yaml.dump(manifest_data, fh, default_flow_style=False, allow_unicode=True,
                      sort_keys=False, indent=2)

        results.append({"key": comp.key, "status": "created", "path": str(stub_path)})

    if args.emit_json:
        print(json.dumps(results, indent=2))
    else:
        created = [r for r in results if r["status"] == "created"]
        exists = [r for r in results if r["status"] == "already_exists"]
        would = [r for r in results if r["status"] == "would_create"]
        prefix = "[dry-run] " if args.dry_run else ""
        print(f"{prefix}Created: {len(created)}  Already exists: {len(exists)}  Would create: {len(would)}")
        for r in (would if args.dry_run else created):
            print(f"  {'(would create)' if args.dry_run else '+'} {r['path']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
