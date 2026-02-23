#!/usr/bin/env python3
# CUI // SP-CTI
"""CLI Harmonization Fixer — bulk-apply ICDEV CLI standards.

Applies two mechanical transformations to tools with argparse:

1. --project → --project-id: Renames bare --project flags to --project-id
   while keeping --project as a deprecated alias (dest="project_id").
   Also updates args.project → args.project_id references.

2. --json flag: Adds standard --json flag to tools that lack it.

Usage:
    python tools/compat/cli_harmonizer.py --fix project-naming --dry-run
    python tools/compat/cli_harmonizer.py --fix project-naming --apply
    python tools/compat/cli_harmonizer.py --fix json-flag --dry-run
    python tools/compat/cli_harmonizer.py --fix json-flag --apply
    python tools/compat/cli_harmonizer.py --fix all --apply
    python tools/compat/cli_harmonizer.py --fix all --apply --json

This tool uses only Python stdlib (air-gap safe).
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"

# Files to skip (not CLI tools, or special cases)
SKIP_FILES = {
    "tools/dashboard/app.py",         # Flask web server
    "tools/saas/api_gateway.py",      # Flask web server
    "tools/saas/portal/app.py",       # Flask web server
    "tools/saas/db/pg_schema.py",     # Schema definition module
    "tools/db/init_icdev_db.py",      # DB init script (has --json already via convention)
    "tools/compat/cli_harmonizer.py", # This file
}


def find_argparse_tools() -> List[Path]:
    """Find all Python files under tools/ that use argparse + __main__."""
    results = []
    for py_file in sorted(TOOLS_DIR.rglob("*.py")):
        if py_file.name.startswith("__"):
            continue
        rel = str(py_file.relative_to(PROJECT_ROOT)).replace("\\", "/")
        if rel in SKIP_FILES or "/tests/" in rel:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "ArgumentParser" in content and '__name__' in content and '"__main__"' in content:
            results.append(py_file)
    return results


def fix_project_naming(py_file: Path, dry_run: bool = True) -> Optional[str]:
    """Fix bare --project to --project-id with backward-compat alias.

    Returns description of change, or None if no change needed.
    """
    content = py_file.read_text(encoding="utf-8")

    # Check if file has bare --project (not --project-id, --project-dir, --project-path)
    if not re.search(r'''add_argument\(\s*['"]--project['"]''', content):
        return None

    # Already has --project-id? Skip.
    if re.search(r'''add_argument\(\s*['"]--project-id['"]''', content):
        return None

    # Transform: add_argument("--project", ...) → add_argument("--project-id", "--project", dest="project_id", ...)
    new_content = content

    # Pattern: add_argument("--project", ...) or add_argument('--project', ...)
    # We need to insert "--project" as alias and dest="project_id"
    def replace_project_arg(m):
        full_match = m.group(0)
        quote = m.group(1)  # " or '

        # Check if dest is already specified
        if "dest=" in full_match:
            # Just rename the flag, keep existing dest
            return full_match.replace(
                f"add_argument({quote}--project{quote}",
                f"add_argument({quote}--project-id{quote}, {quote}--project{quote}",
            )

        # Insert --project as alias and add dest="project_id"
        # Find the closing paren position is tricky, so let's use a simpler approach:
        # Replace the flag name and add dest after it
        result = full_match.replace(
            f"add_argument({quote}--project{quote}",
            f"add_argument({quote}--project-id{quote}, {quote}--project{quote}",
        )

        # Add dest="project_id" before the closing paren
        # Find last ) and insert before it
        last_paren = result.rfind(")")
        if last_paren > 0:
            before = result[:last_paren].rstrip()
            if before.endswith(","):
                result = before + f" dest={quote}project_id{quote})" + result[last_paren + 1:]
            else:
                result = before + f", dest={quote}project_id{quote})" + result[last_paren + 1:]

        return result

    # Match the full add_argument("--project", ...) call
    # This regex captures the full call including multiline
    pattern = re.compile(
        r'''add_argument\(\s*(['"])--project\1[^)]*\)''',
        re.DOTALL,
    )

    new_content = pattern.sub(replace_project_arg, new_content)

    # Replace args.project with args.project_id (but not args.project_id, args.project_dir, etc.)
    new_content = re.sub(
        r'\bargs\.project\b(?!_)',
        'args.project_id',
        new_content,
    )

    if new_content == content:
        return None

    if not dry_run:
        py_file.write_text(new_content, encoding="utf-8")

    rel = str(py_file.relative_to(PROJECT_ROOT)).replace("\\", "/")
    return f"Renamed --project → --project-id in {rel}"


def fix_json_flag(py_file: Path, dry_run: bool = True) -> Optional[str]:
    """Add --json flag to tools that lack it.

    Returns description of change, or None if no change needed.
    """
    content = py_file.read_text(encoding="utf-8")

    # Already has --json?
    if '"--json"' in content or "'--json'" in content:
        return None

    # Find the last add_argument line to insert after it
    lines = content.split("\n")
    last_add_arg_idx = None
    parser_var = "parser"  # default

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Detect parser variable name
        if "ArgumentParser" in stripped:
            m = re.match(r'\s*(\w+)\s*=\s*\w*\.?ArgumentParser', stripped)
            if m:
                parser_var = m.group(1)
        # Track last add_argument call
        if "add_argument" in stripped and not stripped.startswith("#"):
            last_add_arg_idx = i

    if last_add_arg_idx is None:
        return None

    # Determine indentation from the last add_argument line
    indent = ""
    for ch in lines[last_add_arg_idx]:
        if ch in " \t":
            indent += ch
        else:
            break

    # Insert --json flag after the last add_argument
    json_line = f'{indent}{parser_var}.add_argument("--json", action="store_true", dest="json_output", help="JSON output")'
    lines.insert(last_add_arg_idx + 1, json_line)

    new_content = "\n".join(lines)

    if not dry_run:
        py_file.write_text(new_content, encoding="utf-8")

    rel = str(py_file.relative_to(PROJECT_ROOT)).replace("\\", "/")
    return f"Added --json flag to {rel}"


def main():
    parser = argparse.ArgumentParser(
        description="Bulk-apply ICDEV CLI harmonization standards"
    )
    parser.add_argument(
        "--fix", required=True,
        choices=["project-naming", "json-flag", "all"],
        help="Which fix to apply",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    parser.add_argument("--apply", action="store_true", help="Apply changes to files")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("Error: specify --dry-run or --apply", file=sys.stderr)
        sys.exit(1)

    dry_run = not args.apply
    tools = find_argparse_tools()
    results: Dict[str, List[str]] = {"project_naming": [], "json_flag": []}

    if args.fix in ("project-naming", "all"):
        for tool in tools:
            change = fix_project_naming(tool, dry_run=dry_run)
            if change:
                results["project_naming"].append(change)

    if args.fix in ("json-flag", "all"):
        for tool in tools:
            change = fix_json_flag(tool, dry_run=dry_run)
            if change:
                results["json_flag"].append(change)

    if args.json_output:
        output = {
            "status": "ok",
            "dry_run": dry_run,
            "project_naming_fixes": len(results["project_naming"]),
            "json_flag_fixes": len(results["json_flag"]),
            "details": results,
        }
        print(json.dumps(output, indent=2))
    else:
        mode = "DRY RUN" if dry_run else "APPLIED"
        print(f"\n=== CLI Harmonizer ({mode}) ===\n")
        for category, changes in results.items():
            if changes:
                print(f"  {category} ({len(changes)} fixes):")
                for change in changes:
                    print(f"    {change}")
                print()
        total = sum(len(v) for v in results.values())
        print(f"  Total: {total} changes {'previewed' if dry_run else 'applied'}")

    sys.exit(0)


if __name__ == "__main__":
    main()
