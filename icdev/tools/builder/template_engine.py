# CUI // SP-CTI
"""Template Engine — deterministic Jinja2 file-tree generation for ICDEV™ canvases and child apps.

The engine reads a YAML manifest that describes a template (variables, files, validators)
and renders a concrete file tree to a target directory. It is the foundation for the
canvas/child-app scaffolding priorities approved in the enterprise-configurable-templates plan.

Example manifest (data/templates/canvases/minimal/manifest.yaml)::

    name: minimal
    description: Minimal canvas skeleton
    variables:
      key:
        type: string
        required: true
      display_name:
        type: string
        required: true
      url_prefix:
        type: string
        default: "/{{ key }}"
    files:
      - src: blueprint.py.j2
        dest: "tools/{{ key }}_canvas/blueprint.py"
      - src: constants.py.j2
        dest: "tools/{{ key }}_canvas/constants.py"
      - src: page.html.j2
        dest: "tools/dashboard/templates/{{ key }}/page.html"
    validators:
      - type: file_exists
        path: "tools/{{ key }}_canvas/blueprint.py"
      - type: python_syntax
        path: "tools/{{ key }}_canvas/blueprint.py"

CLI::

    python tools/builder/template_engine.py --template data/templates/canvases/minimal \
        --out /tmp/my-canvas --vars key=my_canvas display_name="My Canvas"
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

# Repo root, resolved from this file rather than cwd: render_tree is called from
# the CLI, from tests and from worktrees, so cwd is not a reliable anchor.
REPO_ROOT = Path(__file__).resolve().parents[2]

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.template_engine")

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _import_jinja2() -> Any:
    """Return the jinja2 module, or raise a clear error if missing."""
    try:
        import jinja2

        return jinja2
    except ImportError as exc:
        raise RuntimeError(
            "Jinja2 is required for the template engine. Install pyyaml+jinja2 or run in a venv."
        ) from exc


def _import_yaml() -> Any:
    """Return the yaml module, or raise a clear error if missing."""
    try:
        import yaml

        return yaml
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required for the template engine. Install pyyaml+jinja2 or run in a venv."
        ) from exc


def load_manifest(template_dir: Path) -> dict[str, Any]:
    """Load and return the manifest.yaml from a template directory."""
    yaml = _import_yaml()
    manifest_path = template_dir / "manifest.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Template manifest not found: {manifest_path}")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Template manifest {manifest_path} must be a YAML mapping")
    return data


def _resolve_variables(
    manifest_variables: dict[str, Any], overrides: dict[str, str]
) -> dict[str, Any]:
    """Merge manifest variable defaults with user-provided overrides.

    Returns a dict of resolved values. Raises ValueError for missing required
    variables or type mismatches. Default values may reference other variables
    using Jinja2 syntax (e.g. ``default: "/{{ key }}"``).
    """
    resolved: dict[str, Any] = {}
    for name, spec in manifest_variables.items():
        if name in overrides:
            value = overrides[name]
        elif "default" in spec:
            value = spec["default"]
        elif spec.get("required"):
            raise ValueError(f"Missing required template variable: {name}")
        else:
            value = None

        var_type = spec.get("type", "string")
        if var_type == "boolean":
            if isinstance(value, str):
                value = value.lower() in ("true", "1", "yes", "on")
            value = bool(value)
        elif var_type == "integer":
            value = int(value)
        elif var_type == "string":
            value = str(value) if value is not None else ""

        resolved[name] = value

    # Second pass: render any Jinja2 references inside string values so defaults
    # like "tools.{{ key }}_canvas" resolve correctly.
    for name, value in list(resolved.items()):
        if isinstance(value, str) and ("{{" in value or "{%" in value):
            resolved[name] = _render_string(value, resolved)

    return resolved


def _render_string(template_str: str, context: dict[str, Any]) -> str:
    """Render a single Jinja2 string with the given context."""
    jinja2 = _import_jinja2()
    env = jinja2.Environment(autoescape=False)  # nosec B701 — renders code templates, not HTML; autoescape would corrupt syntax
    return env.from_string(template_str).render(context)


def _eval_when(when_expr: str | None, context: dict[str, Any]) -> bool:
    """Evaluate a ``when`` expression from a manifest file entry.

    Returns True (include file) if when is absent or evaluates to a truthy string.
    Falsy strings: 'false', '0', 'no', '', 'none'.
    """
    if when_expr is None:
        return True
    rendered = _render_string(str(when_expr), context)
    return rendered.lower() not in ("false", "0", "no", "", "none")


def render_tree(
    template_dir: Path,
    out_dir: Path,
    variables: dict[str, str],
    *,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Render a template tree into ``out_dir``.

    Args:
        template_dir: Directory containing ``manifest.yaml`` and template files.
        out_dir: Destination directory (created if missing).
        variables: User-provided variable overrides.
        skip_existing: If True, leave existing files untouched instead of overwriting.

    Returns:
        Result dict with ``manifest``, ``variables``, ``rendered_files``,
        ``skipped_files``, ``errors``.  When ``dry_run`` is True, no files are
        written but ``rendered_files`` lists what *would* be created.
    """
    manifest = load_manifest(template_dir)
    spec_vars = manifest.get("variables", {})
    context = _resolve_variables(spec_vars, variables)

    rendered: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    for file_entry in manifest.get("files", []):
        src_name = file_entry.get("src")
        dest_tmpl = file_entry.get("dest")
        if not src_name or not dest_tmpl:
            errors.append(f"Invalid file entry (missing src or dest): {file_entry}")
            continue

        # Conditional inclusion: skip file if `when` evaluates to falsy
        if not _eval_when(file_entry.get("when"), context):
            skipped.append(_render_string(dest_tmpl, context))
            continue

        dest_rel = _render_string(dest_tmpl, context)
        dest_path = out_dir / dest_rel

        if skip_existing and dest_path.exists():
            skipped.append(dest_rel)
            continue

        src_path = template_dir / src_name

        if not src_path.exists():
            errors.append(f"Template source file not found: {src_path}")
            continue

        src_text = src_path.read_text(encoding="utf-8")
        if src_name.endswith(".j2"):
            rendered_text = _render_string(src_text, context)
            dest_name = dest_rel
        else:
            rendered_text = src_text
            dest_name = dest_rel

        # If the destination still carries .j2, drop it.
        if dest_name.endswith(".j2"):
            dest_name = dest_name[:-3]
            dest_path = out_dir / dest_name

        if not dry_run:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_text(rendered_text, encoding="utf-8")
        rendered.append(dest_name)

    # `copy:` — verbatim copies of REAL repo files (src is repo-root-relative),
    # for code a generated tree must inherit rather than re-declare. The
    # anti-hallucination grounding modules are the motivating case: templating
    # them would fork live TRUST code into a .j2 that silently drifts from the
    # original. Copying keeps one source of truth.
    for copy_entry in manifest.get("copy", []):
        src_rel = copy_entry.get("src")
        dest_tmpl = copy_entry.get("dest") or src_rel
        if not src_rel:
            errors.append(f"Invalid copy entry (missing src): {copy_entry}")
            continue
        if not _eval_when(copy_entry.get("when"), context):
            skipped.append(_render_string(dest_tmpl, context))
            continue

        dest_rel = _render_string(dest_tmpl, context)
        dest_path = out_dir / dest_rel
        if skip_existing and dest_path.exists():
            skipped.append(dest_rel)
            continue

        src_path = REPO_ROOT / src_rel
        if not src_path.exists():
            # An error, not a warning: a manifest asking for a file that isn't
            # there means the generated tree is missing something it declared
            # it needs.
            errors.append(f"Copy source not found: {src_path}")
            continue

        if not dry_run:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_path)
        rendered.append(dest_rel)

    result = {
        "manifest": manifest,
        "variables": context,
        "rendered_files": rendered,
        "skipped_files": skipped,
        "errors": errors,
        "dry_run": dry_run,
    }

    # Validators only run on real (non-dry-run) renders
    if not dry_run:
        validators = manifest.get("validators", [])
        validation_failures = _run_validators(validators, out_dir, context)
    else:
        validation_failures = []
    result["validation_failures"] = validation_failures
    result["success"] = not errors and not validation_failures

    return result


