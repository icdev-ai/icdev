#!/usr/bin/env python3
# CUI // SP-CTI
"""Mass-migrate sqlite3.connect() calls to get_connection() from storage abstraction.

Safely replaces direct sqlite3 usage with the storage abstraction layer.
Does NOT modify: storage.py, pg_init.py, init_icdev_db.py, backup files,
platform_db.py, db_compat.py, migration files, test files.

Usage:
    python tools/db/migrate_to_storage.py --scan --json     # Preview changes
    python tools/db/migrate_to_storage.py --apply           # Apply changes
    python tools/db/migrate_to_storage.py --verify --json   # Verify after apply
"""

import argparse
import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Files to SKIP (they legitimately need direct sqlite3 access)
SKIP_PATTERNS = [
    "storage.py",
    "pg_init.py",
    "init_icdev_db.py",
    "backup.py",
    "backup_manager.py",
    "platform_db.py",
    "db_compat.py",
    "pg_schema.py",
    "migrate.py",
    "migration_runner.py",
    "migrate_to_storage.py",
    "migrate_add_missing_columns.py",
    "__pycache__",
]

# Import line to add
IMPORT_LINE = "from tools.db.storage import get_connection"

# Patterns to match various sqlite3.connect() calls
CONNECT_PATTERNS = [
    # conn = sqlite3.connect(str(path))
    (r"(\s*)(\w+)\s*=\s*sqlite3\.connect\(str\((\w+)\)\)", r"\1\2 = get_connection()"),
    # conn = sqlite3.connect(str(DB_PATH))
    (r"(\s*)(\w+)\s*=\s*sqlite3\.connect\(str\(DB_PATH\)\)", r"\1\2 = get_connection()"),
    # conn = sqlite3.connect(str(self.db_path))
    (r"(\s*)(\w+)\s*=\s*sqlite3\.connect\(str\(self\.db_path\)\)", r"\1\2 = get_connection()"),
    # conn = sqlite3.connect(str(BASE_DIR / "data" / "icdev.db"))
    (
        r'(\s*)(\w+)\s*=\s*sqlite3\.connect\(str\(BASE_DIR\s*/\s*"data"\s*/\s*"icdev\.db"\)\)',
        r"\1\2 = get_connection()",
    ),
    # conn = sqlite3.connect(DB_PATH)  (without str())
    (r"(\s*)(\w+)\s*=\s*sqlite3\.connect\(DB_PATH\)", r"\1\2 = get_connection()"),
]

# row_factory line to remove (StorageConnection handles this)
ROW_FACTORY_PATTERN = re.compile(r"^\s*\w+\.row_factory\s*=\s*sqlite3\.Row\s*$")


def should_skip(file_path: Path) -> bool:
    """Check if file should be skipped."""
    name = file_path.name
    path_str = str(file_path)
    for pattern in SKIP_PATTERNS:
        if pattern in name or pattern in path_str:
            return True
    # Skip test files
    if name.startswith("test_"):
        return True
    return False


