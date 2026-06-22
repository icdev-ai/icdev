#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev scaffold` — generate ICDEV™ canvases and child apps from templates.

Subcommands:
  icdev scaffold canvas <key> --display-name "Name" [--flavor FLAVOR] [--out PATH]
  icdev scaffold child-app <key> --display-name "Name" [--flavor FLAVOR] [--canvases ...] [--out PATH]

Examples:
  icdev scaffold canvas demo --display-name "Demo Canvas" --out ./demo-canvas
  icdev scaffold canvas demo --display-name "Demo Canvas" --vars url_prefix=/demo
  icdev scaffold child-app my_lab --display-name "My Lab" --flavor ai-lab --canvases dic,slides
  icdev scaffold child-app my_lab --display-name "My Lab" --template ./custom-template
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

REGISTRY_PATH = REPO_ROOT / "args" / "component_registry.yaml"

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
    canvas.add_argument("--flavor", default=None, help="Built-in canvas flavor (e.g. minimal)")
    canvas.add_argument("--template", default="data/templates/canvases/minimal", help="Template directory (overrides --flavor)")
    canvas.add_argument("--out", default=None, help="Output directory (default: ./<key>-canvas)")
    canvas.add_argument("--vars", nargs="*", default=[], help="Extra variable overrides as key=value")
    canvas.add_argument("--dry-run", action="store_true", help="Preview what would be generated without writing files")
    canvas.add_argument("--no-register", action="store_true", help="Skip auto-registration in component_registry.yaml")
    canvas.add_argument("--json", action="store_true", help="Emit JSON result")

    child_app = sub.add_parser("child-app", help="Scaffold a new child application")
    child_app.add_argument("key", help="Short app key (e.g. my_app)")
    child_app.add_argument("--display-name", required=True, help="Human-facing app name")
    child_app.add_argument("--env-flag", default=None, help="Primary .env toggle (default: ICDEV_<KEY>_ENABLED)")
    child_app.add_argument("--url-prefix", default=None, help="Flask url_prefix (default: /<key>)")
    child_app.add_argument("--flavor", default=None, help="Built-in child-app flavor (e.g. minimal, ai-lab, compliance, govcon)")
    child_app.add_argument("--canvases", default=None, help="Comma-separated canvas keys this child app depends on (template-specific)")
    child_app.add_argument("--template", default="data/templates/child_apps/minimal", help="Template directory (overrides --flavor)")
    child_app.add_argument("--out", default=None, help="Output directory (default: ./<key>-app)")
    child_app.add_argument("--vars", nargs="*", default=[], help="Extra variable overrides as key=value")
    child_app.add_argument("--dry-run", action="store_true", help="Preview what would be generated without writing files")
    child_app.add_argument("--no-register", action="store_true", help="Skip auto-registration in component_registry.yaml")
    child_app.add_argument("--json", action="store_true", help="Emit JSON result")

    return parser


