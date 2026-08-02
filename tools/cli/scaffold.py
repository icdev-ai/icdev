#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev scaffold` — generate ICDEV™ canvases, child apps, and core extensions from templates.

Subcommands:
  icdev scaffold canvas <key> --display-name "Name" [--flavor FLAVOR] [--out PATH]
  icdev scaffold child-app <key> --display-name "Name" [--flavor FLAVOR] [--canvases ...] [--out PATH]
  icdev scaffold core <key> --display-name "Name" [--flavor FLAVOR] [--out PATH]

Examples:
  icdev scaffold canvas demo --display-name "Demo Canvas" --out ./demo-canvas
  icdev scaffold canvas demo --display-name "Demo Canvas" --vars url_prefix=/demo
  icdev scaffold child-app my_lab --display-name "My Lab" --flavor ai-lab --canvases dic,slides
  icdev scaffold child-app my_lab --display-name "My Lab" --template ./custom-template
  icdev scaffold core notification_hub --display-name "Notification Hub" --env-flag ICDEV_NOTIF_ENABLED
  icdev scaffold core notification_hub --display-name "Notification Hub" --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
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


def _list_templates(emit_json: bool = False) -> int:
    """Print available template flavors per kind."""
    templates_root = BASE_DIR / "data" / "templates"
    result = {}
    for kind_dir in ("canvases", "child_apps", "core_extensions"):
        kind_path = templates_root / kind_dir
        if not kind_path.exists():
            result[kind_dir] = []
            continue
        flavors = []
        for item in sorted(kind_path.iterdir()):
            if item.is_dir() and (item / "manifest.yaml").exists():
                flavors.append(item.name)
        result[kind_dir] = flavors
    if emit_json:
        print(json.dumps(result, indent=2))
    else:
        for kind_dir, flavors in result.items():
            kind_label = {"canvases": "canvas", "child_apps": "child-app", "core_extensions": "core"}[kind_dir]
            print(f"\n{kind_label}:")
            for f in flavors:
                print(f"  {f}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="icdev scaffold",
        description=__doc__,
    )
    sub = parser.add_subparsers(dest="target", required=True)

    # Template discovery
    list_tmpl = sub.add_parser("list-templates", help="List available template flavors")
    list_tmpl.add_argument("--json", action="store_true", help="Emit JSON output")

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

    core = sub.add_parser("core", help="Scaffold a new core extension")
    core.add_argument("key", help="Short extension key (e.g. notification_hub)")
    core.add_argument("--display-name", required=True, help="Human-facing extension name")
    core.add_argument("--env-flag", default=None, help="Primary .env toggle (default: ICDEV_<KEY>_ENABLED)")
    core.add_argument("--url-prefix", default=None, help="Flask url_prefix (default: /<key>, empty=no-web)")
    core.add_argument("--flavor", default="standard", help="Built-in core-extension flavor (default: standard)")
    core.add_argument("--template", default=None, help="Template directory path (overrides --flavor)")
    core.add_argument("--out", default=None, help="Output directory (default: ./<key>-ext)")
    core.add_argument("--vars", nargs="*", default=[], help="Extra variable overrides as key=value")
    core.add_argument("--dry-run", action="store_true", help="Preview what would be generated without writing files")
    core.add_argument("--no-register", action="store_true", help="Skip auto-registration in component_registry.yaml")
    core.add_argument("--json", action="store_true", help="Emit JSON result")

    # docmod pack — a document-currency pack for a new domain. Unlike the other
    # targets this writes IN PLACE by default (its output is 1-2 config files
    # that must live in args/docmod/ to be discovered) and never overwrites.
    pack = sub.add_parser("docmod-pack", help="Scaffold a docmod document-currency pack for a new domain")
    pack.add_argument("key", help="Pack id / domain key (e.g. safety_standards)")
    pack.add_argument("--display-name", default=None, help="Human-facing pack name (default: derived from key)")
    pack.add_argument(
        "--flavor", default="rulebook",
        help="rulebook = YAML only, no Python (default) | catalog = table-driven, generates a Python stub",
    )
    pack.add_argument("--entity-type", default="term", help="KG entity type the pack extracts (default: term)")
    pack.add_argument("--finding-type", default="deprecated_tech",
                      help="Default finding_type; must be in constants.FINDING_TYPES (rulebook flavor)")
    pack.add_argument("--evidence-table", default=None, help="Table holding the domain's truth (catalog flavor)")
    pack.add_argument("--template", default=None, help="Template directory path (overrides --flavor)")
    pack.add_argument("--out", default=None, help="Output root (default: the repo root — writes in place)")
    pack.add_argument("--vars", nargs="*", default=[], help="Extra variable overrides as key=value")
    pack.add_argument("--dry-run", action="store_true", help="Preview what would be generated without writing files")
    pack.add_argument("--json", action="store_true", help="Emit JSON result")

    return parser


