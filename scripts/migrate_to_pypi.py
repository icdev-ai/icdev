#!/usr/bin/env python3
"""Migrate ICDEV™ to PyPI-ready package structure.

Moves tools/ subdirectories into icdev/tools/, copies data directories
into icdev/data/, and updates import statements across all Python files.

Usage:
    python scripts/migrate_to_pypi.py --dry-run    # Preview changes
    python scripts/migrate_to_pypi.py --execute     # Execute migration
"""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Modules to EXCLUDE from the PyPI package
EXCLUDED_TOOL_DIRS = {
    "saas",
    "govcon",
    "rfx",
    "marketplace",
    "creative",
    "gateway",
    "playground",
}

# Data directories to copy into icdev/data/
DATA_DIRS = ["args", "context", "goals", "hardprompts", "features", "docs"]

# Data files to EXCLUDE from icdev/data/
EXCLUDED_DATA_FILES = {
    "args/govcon_config.yaml",
    "args/marketplace_config.yaml",
    "args/creative_config.yaml",
    "args/remote_gateway_config.yaml",
}

EXCLUDED_DATA_DIRS = {
    "context/govcon",
    "context/marketplace",
}

EXCLUDED_GOAL_FILES = {
    "goals/govcon_intelligence.md",
    "goals/govcon_product_descriptions.md",
    "goals/cpmp_workflow.md",
    "goals/marketplace.md",
    "goals/remote_command_gateway.md",
    "goals/creative_engine.md",
    "goals/saas_multi_tenancy.md",
}


def move_tools(dry_run: bool) -> list[str]:
    """Move tools/ subdirectories into icdev/tools/."""
    actions = []
    src_tools = PROJECT_ROOT / "tools"
    dst_tools = PROJECT_ROOT / "icdev" / "tools"

    if not src_tools.is_dir():
        print(f"ERROR: {src_tools} does not exist")
        sys.exit(1)

    dst_tools.mkdir(parents=True, exist_ok=True)

    # Copy tools/__init__.py if it exists (some tools/ have one)
    init_src = src_tools / "__init__.py"
    init_dst = dst_tools / "__init__.py"
    if init_src.exists():
        actions.append(f"COPY {init_src} -> {init_dst}")
        if not dry_run:
            shutil.copy2(init_src, init_dst)
    else:
        # Create a minimal __init__.py
        actions.append(f"CREATE {init_dst}")
        if not dry_run:
            init_dst.write_text('"""ICDEV™ Tools -- the T in FORGE."""\n')

    for item in sorted(src_tools.iterdir()):
        if item.name.startswith("__"):
            continue
        if item.name.startswith("."):
            continue
        if item.is_dir() and item.name in EXCLUDED_TOOL_DIRS:
            actions.append(f"SKIP (excluded) {item}")
            continue
        if item.is_dir():
            dst = dst_tools / item.name
            actions.append(f"COPY DIR {item} -> {dst}")
            if not dry_run:
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(item, dst)
        elif item.is_file() and item.suffix == ".py":
            dst = dst_tools / item.name
            actions.append(f"COPY {item} -> {dst}")
            if not dry_run:
                shutil.copy2(item, dst)

    return actions


def copy_data_dirs(dry_run: bool) -> list[str]:
    """Copy data directories into icdev/data/."""
    actions = []
    dst_data = PROJECT_ROOT / "icdev" / "data"
    dst_data.mkdir(parents=True, exist_ok=True)

    # Create __init__.py for data package
    data_init = dst_data / "__init__.py"
    actions.append(f"CREATE {data_init}")
    if not dry_run:
        data_init.write_text('"""ICDEV™ data -- FORGE layers (Goals, Context, Hardprompts, Args)."""\n')

    for dirname in DATA_DIRS:
        src = PROJECT_ROOT / dirname
        dst = dst_data / dirname
        if not src.is_dir():
            actions.append(f"SKIP (missing) {src}")
            continue

        actions.append(f"COPY DIR {src} -> {dst}")
        if not dry_run:
            if dst.exists():
                shutil.rmtree(dst)

            def _ignore(directory, files):
                ignored = set()
                rel_dir = os.path.relpath(directory, PROJECT_ROOT)
                for f in files:
                    rel_path = f"{rel_dir}/{f}"
                    # Normalize path separators
                    rel_path = rel_path.replace("\\", "/")
                    if rel_path in EXCLUDED_DATA_FILES:
                        ignored.add(f)
                    if rel_path in EXCLUDED_DATA_DIRS:
                        ignored.add(f)
                    for ed in EXCLUDED_DATA_DIRS:
                        if rel_path.startswith(ed + "/") or rel_path == ed:
                            ignored.add(f)
                    if rel_path in EXCLUDED_GOAL_FILES:
                        ignored.add(f)
                    # Skip __pycache__
                    if f == "__pycache__" or f.endswith(".pyc"):
                        ignored.add(f)
                return ignored

            shutil.copytree(src, dst, ignore=_ignore)

    return actions