def _derive_env_flag(key: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    return f"ICDEV_{key.upper()}_ENABLED"


def _derive_url_prefix(key: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    return f"/{key.replace('_', '-')}"


def _register_component(
    kind: str,
    key: str,
    display_name: str,
    env_flag: str,
    url_prefix: str,
    default_roles: list[str],
    dry_run: bool = False,
) -> dict:
    """Append a new component entry to args/component_registry.yaml."""
    try:
        import yaml
    except ImportError:
        return {"registered": False, "error": "PyYAML not available"}

    if not REGISTRY_PATH.exists():
        return {"registered": False, "error": f"Registry not found: {REGISTRY_PATH}"}

    with open(REGISTRY_PATH, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    kind_key = "canvases" if kind == "canvas" else ("child_apps" if kind == "child-app" else "core_extensions")
    entries = data.setdefault(kind_key, [])

    # Skip if already registered
    if any(e.get("key") == key for e in entries):
        return {"registered": False, "reason": f"{key} already in registry"}

    cli_name = key.replace("_", "-")
    module = f"tools.{key}_canvas.blueprint" if kind == "canvas" else f"tools.{key}.blueprint"
    blueprint_attr = f"create_{key}_blueprint" if kind == "canvas" else f"create_{key}_app"

    new_entry = {
        "key": key,
        "kind": "canvas" if kind == "canvas" else kind,
        "display_name": display_name,
        "cli_name": cli_name,
        "description": f"{display_name} (scaffolded)",
        "env_flag": env_flag,
        "extra_env_flags": [],
        "default_enabled": False,
        "module": module,
        "blueprint_attr": blueprint_attr,
        "url_prefix": url_prefix,
        "min_il": "IL4",
        "default_roles": default_roles,
        "nav": {
            "section": "Canvases",
            "label": display_name,
            "links": [{"label": "Overview", "href": f"{url_prefix}/"}],
        },
        "iqe": {
            "adapter_module": f"tools.iqe.adapters.{key}",
            "collections": [f"{key}.items", f"{key}.events"],
        },
        "completeness": {
            "template": f"tools/dashboard/templates/{key}/page.html",
            "blueprint": True,
            "constants": f"tools/{key}_canvas/constants.py",
            "db_migration": f"tools/{key}_canvas/db",
            "iqe_adapter": True,
            "nav_link": True,
            "seed_queries": f"context/iqe/queries/{key}/",
        },
    }

    if dry_run:
        return {"registered": False, "dry_run": True, "would_add": new_entry}

    entries.append(new_entry)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False, indent=2)

    # Log to component_audit_log
    try:
        from tools.config.component_registry import log_component_audit
        log_component_audit(
            event_type="scaffold",
            actor="icdev-cli",
            component_key=key,
            details={"kind": kind, "display_name": display_name, "env_flag": env_flag},
        )
    except Exception:
        pass

    return {"registered": True, "key": key, "kind": kind_key}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.flavor:
        kind = "canvases" if args.target == "canvas" else "child_apps"
        template_dir = BASE_DIR / "data" / "templates" / kind / args.flavor
    else:
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
    if args.target == "child-app" and args.canvases:
        variables["canvases"] = args.canvases

    for raw in args.vars:
        if "=" not in raw:
            print(f"Invalid --vars entry (expected key=value): {raw}", file=sys.stderr)
            return 2
        k, v = raw.split("=", 1)
        variables[k.strip()] = v.strip()

    dry_run = getattr(args, "dry_run", False)
    no_register = getattr(args, "no_register", False)

    result = render_tree(template_dir, out_dir, variables, dry_run=dry_run)

    # Auto-register in component_registry.yaml (canvas scaffolds only)
    reg_result: dict = {}
    if args.target == "canvas" and result.get("success") and not no_register:
        default_roles_str = variables.get("default_roles", "developer")
        default_roles = [r.strip() for r in default_roles_str.split(",") if r.strip()]
        reg_result = _register_component(
            kind="canvas",
            key=args.key,
            display_name=args.display_name,
            env_flag=variables["env_flag"],
            url_prefix=variables["url_prefix"],
            default_roles=default_roles,
            dry_run=dry_run,
        )
        result["registry"] = reg_result

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if dry_run:
            print(f"[dry-run] Would scaffold {args.display_name} to {out_dir}")
        else:
            print(f"Scaffolded {args.display_name} to {out_dir}")
        for f in result["rendered_files"]:
            prefix = "  (would create)" if dry_run else "  +"
            print(f"{prefix} {f}")
        if result.get("skipped_files"):
            for f in result["skipped_files"]:
                print(f"  - skipped: {f}")
        if result.get("errors"):
            print("Errors:")
            for e in result["errors"]:
                print(f"  ! {e}")
        if result.get("validation_failures"):
            print("Validation failures:")
            for e in result["validation_failures"]:
                print(f"  ! {e}")
        if reg_result:
            if reg_result.get("registered"):
                print(f"  Registered in args/component_registry.yaml (key={args.key})")
            elif reg_result.get("dry_run"):
                print(f"  [dry-run] Would register key={args.key} in args/component_registry.yaml")
            elif reg_result.get("reason"):
                print(f"  Registry: {reg_result['reason']}")

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