def _run_validators(
    validators: list[dict[str, Any]], out_dir: Path, context: dict[str, Any]
) -> list[str]:
    """Run manifest validators against the rendered tree."""
    failures: list[str] = []
    for validator in validators:
        vtype = validator.get("type")
        path_tmpl = validator.get("path", "")
        path_rel = _render_string(path_tmpl, context)
        path = out_dir / path_rel

        if vtype == "file_exists":
            if not path.exists():
                failures.append(f"file_exists failed: {path_rel}")
        elif vtype == "python_syntax":
            if not path.exists():
                failures.append(f"python_syntax failed (missing): {path_rel}")
                continue
            try:
                ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                failures.append(f"python_syntax failed: {path_rel} ({exc})")
        elif vtype == "env_var":
            env_flag = validator.get("env_flag", "")
            if env_flag and not os.environ.get(env_flag):
                failures.append(f"env_var failed: {env_flag} not set")
        else:
            failures.append(f"Unknown validator type: {vtype}")

    return failures


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="icdev-template-engine",
        description="Render an ICDEV template tree to a target directory.",
    )
    parser.add_argument("--template", required=True, help="Path to template directory")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument(
        "--vars",
        nargs="*",
        default=[],
        help="Variable overrides as key=value pairs",
    )
    parser.add_argument("--skip-existing", action="store_true", help="Do not overwrite existing files")
    parser.add_argument("--json", action="store_true", help="Emit JSON result")
    return parser


def _parse_vars(raw_vars: list[str]) -> dict[str, str]:
    """Parse key=value strings into a dict."""
    out: dict[str, str] = {}
    for item in raw_vars:
        if "=" not in item:
            raise ValueError(f"Variable must be key=value: {item}")
        key, value = item.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    template_dir = Path(args.template).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    variables = _parse_vars(args.vars)

    result = render_tree(template_dir, out_dir, variables, skip_existing=args.skip_existing)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Template: {template_dir}")
        print(f"Output: {out_dir}")
        print(f"Rendered: {len(result['rendered_files'])}")
        for f in result["rendered_files"]:
            print(f"  + {f}")
        if result["skipped_files"]:
            print(f"Skipped: {len(result['skipped_files'])}")
        if result["errors"]:
            print(f"Errors: {len(result['errors'])}")
            for e in result["errors"]:
                print(f"  ! {e}")
        if result.get("validation_failures"):
            print(f"Validation failures: {len(result['validation_failures'])}")
            for e in result["validation_failures"]:
                print(f"  ! {e}")

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