def update_imports(dry_run: bool) -> list[str]:
    """Update import statements from tools.* to icdev.tools.* in icdev/tools/."""
    actions = []
    icdev_tools = PROJECT_ROOT / "icdev" / "tools"

    # Patterns to replace
    patterns = [
        # from tools.xxx import yyy
        (re.compile(r"^(\s*)(from\s+)tools\.([\w.]+)(\s+import\s+)", re.MULTILINE),
         r"\1\2icdev.tools.\3\4"),
        # import tools.xxx
        (re.compile(r"^(\s*)(import\s+)tools\.([\w.]+)", re.MULTILINE),
         r"\1\2icdev.tools.\3"),
        # "tools.xxx.yyy" in string literals (for module path references)
        (re.compile(r'"tools\.([\w.]+)"'), r'"icdev.tools.\1"'),
        (re.compile(r"'tools\.([\w.]+)'"), r"'icdev.tools.\1'"),
    ]

    py_files = list(icdev_tools.rglob("*.py"))
    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8", errors="replace")
        new_content = content
        for pattern, replacement in patterns:
            new_content = pattern.sub(replacement, new_content)

        if new_content != content:
            actions.append(f"UPDATE imports: {py_file.relative_to(PROJECT_ROOT)}")
            if not dry_run:
                py_file.write_text(new_content, encoding="utf-8")

    return actions


def update_imports_in_tests(dry_run: bool) -> list[str]:
    """Update import statements in test files."""
    actions = []
    tests_dir = PROJECT_ROOT / "tests"
    if not tests_dir.is_dir():
        return actions

    patterns = [
        (re.compile(r"^(\s*)(from\s+)tools\.([\w.]+)(\s+import\s+)", re.MULTILINE),
         r"\1\2icdev.tools.\3\4"),
        (re.compile(r"^(\s*)(import\s+)tools\.([\w.]+)", re.MULTILINE),
         r"\1\2icdev.tools.\3"),
        (re.compile(r'"tools\.([\w.]+)"'), r'"icdev.tools.\1"'),
        (re.compile(r"'tools\.([\w.]+)'"), r"'icdev.tools.\1'"),
    ]

    py_files = list(tests_dir.rglob("*.py"))
    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8", errors="replace")
        new_content = content
        for pattern, replacement in patterns:
            new_content = pattern.sub(replacement, new_content)

        if new_content != content:
            actions.append(f"UPDATE imports: {py_file.relative_to(PROJECT_ROOT)}")
            if not dry_run:
                py_file.write_text(new_content, encoding="utf-8")

    return actions


def update_base_dir(dry_run: bool) -> list[str]:
    """Replace BASE_DIR = Path(__file__).resolve().parent... patterns."""
    actions = []
    icdev_tools = PROJECT_ROOT / "icdev" / "tools"

    # Match various depths of .parent chains
    pattern = re.compile(
        r'^(\s*)BASE_DIR\s*=\s*Path\(__file__\)\.resolve\(\)'
        r'((?:\.parent)+)\s*$',
        re.MULTILINE,
    )

    # Also match _PROJECT_ROOT variant
    pattern_proj_root = re.compile(
        r'^(\s*)_PROJECT_ROOT\s*=\s*Path\(__file__\)\.resolve\(\)'
        r'((?:\.parent)+)\s*$',
        re.MULTILINE,
    )

    import_line = "from icdev._paths import get_project_root"

    py_files = list(icdev_tools.rglob("*.py"))
    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8", errors="replace")
        new_content = content
        needs_import = False

        for p, varname in [(pattern, "BASE_DIR"), (pattern_proj_root, "_PROJECT_ROOT")]:
            if p.search(new_content):
                needs_import = True
                new_content = p.sub(rf"\1{varname} = get_project_root()", new_content)

        if needs_import and new_content != content:
            # Add the import if not already present
            if import_line not in new_content:
                # Insert after the last existing import or at the top
                # Find a good insertion point after existing imports
                lines = new_content.split("\n")
                insert_idx = 0
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    if stripped.startswith("import ") or stripped.startswith("from "):
                        insert_idx = i + 1
                    elif stripped and not stripped.startswith("#") and not stripped.startswith('"""') and not stripped.startswith("'''") and insert_idx > 0:
                        break
                lines.insert(insert_idx, import_line)
                new_content = "\n".join(lines)

            actions.append(f"UPDATE BASE_DIR: {py_file.relative_to(PROJECT_ROOT)}")
            if not dry_run:
                py_file.write_text(new_content, encoding="utf-8")

    return actions