def scan_file(file_path: Path) -> dict:
    """Scan a file for sqlite3.connect() calls and report changes needed."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception:
        return None

    if "sqlite3.connect" not in content:
        return None

    lines = content.splitlines()
    changes = []

    for i, line in enumerate(lines, 1):
        if "sqlite3.connect" in line:
            changes.append({"line": i, "type": "connect", "text": line.strip()})
        if ROW_FACTORY_PATTERN.match(line):
            changes.append({"line": i, "type": "row_factory", "text": line.strip()})

    if not changes:
        return None

    has_import = IMPORT_LINE in content
    has_sqlite3_import = "import sqlite3" in content

    return {
        "file": str(file_path.relative_to(BASE_DIR)),
        "changes": changes,
        "needs_import": not has_import,
        "has_sqlite3_import": has_sqlite3_import,
        "change_count": len(changes),
    }


def apply_to_file(file_path: Path) -> dict:
    """Apply migration to a single file."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return {"file": str(file_path), "status": "error", "error": str(e)}

    original = content
    changes_made = 0

    # 1. Replace sqlite3.connect() calls
    for pattern, replacement in CONNECT_PATTERNS:
        new_content = re.sub(pattern, replacement, content)
        if new_content != content:
            changes_made += content.count("sqlite3.connect") - new_content.count("sqlite3.connect")
            content = new_content

    # 2. Remove row_factory lines
    lines = content.splitlines()
    new_lines = []
    for line in lines:
        if ROW_FACTORY_PATTERN.match(line):
            changes_made += 1
            continue  # Skip this line
        new_lines.append(line)
    content = "\n".join(new_lines)

    # 3. Add import if needed
    if IMPORT_LINE not in content and changes_made > 0:
        # Find the right place to add the import
        lines = content.splitlines()
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.startswith("from tools.") or line.startswith("import "):
                insert_idx = i + 1
            if line.strip() == "" and insert_idx > 0:
                break

        # Add after last import
        if insert_idx > 0:
            lines.insert(insert_idx, IMPORT_LINE)
        else:
            # Add after module docstring
            in_docstring = False
            for i, line in enumerate(lines):
                if '"""' in line:
                    if in_docstring:
                        insert_idx = i + 1
                        break
                    in_docstring = True
            lines.insert(insert_idx + 1, "")
            lines.insert(insert_idx + 2, IMPORT_LINE)

        content = "\n".join(lines)
        changes_made += 1

    # 4. Remove 'import sqlite3' if no longer used
    if "sqlite3" not in content.replace(IMPORT_LINE, "").replace("import sqlite3", ""):
        content = re.sub(r"^import sqlite3\n", "", content, flags=re.MULTILINE)

    if content == original:
        return {"file": str(file_path.relative_to(BASE_DIR)), "status": "no_changes"}

    # Write back
    file_path.write_text(content, encoding="utf-8", newline="")
    return {
        "file": str(file_path.relative_to(BASE_DIR)),
        "status": "modified",
        "changes": changes_made,
    }


def scan_all() -> dict:
    """Scan all Python files in tools/ for sqlite3.connect() usage."""
    results = {"files_scanned": 0, "files_needing_changes": 0, "total_changes": 0, "files": []}

    for py_file in (BASE_DIR / "tools").rglob("*.py"):
        if should_skip(py_file):
            continue
        results["files_scanned"] += 1
        scan = scan_file(py_file)
        if scan:
            results["files_needing_changes"] += 1
            results["total_changes"] += scan["change_count"]
            results["files"].append(scan)

    return results


def apply_all() -> dict:
    """Apply migration to all eligible files."""
    results = {"files_processed": 0, "files_modified": 0, "total_changes": 0, "errors": []}

    for py_file in (BASE_DIR / "tools").rglob("*.py"):
        if should_skip(py_file):
            continue
        if "sqlite3.connect" not in py_file.read_text(encoding="utf-8"):
            continue

        results["files_processed"] += 1
        result = apply_to_file(py_file)
        if result["status"] == "modified":
            results["files_modified"] += 1
            results["total_changes"] += result.get("changes", 0)
        elif result["status"] == "error":
            results["errors"].append(result)

    return results


def verify() -> dict:
    """Verify no direct sqlite3.connect() calls remain (except skipped files)."""
    remaining = []
    for py_file in (BASE_DIR / "tools").rglob("*.py"):
        if should_skip(py_file):
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
            if "sqlite3.connect" in content:
                remaining.append(str(py_file.relative_to(BASE_DIR)))
        except Exception:
            pass

    return {
        "remaining_direct_calls": len(remaining),
        "files": remaining,
        "status": "clean" if not remaining else "remaining",
    }


def main():
    parser = argparse.ArgumentParser(description="Migrate sqlite3.connect() to get_connection()")
    parser.add_argument("--scan", action="store_true", help="Preview changes")
    parser.add_argument("--apply", action="store_true", help="Apply changes")
    parser.add_argument("--verify", action="store_true", help="Verify no remaining calls")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.scan:
        result = scan_all()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Files needing changes: {result['files_needing_changes']}")
            print(f"Total changes needed: {result['total_changes']}")
            for f in result["files"][:20]:
                print(f"  {f['file']}: {f['change_count']} changes")
    elif args.apply:
        result = apply_all()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Modified: {result['files_modified']}/{result['files_processed']}")
            print(f"Total changes: {result['total_changes']}")
    elif args.verify:
        result = verify()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Status: {result['status']}")
            if result["files"]:
                for f in result["files"]:
                    print(f"  {f}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
