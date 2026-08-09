# CUI // SP-CTI
"""CLI Generator — produces argparse CLI wrappers from spec files.

Generates standalone CLI entry-points for ICDEV™ tools so they can be
invoked from shell scripts, CI/CD pipelines, and the dashboard
CLI-generator panel.

Architecture decision: deterministic code generation (no LLM).
"""

from __future__ import annotations

import pathlib

from tools.common.helpers import now_iso


def generate(
    spec_path: str,
    output_dir: str | None = None,
    name: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Generate a CLI wrapper from a tool spec file.

    Parameters
    ----------
    spec_path : str
        Path to the tool spec (Python module or YAML definition).
    output_dir : str | None
        Directory for generated CLI script.  Defaults to ``<spec_dir>/cli/``.
    name : str | None
        Override the generated CLI script name.
    dry_run : bool
        If True, return what *would* be generated without writing files.

    Returns
    -------
    dict
        ``{status, spec_path, output_path, name, dry_run, generated_at}``
    """
    spec = pathlib.Path(spec_path)
    if not spec.exists():
        return {
            "status": "error",
            "error": f"Spec file not found: {spec_path}",
        }

    resolved_name = name or spec.stem + "_cli"
    resolved_output_dir = pathlib.Path(output_dir) if output_dir else spec.parent / "cli"

    output_path = resolved_output_dir / f"{resolved_name}.py"

    if dry_run:
        return {
            "status": "dry_run",
            "spec_path": str(spec),
            "output_path": str(output_path),
            "name": resolved_name,
            "dry_run": True,
            "generated_at": now_iso(),
        }

    # Ensure output directory exists
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    # Generate minimal CLI wrapper
    cli_content = _render_cli_template(resolved_name, str(spec))
    output_path.write_text(cli_content, encoding="utf-8", newline="")

    return {
        "status": "ok",
        "spec_path": str(spec),
        "output_path": str(output_path),
        "name": resolved_name,
        "dry_run": False,
        "generated_at": now_iso(),
    }


def _render_cli_template(name: str, spec_path: str) -> str:
    """Render a minimal argparse CLI wrapper."""
    return f'''\
# CUI // SP-CTI
# Auto-generated CLI wrapper for {name}
# Spec: {spec_path}
# Generated: {now_iso()}

"""CLI entry-point for {name}."""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="{name} CLI")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    result = {{"status": "ok", "tool": "{name}"}}

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{name}: ok")


if __name__ == "__main__":
    main()
'''


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="CLI Generator")
    parser.add_argument("--spec-path", required=True, help="Path to tool spec")
    parser.add_argument("--output-dir", default=None, help="Output directory")
    parser.add_argument("--name", default=None, help="CLI script name")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = generate(
        spec_path=args.spec_path,
        output_dir=args.output_dir,
        name=args.name,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Status: {result.get('status', 'unknown')}")
        if result.get("output_path"):
            print(f"Output: {result['output_path']}")