def _scaffold_docmod_pack(args) -> int:
    """Generate a docmod pack. Separate from main()'s canvas flow — a pack has
    no env flag, url_prefix or component_registry entry."""
    key = args.key
    display_name = args.display_name or key.replace("_", " ").replace("-", " ").title()

    template_dir = (
        BASE_DIR / args.template if args.template
        else BASE_DIR / "data" / "templates" / "docmod_packs" / args.flavor
    )
    if not template_dir.exists():
        print(f"Template not found: {template_dir}", file=sys.stderr)
        return 2

    variables: dict[str, str] = {
        "key": key,
        "display_name": display_name,
        "entity_type": args.entity_type,
        "finding_type": args.finding_type,
        # Only the catalog flavor declares these; harmless extras for rulebook.
        "class_name": "".join(p.title() for p in key.replace("-", "_").split("_")) + "Pack",
        "evidence_table": args.evidence_table or "",
    }
    for raw in args.vars:
        if "=" not in raw:
            print(f"Invalid --vars entry (expected key=value): {raw}", file=sys.stderr)
            return 2
        k, v = raw.split("=", 1)
        variables[k.strip()] = v.strip()

    if args.flavor == "catalog" and not variables["evidence_table"]:
        print(
            "--evidence-table is required for the catalog flavor "
            "(the table holding this domain's deterministic truth).",
            file=sys.stderr,
        )
        return 2

    # In place by default: a pack is discovered by its location under
    # args/docmod/, so a staging dir would just mean a manual copy.
    out_dir = Path(args.out).resolve() if args.out else BASE_DIR

    # skip_existing so re-running never clobbers a rulebook someone has written.
    # The other scaffold targets omit this and silently overwrite; don't inherit
    # that here, where the output lands directly in the repo.
    result = render_tree(
        template_dir, out_dir, variables,
        skip_existing=True, dry_run=getattr(args, "dry_run", False),
    )

    payload = {
        "target": "docmod-pack",
        "pack_id": key,
        "flavor": args.flavor,
        "out_dir": str(out_dir),
        **result,
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, default=str))
    else:
        ok = result.get("success")
        print(f"{'Generated' if ok else 'FAILED'} docmod pack '{key}' ({args.flavor}) -> {out_dir}")
        for f in result.get("files", []) or []:
            print(f"  {f}")
        if result.get("skipped"):
            print("  skipped (already exist):")
            for f in result["skipped"]:
                print(f"    {f}")
        if ok and not getattr(args, "dry_run", False):
            print("\nNext:")
            if args.flavor == "rulebook":
                print(f"  1. write rules in args/docmod/rulebook_{key}.yaml")
            else:
                print(f"  1. implement evaluate() in tools/doc_modernization/packs/{key}.py")
            print(f"  2. set enabled: true in args/docmod/packs/{key}.yaml")
            print("  3. the next docmod sweep picks it up (pack_loader auto-discovers)")
    return 0 if result.get("success") else 1


