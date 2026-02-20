#!/usr/bin/env python3
# CUI // SP-CTI
"""CLI wrapper for ICDEV backup/restore operations.

Usage:
    python tools/db/backup.py --backup [--db icdev] [--encrypt --passphrase "..."] [--json]
    python tools/db/backup.py --restore --backup-file path/to/backup.db.bak [--db-path target.db] [--json]
    python tools/db/backup.py --verify --backup-file path/to/backup.db.bak [--json]
    python tools/db/backup.py --list [--json]
    python tools/db/backup.py --backup --tenants [--slug acme] [--json]
    python tools/db/backup.py --backup --all [--json]
    python tools/db/backup.py --prune [--retention-days 30] [--json]

ADR D152: Backup/restore with SHA-256 integrity, optional AES-256-CBC encryption.
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Ensure project root is on sys.path so tools.db imports work
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.backup_manager import BackupManager


def _print_human(data, label: str = "Result") -> None:
    """Print data in a human-readable format."""
    if isinstance(data, dict):
        print(f"\n--- {label} ---")
        for key, value in data.items():
            if isinstance(value, list):
                print(f"  {key}:")
                for item in value:
                    if isinstance(item, dict):
                        for k, v in item.items():
                            print(f"    {k}: {v}")
                        print()
                    else:
                        print(f"    - {item}")
            else:
                print(f"  {key}: {value}")
    elif isinstance(data, list):
        print(f"\n--- {label} ({len(data)} items) ---")
        for i, item in enumerate(data, 1):
            if isinstance(item, dict):
                print(f"\n  [{i}]")
                for key, value in item.items():
                    print(f"    {key}: {value}")
            else:
                print(f"  [{i}] {item}")
    else:
        print(f"\n{label}: {data}")
    print()


def _print_output(data, as_json: bool, label: str = "Result") -> None:
    """Print data as JSON or human-readable."""
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        _print_human(data, label)


def cmd_backup(args, manager: BackupManager) -> None:
    """Handle --backup command."""
    if args.all:
        results = manager.backup_all(
            output_dir=Path(args.output_dir) if args.output_dir else None
        )
        _print_output(results, args.json, "Backup All Databases")
        # Check for errors
        if any("error" in r for r in results):
            sys.exit(1)
        return

    if args.tenants:
        results = manager.backup_tenants(
            slug=args.slug,
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
        _print_output(results, args.json, "Tenant Backups")
        if any("error" in r for r in results):
            sys.exit(1)
        return

    # Single database backup
    if args.db:
        try:
            db_path = manager.resolve_db_path(args.db)
        except ValueError as exc:
            _print_output({"error": str(exc)}, args.json, "Error")
            sys.exit(1)
    elif args.db_path:
        db_path = Path(args.db_path).resolve()
    else:
        # Default to icdev.db
        try:
            db_path = manager.resolve_db_path("icdev")
        except ValueError:
            db_path = BASE_DIR / "data" / "icdev.db"

    if not db_path.exists():
        _print_output(
            {"error": f"Database not found: {db_path}"},
            args.json,
            "Error",
        )
        sys.exit(1)

    result = manager.backup_sqlite(
        db_path,
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )

    # Optional encryption
    if args.encrypt:
        if not args.passphrase:
            _print_output(
                {"error": "--passphrase is required when --encrypt is set"},
                args.json,
                "Error",
            )
            sys.exit(1)
        try:
            enc_path = manager.encrypt(
                Path(result["backup_path"]), args.passphrase
            )
            result["encrypted"] = True
            result["encrypted_path"] = str(enc_path)
        except ImportError as exc:
            _print_output({"error": str(exc)}, args.json, "Error")
            sys.exit(1)

    _print_output(result, args.json, "Backup Created")


def cmd_restore(args, manager: BackupManager) -> None:
    """Handle --restore command."""
    if not args.backup_file:
        _print_output(
            {"error": "--backup-file is required for restore"},
            args.json,
            "Error",
        )
        sys.exit(1)

    backup_path = Path(args.backup_file).resolve()

    # Determine target path
    if args.db_path:
        target_path = Path(args.db_path).resolve()
    elif args.db:
        try:
            target_path = manager.resolve_db_path(args.db)
        except ValueError as exc:
            _print_output({"error": str(exc)}, args.json, "Error")
            sys.exit(1)
    else:
        _print_output(
            {"error": "--db-path or --db is required for restore target"},
            args.json,
            "Error",
        )
        sys.exit(1)

    # Decrypt if needed
    if backup_path.name.endswith(".enc"):
        if not args.passphrase:
            _print_output(
                {"error": "--passphrase is required to decrypt .enc files"},
                args.json,
                "Error",
            )
            sys.exit(1)
        try:
            backup_path = manager.decrypt(backup_path, args.passphrase)
        except ImportError as exc:
            _print_output({"error": str(exc)}, args.json, "Error")
            sys.exit(1)

    result = manager.restore_sqlite(backup_path, target_path)
    _print_output(result, args.json, "Restore Result")

    if not result.get("integrity_ok"):
        sys.exit(1)


def cmd_verify(args, manager: BackupManager) -> None:
    """Handle --verify command."""
    if not args.backup_file:
        _print_output(
            {"error": "--backup-file is required for verify"},
            args.json,
            "Error",
        )
        sys.exit(1)

    backup_path = Path(args.backup_file).resolve()
    result = manager.verify(backup_path)
    _print_output(result, args.json, "Verification Result")

    if not result.get("valid"):
        sys.exit(1)


def cmd_list(args, manager: BackupManager) -> None:
    """Handle --list command."""
    records = manager.list_backups(
        backup_dir=Path(args.backup_dir) if args.backup_dir else None
    )
    _print_output(records, args.json, "Backups")


def cmd_prune(args, manager: BackupManager) -> None:
    """Handle --prune command."""
    result = manager.prune_old_backups(
        backup_dir=Path(args.backup_dir) if args.backup_dir else None,
        retention_days=args.retention_days,
    )
    _print_output(result, args.json, "Prune Result")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="ICDEV database backup/restore tool (CUI // SP-CTI)"
    )

    # Operation modes (mutually exclusive)
    ops = parser.add_mutually_exclusive_group(required=True)
    ops.add_argument("--backup", action="store_true", help="Create a backup")
    ops.add_argument("--restore", action="store_true", help="Restore from backup")
    ops.add_argument("--verify", action="store_true", help="Verify backup integrity")
    ops.add_argument("--list", action="store_true", help="List available backups")
    ops.add_argument("--prune", action="store_true", help="Prune old backups")

    # Backup options
    parser.add_argument(
        "--db",
        choices=["icdev", "platform", "memory", "activity"],
        help="Named database to backup (from db_config.yaml)",
    )
    parser.add_argument("--db-path", help="Explicit database file path")
    parser.add_argument("--all", action="store_true", help="Backup all configured databases")
    parser.add_argument("--tenants", action="store_true", help="Backup tenant databases")
    parser.add_argument("--slug", help="Specific tenant slug (with --tenants)")
    parser.add_argument("--output-dir", help="Override backup output directory")

    # Restore options
    parser.add_argument("--backup-file", help="Path to backup file for restore/verify")

    # Encryption
    parser.add_argument("--encrypt", action="store_true", help="Encrypt the backup")
    parser.add_argument("--passphrase", help="Passphrase for encryption/decryption")

    # Prune options
    parser.add_argument(
        "--retention-days",
        type=int,
        default=30,
        help="Retention period in days for pruning (default: 30)",
    )

    # List options
    parser.add_argument("--backup-dir", help="Directory to scan for backups")

    # Output format
    parser.add_argument(
        "--json", action="store_true", help="Output in JSON format"
    )

    args = parser.parse_args()
    manager = BackupManager()

    try:
        if args.backup:
            cmd_backup(args, manager)
        elif args.restore:
            cmd_restore(args, manager)
        elif args.verify:
            cmd_verify(args, manager)
        elif args.list:
            cmd_list(args, manager)
        elif args.prune:
            cmd_prune(args, manager)
    except FileNotFoundError as exc:
        _print_output({"error": str(exc)}, args.json, "Error")
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        _print_output(
            {
                "error": f"Subprocess failed: {exc.cmd}",
                "stderr": exc.stderr,
                "returncode": exc.returncode,
            },
            args.json,
            "Error",
        )
        sys.exit(1)
    except Exception as exc:
        _print_output({"error": str(exc)}, args.json, "Error")
        sys.exit(1)


if __name__ == "__main__":
    main()
