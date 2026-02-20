#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV Database Migration CLI.

D150: Lightweight migration runner — apply, rollback, validate, scaffold.

Usage:
    python tools/db/migrate.py --status [--json]
    python tools/db/migrate.py --up [--target 005] [--dry-run]
    python tools/db/migrate.py --down [--target 003]
    python tools/db/migrate.py --validate [--json]
    python tools/db/migrate.py --create "add_feature_table"
    python tools/db/migrate.py --mark-applied 001
    python tools/db/migrate.py --up --all-tenants
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.migration_runner import MigrationRunner

DB_PATH = BASE_DIR / "data" / "icdev.db"


def _format_status(status: dict) -> str:
    """Format migration status for human-readable output."""
    lines = [
        f"Database: {status['db_path']}",
        f"Engine: {status['engine']}",
        f"Migrations table: {'exists' if status['has_migrations_table'] else 'missing'}",
        f"Current version: {status['current_version'] or 'none'}",
        f"Applied: {status['applied_count']}  |  Pending: {status['pending_count']}",
    ]

    if status["applied"]:
        lines.append("\nApplied migrations:")
        for m in status["applied"]:
            lines.append(f"  [{m['version']}] {m['name']} (applied {m['applied_at']})")

    if status["pending"]:
        lines.append("\nPending migrations:")
        for m in status["pending"]:
            lines.append(f"  [{m['version']}] {m['name']}")

    if status["issues"]:
        lines.append("\nIssues:")
        for issue in status["issues"]:
            lines.append(f"  [{issue['version']}] {issue['issue']}: {issue['detail']}")

    return "\n".join(lines)


def _get_tenant_db_paths() -> list:
    """Discover tenant database files."""
    tenant_dir = BASE_DIR / "data" / "tenants"
    if not tenant_dir.exists():
        return []
    return sorted(tenant_dir.glob("*.db"))


def main():
    parser = argparse.ArgumentParser(description="ICDEV Database Migration Tool")
    parser.add_argument("--db-path", type=Path, default=DB_PATH, help="Database file path")
    parser.add_argument("--status", action="store_true", help="Show migration status")
    parser.add_argument("--up", action="store_true", help="Apply pending migrations")
    parser.add_argument("--down", action="store_true", help="Roll back last migration")
    parser.add_argument("--target", help="Target version (for --up or --down)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without applying")
    parser.add_argument("--validate", action="store_true", help="Validate migration checksums")
    parser.add_argument("--create", metavar="NAME", help="Create new migration scaffold")
    parser.add_argument("--mark-applied", metavar="VERSION", help="Mark version as applied")
    parser.add_argument("--all-tenants", action="store_true", help="Apply to all tenant DBs too")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    runner = MigrationRunner(db_path=args.db_path)

    # ---- Status ----
    if args.status:
        status = runner.get_status()
        if args.json:
            print(json.dumps(status, indent=2, default=str))
        else:
            print(_format_status(status))
        return

    # ---- Validate ----
    if args.validate:
        runner.ensure_migrations_table()
        issues = runner.validate_checksums()
        if args.json:
            print(json.dumps({"issues": issues, "valid": len(issues) == 0}, indent=2))
        elif issues:
            print("Validation FAILED:")
            for issue in issues:
                print(f"  [{issue['version']}] {issue['issue']}: {issue['detail']}")
            sys.exit(1)
        else:
            print("All migration checksums valid.")
        return

    # ---- Create ----
    if args.create:
        path = runner.create_migration(args.create)
        result = {"created": path}
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Created migration: {path}")
        return

    # ---- Mark Applied ----
    if args.mark_applied:
        runner.mark_applied(args.mark_applied)
        if args.json:
            print(json.dumps({"marked_applied": args.mark_applied}))
        else:
            print(f"Marked migration {args.mark_applied} as applied.")
        return

    # ---- Migrate Up ----
    if args.up:
        db_paths = [args.db_path]
        if args.all_tenants:
            db_paths.extend(_get_tenant_db_paths())

        all_results = {}
        for db_path in db_paths:
            r = MigrationRunner(db_path=db_path)
            results = r.migrate_up(target=args.target, dry_run=args.dry_run)
            all_results[str(db_path)] = results

        if args.json:
            print(json.dumps(all_results, indent=2, default=str))
        else:
            for db_path, results in all_results.items():
                if not results:
                    print(f"[{db_path}] No pending migrations.")
                    continue
                print(f"[{db_path}]")
                for r in results:
                    status = "OK" if r.get("success") else f"FAILED: {r.get('error')}"
                    ms = r.get("execution_time_ms", "")
                    dry = " (dry run)" if r.get("dry_run") else ""
                    print(f"  [{r['version']}] {r['name']} — {status} {ms}ms{dry}")

            # Exit with error if any failed
            for results in all_results.values():
                if any(not r.get("success") for r in results):
                    sys.exit(1)
        return

    # ---- Migrate Down ----
    if args.down:
        results = runner.migrate_down(target=args.target)
        if args.json:
            print(json.dumps(results, indent=2, default=str))
        elif not results:
            print("Nothing to roll back.")
        else:
            for r in results:
                status = "OK" if r.get("success") else f"FAILED: {r.get('error')}"
                print(f"  [{r['version']}] {r['name']} — rolled back {status}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