def create_tools_shim(dry_run: bool) -> list[str]:
    """Create backward-compatibility shim at tools/__init__.py."""
    actions = []
    tools_dir = PROJECT_ROOT / "tools"
    shim_file = tools_dir / "__init__.py"

    shim_content = '''\
"""Backward-compatibility shim: tools.* -> icdev.tools.*

The ICDEV™ tools package has moved to icdev.tools. This shim provides
backward compatibility for existing scripts and child applications.

Update your imports:
    from tools.llm.router import LLMRouter       # old
    from icdev.tools.llm.router import LLMRouter  # new
"""

import importlib
import sys
import warnings

warnings.warn(
    "Importing from 'tools' is deprecated. Use 'from icdev.tools' instead.",
    DeprecationWarning,
    stacklevel=2,
)


class _ToolsRedirect:
    """Module redirect: tools.xxx -> icdev.tools.xxx."""

    def __getattr__(self, name):
        return importlib.import_module(f"icdev.tools.{name}")


sys.modules[__name__] = _ToolsRedirect()
'''

    actions.append(f"CREATE {shim_file}")
    if not dry_run:
        shim_file.write_text(shim_content, encoding="utf-8")

    return actions


def verify_syntax(dry_run: bool) -> list[str]:
    """Verify all Python files compile without syntax errors."""
    if dry_run:
        return ["WOULD verify syntax of all icdev/*.py files"]

    actions = []
    icdev_dir = PROJECT_ROOT / "icdev"
    errors = 0
    for py_file in icdev_dir.rglob("*.py"):
        try:
            with open(py_file, "r", encoding="utf-8", errors="replace") as f:
                compile(f.read(), py_file, "exec")
        except SyntaxError as e:
            actions.append(f"SYNTAX ERROR: {py_file.relative_to(PROJECT_ROOT)}: {e}")
            errors += 1

    if errors == 0:
        actions.append("SYNTAX OK: All files compile successfully")
    else:
        actions.append(f"SYNTAX ERRORS: {errors} files have errors")

    return actions


def main():
    parser = argparse.ArgumentParser(description="Migrate ICDEV™ to PyPI structure")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Preview changes")
    group.add_argument("--execute", action="store_true", help="Execute migration")
    args = parser.parse_args()

    dry_run = args.dry_run
    mode = "DRY RUN" if dry_run else "EXECUTING"
    print(f"\n{'='*60}")
    print(f"  ICDEV™ PyPI Migration — {mode}")
    print(f"{'='*60}\n")

    all_actions = []

    print("Step 1: Moving tools/ subdirectories to icdev/tools/...")
    actions = move_tools(dry_run)
    all_actions.extend(actions)
    for a in actions:
        print(f"  {a}")

    print("\nStep 2: Copying data directories to icdev/data/...")
    actions = copy_data_dirs(dry_run)
    all_actions.extend(actions)
    for a in actions:
        print(f"  {a}")

    print("\nStep 3: Updating imports in icdev/tools/...")
    actions = update_imports(dry_run)
    all_actions.extend(actions)
    print(f"  {len(actions)} files updated")

    print("\nStep 4: Updating imports in tests/...")
    actions = update_imports_in_tests(dry_run)
    all_actions.extend(actions)
    print(f"  {len(actions)} files updated")

    print("\nStep 5: Updating BASE_DIR patterns...")
    actions = update_base_dir(dry_run)
    all_actions.extend(actions)
    print(f"  {len(actions)} files updated")

    print("\nStep 6: Creating backward-compat shim...")
    actions = create_tools_shim(dry_run)
    all_actions.extend(actions)
    for a in actions:
        print(f"  {a}")

    print("\nStep 7: Verifying syntax...")
    actions = verify_syntax(dry_run)
    all_actions.extend(actions)
    for a in actions:
        print(f"  {a}")

    print(f"\n{'='*60}")
    print(f"  Total actions: {len(all_actions)}")
    print(f"{'='*60}\n")

    if dry_run:
        print("Run with --execute to apply these changes.")


if __name__ == "__main__":
    main()
