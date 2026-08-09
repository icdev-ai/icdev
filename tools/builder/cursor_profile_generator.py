#!/usr/bin/env python3
# CUI // SP-CTI
"""Cursor AI Profile Generator — exports resolved ICDEV dev profiles as Cursor rules.

Bridge between ICDEV's 5-layer cascade dev profiles and Cursor AI editor configuration.
Supports two output formats:
  - .cursorrules   (project-root single file)
  - .mdc           (multi-file in .cursor/rules/ with globs)

Usage:
    python tools/builder/cursor_profile_generator.py --scope project --scope-id proj-123 --format cursorrules
    python tools/builder/cursor_profile_generator.py --scope project --scope-id proj-123 --format mdc
    python tools/builder/cursor_profile_generator.py --scope tenant --scope-id tenant-abc --format cursorrules --json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.builder.dev_profile_manager import resolve_profile

CONFIG_PATH = BASE_DIR / "args" / "cursor_export_config.yaml"


def _load_config():
    """Load cursor_export_config.yaml with minimal fallback."""
    try:
        import yaml

        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
    except ImportError:
        pass
    return {
        "export": {
            "formats": {
                "cursorrules": {
                    "header_template": "# ICDEV™ Cursor Profile — {{ scope }}/{{ scope_id }}\n",
                },
                "mdc": {
                    "header_template": "---\ndescription: ICDEV™ {{ scope }}/{{ scope_id }} rules\nglobs:\n  - '**/*'\nalwaysApply: true\n---\n",
                },
            },
            "dimension_map": {},
            "icdev_overrides": {},
        }
    }


def _render_template(template_str, context):
    """Lightweight Jinja2 rendering using string.Template as fallback."""
    try:
        from jinja2 import Template, UndefinedError, StrictUndefined

        t = Template(template_str, undefined=StrictUndefined)
        try:
            return t.render(**context)
        except UndefinedError as e:
            return f"<!-- template error: {e} -->"
    except ImportError:
        from string import Template

        t = Template(template_str)
        # Flatten one level for string.Template
        flat = {}
        for k, v in context.items():
            if isinstance(v, (str, int, float, bool)):
                flat[k] = str(v)
            elif isinstance(v, list):
                flat[k] = ", ".join(str(x) for x in v)
            elif isinstance(v, dict):
                flat[k] = json.dumps(v)
            else:
                flat[k] = str(v)
        return t.safe_substitute(flat)


def _evaluate_condition(condition, data):
    """Evaluate a dotted condition path against a dict."""
    parts = condition.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    # Truthy check
    if current is None:
        return False, current
    if isinstance(current, bool):
        return current, current
    if isinstance(current, (list, dict)):
        return len(current) > 0, current
    if isinstance(current, (int, float)):
        return True, current
    return bool(current), current


def _build_rules_for_dimension(dim_name, dim_data, dim_config):
    """Generate markdown rule lines for a single dimension."""
    rules = dim_config.get("rules", [])
    lines = []
    for rule in rules:
        condition = rule.get("condition", "")
        template = rule.get("template", "")
        ok, value = _evaluate_condition(condition, dim_data)
        if ok:
            try:
                rendered = _render_template(template, dim_data)
                if rendered.strip():
                    lines.append(rendered.strip())
            except Exception:
                pass
    return lines


def generate_cursorrules(scope, scope_id, resolved_profile, config):
    """Generate a .cursorrules string from a resolved profile."""
    fmt_config = config["export"]["formats"]["cursorrules"]
    dim_map = config["export"]["dimension_map"]
    icdev_overrides = config["export"].get("icdev_overrides", {})

    header_ctx = {
        "scope": scope,
        "scope_id": scope_id,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "dimensions": list(resolved_profile.keys()),
        "classification": resolved_profile.get("compliance", {}).get("classification_level", "none"),
    }
    output = _render_template(fmt_config.get("header_template", ""), header_ctx)
    output += "\n"

    # Sort dimensions by priority
    sorted_dims = sorted(
        dim_map.items(),
        key=lambda kv: kv[1].get("priority", 99),
    )

    for dim_name, dim_config in sorted_dims:
        if dim_name not in resolved_profile:
            continue
        section_title = dim_config.get("section", dim_name)
        dim_data = resolved_profile[dim_name]
        rules = _build_rules_for_dimension(dim_name, dim_data, dim_config)
        if rules:
            output += f"## {section_title}\n\n"
            for r in rules:
                output += f"- {r}\n"
            output += "\n"

    # ICDEV-specific overrides
    if icdev_overrides:
        output += "## ICDEV™ Guardrails\n\n"
        for k, v in icdev_overrides.items():
            if v:
                output += f"- {v.strip()}\n"
        output += "\n"

    return output.strip() + "\n"


def generate_mdc(scope, scope_id, resolved_profile, config):
    """Generate a .mdc string from a resolved profile."""
    fmt_config = config["export"]["formats"]["mdc"]
    dim_map = config["export"]["dimension_map"]
    icdev_overrides = config["export"].get("icdev_overrides", {})

    header_ctx = {
        "scope": scope,
        "scope_id": scope_id,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "dimensions": list(resolved_profile.keys()),
        "classification": resolved_profile.get("compliance", {}).get("classification_level", "none"),
    }
    output = _render_template(fmt_config.get("header_template", ""), header_ctx)
    output += "\n"

    sorted_dims = sorted(
        dim_map.items(),
        key=lambda kv: kv[1].get("priority", 99),
    )

    for dim_name, dim_config in sorted_dims:
        if dim_name not in resolved_profile:
            continue
        section_title = dim_config.get("section", dim_name)
        dim_data = resolved_profile[dim_name]
        rules = _build_rules_for_dimension(dim_name, dim_data, dim_config)
        if rules:
            output += f"# {section_title}\n\n"
            for r in rules:
                output += f"- {r}\n"
            output += "\n"

    if icdev_overrides:
        output += "# ICDEV™ Guardrails\n\n"
        for k, v in icdev_overrides.items():
            if v:
                output += f"- {v.strip()}\n"
        output += "\n"

    return output.strip() + "\n"


def generate(scope, scope_id, fmt="cursorrules", db_path=None):
    """Main entry point: resolve profile and emit Cursor rules."""
    config = _load_config()

    result = resolve_profile(scope, scope_id, db_path=db_path)
    if result.get("status") != "resolved":
        return {"error": "Failed to resolve profile", "details": result}

    resolved = result.get("resolved", {})

    if fmt == "cursorrules":
        content = generate_cursorrules(scope, scope_id, resolved, config)
        filename = f".{scope}_{scope_id}.cursorrules" if scope != "project" else ".cursorrules"
    elif fmt == "mdc":
        content = generate_mdc(scope, scope_id, resolved, config)
        safe_id = scope_id.replace("/", "_").replace("\\", "_")
        filename = f"icdev_{scope}_{safe_id}.mdc"
    else:
        return {"error": f"Unknown format: {fmt}"}

    return {
        "status": "generated",
        "format": fmt,
        "filename": filename,
        "scope": scope,
        "scope_id": scope_id,
        "content": content,
        "dimensions": list(resolved.keys()),
    }


def main():
    parser = argparse.ArgumentParser(description="Generate Cursor AI rules from ICDEV dev profiles")
    parser.add_argument("--scope", required=True, choices=["platform", "tenant", "program", "project", "user"])
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--format", default="cursorrules", choices=["cursorrules", "mdc"])
    parser.add_argument("--db-path")
    parser.add_argument("--output")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = generate(args.scope, args.scope_id, args.format, db_path=args.db_path)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if "content" in result:
            print(result["content"])
        else:
            print(json.dumps(result, indent=2))

    if args.output and "content" in result:
        out_path = Path(args.output)
        out_path.write_text(result["content"], encoding="utf-8", newline="")
        print(f"\n# Written to {out_path}")


if __name__ == "__main__":
    main()