def _derive_env_flag(key: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    return f"ICDEV_{key.upper()}_ENABLED"


def _derive_url_prefix(key: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    return f"/{key.replace('_', '-')}"



# CLI kind spelling -> args/component_registry.yaml `kind:` vocabulary.
_REGISTRY_KIND = {"canvas": "canvas", "child-app": "child_app", "core": "core_extension"}


def _insert_into_components(raw_text: str, rendered_entry: str) -> str:
    """Splice *rendered_entry* onto the end of the ``components:`` list.

    Text-level on purpose: the registry is hand-maintained and its comments are
    load-bearing documentation, so the file is never round-tripped through
    ``yaml.dump``. The components list runs to EOF unless another top-level key
    follows it, so the insertion point is the last line before that key —
    skipping back over the comment block that documents it, which belongs to the
    FOLLOWING key and must stay attached to it.

    Line endings are preserved by splitting with ``keepends`` and never
    re-joining with a literal ``"\\n"``.
    """
    lines = raw_text.splitlines(keepends=True)
    eol = "\n"
    for line in lines:
        if line.endswith("\r\n"):
            eol = "\r\n"
            break
        if line.endswith("\n"):
            break

    # First top-level key AFTER `components:` bounds the list.
    top_level = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:")
    start = next(
        (i for i, ln in enumerate(lines) if ln.startswith("components:")), None
    )
    if start is None:
        raise ValueError("component_registry.yaml has no top-level 'components:' key")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if top_level.match(lines[i]):
            end = i
            break

    # Walk back over the comment/blank block introducing the next key.
    while end > start + 1:
        prev = lines[end - 1].strip()
        if prev.startswith("#") or not prev:
            end -= 1
        else:
            break

    block = [ln if ln.endswith(("\n", "\r\n")) else ln + eol
             for ln in rendered_entry.replace("\r\n", "\n").splitlines(keepends=True)]
    if eol == "\r\n":
        block = [ln.replace("\n", "\r\n").replace("\r\r\n", "\r\n") for ln in block]

    # Guarantee the preceding line is terminated so the splice cannot glue onto it.
    if end > 0 and lines[end - 1] and not lines[end - 1].endswith(("\n", "\r\n")):
        lines[end - 1] += eol

    return "".join(lines[:end] + block + lines[end:])


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

    raw_text = REGISTRY_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(raw_text) or {}

    # The registry has ONE component list — `components:` — and the entry's own
    # `kind` field distinguishes canvas / child_app / core_extension.
    # ComponentRegistry._load_components() reads data["components"] and nothing
    # else, so writing to per-kind top-level keys registered a component into a
    # list that is never loaded (the scaffolded component silently did not exist).
    entries = data.get("components") or []

    # Skip if already registered
    if any(e.get("key") == key for e in entries):
        return {"registered": False, "reason": f"{key} already in registry"}

    cli_name = key.replace("_", "-")

    if kind == "canvas":
        module = f"tools.{key}_canvas.blueprint"
        blueprint_attr = f"create_{key}_blueprint"
        nav_section = "Canvases"
        completeness = {
            "template": f"tools/dashboard/templates/{key}/page.html",
            "blueprint": True,
            "constants": f"tools/{key}_canvas/constants.py",
            "db_migration": f"tools/{key}_canvas/db",
            "iqe_adapter": True,
            "nav_link": True,
            "seed_queries": f"context/iqe/queries/{key}/",
        }
    elif kind == "core":
        module = f"tools.{key}.blueprint"
        blueprint_attr = f"create_{key.replace('-', '_')}_blueprint"
        nav_section = "System"
        completeness = {
            "blueprint": True,
            "constants": f"tools/{key}/constants.py",
        }
    else:  # child-app
        module = f"tools.{key}.blueprint"
        blueprint_attr = f"create_{key.replace('-', '_')}_app"
        nav_section = "Applications"
        completeness = {}

    new_entry = {
        "key": key,
        # CLI kind -> registry kind. The CLI spells it "child-app"; the registry
        # vocabulary is child_app (Component.kind, and iter_child_apps() filters
        # on kind="child_app"), so writing the hyphenated form registered a child
        # app that iter_child_apps() could never return.
        "kind": _REGISTRY_KIND.get(kind, kind),
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
        "min_tier": "community",
        "default_roles": default_roles,
        "nav": {
            "section": nav_section,
            "label": display_name,
            "links": [{"label": "Overview", "href": f"{url_prefix}/"}] if url_prefix else [],
        },
        "iqe": {
            "adapter_module": f"tools.iqe.adapters.{key}",
            "collections": [f"{key}.items", f"{key}.events"],
        },
        "completeness": completeness,
    }

    if dry_run:
        return {"registered": False, "dry_run": True, "would_add": new_entry}

    # Append as TEXT, not by re-dumping the parsed document. args/component_registry.yaml
    # is hand-maintained and carries ~190 comment lines (section banners, per-entry
    # rationale, the iqe_path_canvas ordering rules); yaml.dump(data) would silently
    # delete every one of them.
    rendered = yaml.dump(
        [new_entry], default_flow_style=False, allow_unicode=True, sort_keys=False, indent=2
    )
    REGISTRY_PATH.write_text(
        _insert_into_components(raw_text, rendered), encoding="utf-8", newline=""
    )

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

    return {"registered": True, "key": key, "kind": new_entry["kind"]}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.target == "list-templates":
        return _list_templates(emit_json=getattr(args, "json", False))

    # Handled before the canvas flow below: a docmod pack has no env flag,
    # url_prefix or component_registry entry, so none of that applies.
    if args.target == "docmod-pack":
        return _scaffold_docmod_pack(args)

    _kind_to_dir = {"canvas": "canvases", "child-app": "child_apps", "core": "core_extensions"}
    if args.flavor:
        kind_dir = _kind_to_dir.get(args.target, "canvases")
        template_dir = BASE_DIR / "data" / "templates" / kind_dir / args.flavor
    elif getattr(args, "template", None):
        template_dir = BASE_DIR / args.template
    else:
        # Default templates per kind
        _defaults = {"canvas": "minimal", "child-app": "minimal", "core": "standard"}
        kind_dir = _kind_to_dir.get(args.target, "canvases")
        template_dir = BASE_DIR / "data" / "templates" / kind_dir / _defaults.get(args.target, "standard")
    if not template_dir.exists():
        print(f"Template not found: {template_dir}", file=sys.stderr)
        return 2

    _default_suffixes = {"canvas": "canvas", "child-app": "app", "core": "ext"}
    default_suffix = _default_suffixes.get(args.target, "component")
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

    # Auto-register in component_registry.yaml (canvas and core scaffolds)
    reg_result: dict = {}
    if args.target in ("canvas", "core") and result.get("success") and not no_register:
        default_roles_str = variables.get("default_roles", "developer" if args.target == "canvas" else "admin")
        default_roles = [r.strip() for r in default_roles_str.split(",") if r.strip()]
        reg_result = _register_component(
            kind=args.target,
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
