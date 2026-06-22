#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev scaffold` — generate ICDEV™ canvases and child apps from templates.

Subcommands:
  icdev scaffold canvas <key> --display-name "Name" [--out PATH]

Examples:
  icdev scaffold canvas demo --display-name "Demo Canvas" --out ./demo-canvas
  icdev scaffold canvas demo --display-name "Demo Canvas" --vars url_prefix=/demo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path so `tools.builder` is importable when run directly.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if REPO_ROOT.name == "icdev":
    REPO_ROOT = REPO_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.builder.template_engine import render_tree

BASE_DIR = REPO_ROOT


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="icdev scaffold",
        description=__doc__,
    )
    sub = parser.add_subparsers(dest="target", required=True)

    canvas = sub.add_parser("canvas", help="Scaffold a new design canvas")
    canvas.add_argument("key", help="Short canvas key (e.g. demo)")
    canvas.add_argument("--display-name", required=True, help="Human-facing canvas name")
    canvas.add_argument("--env-flag", default=None, help="Primary .env toggle (default: ICDEV_<KEY>_ENABLED)")
    canvas.add_argument("--url-prefix", default=None, help="Flask url_prefix (default: /<key>)")
    canvas.add_argument("--template", default="data/templates/canvases/minimal", help="Template directory")
    canvas.add_argument("--out", default=None, help="Output directory (default: ./<key>-canvas)")
    canvas.add_argument("--vars", nargs="*", default=[], help="Extra variable overrides as key=value")
    canvas.add_argument("--json", action="store_true", help="Emit JSON result")

    child_app = sub.add_parser("child-app", help="Scaffold a new child application")
    child_app.add_argument("key", help="Short app key (e.g. my_app)")
    child_app.add_argument("--display-name", required=True, help="Human-facing app name")
    child_app.add_argument("--env-flag", default=None, help="Primary .env toggle (default: ICDEV_<KEY>_ENABLED)")
    child_app.add_argument("--url-prefix", default=None, help="Flask url_prefix (default: /<key>)")
    child_app.add_argument("--template", default="data/templates/child_apps/minimal", help="Template directory")
    child_app.add_argument("--out", default=None, help="Output directory (default: ./<key>-app)")
    child_app.add_argument("--vars", nargs="*", default=[], help="Extra variable overrides as key=value")
    child_app.add_argument("--json", action="store_true", help="Emit JSON result")

    return parser


def _derive_env_flag(key: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    return f"ICDEV_{key.upper()}_ENABLED"


def _derive_url_prefix(key: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    return f"/{key}"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    template_dir = BASE_DIR / args.template
    if not template_dir.exists():
        print(f"Template not found: {template_dir}", file=sys.stderr)
        return 2

    default_suffix = "canvas" if args.target == "canvas" else "app"
    out_dir = Path(args.out).resolve() if args.out else Path.cwd() / f"{args.key}-{default_suffix}"

    variables: dict[str, str] = {
        "key": args.key,
        "display_name": args.display_name,
        "env_flag": _derive_env_flag(args.key, args.env_flag),
        "url_prefix": _derive_url_prefix(args.key, args.url_prefix),
    }

    for raw in args.vars:
        if "=" not in raw:
            print(f"Invalid --vars entry (expected key=value): {raw}", file=sys.stderr)
            return 2
        k, v = raw.split("=", 1)
        variables[k.strip()] = v.strip()

    result = render_tree(template_dir, out_dir, variables)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Scaffolded {args.display_name} to {out_dir}")
        for f in result["rendered_files"]:
            print(f"  + {f}")
        if result.get("errors"):
            print("Errors:")
            for e in result["errors"]:
                print(f"  ! {e}")
        if result.get("validation_failures"):
            print("Validation failures:")
            for e in result["validation_failures"]:
                print(f"  ! {e}")

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
