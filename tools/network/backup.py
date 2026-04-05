#!/usr/bin/env python3
"""
Network Design Canvas — Backup & Restore CLI

Usage:
    python tools/backup.py --backup                      # Manual backup now
    python tools/backup.py --backup --notes "Pre-upgrade"
    python tools/backup.py --list                        # List all backups
    python tools/backup.py --restore <backup_id>         # Restore specific backup
    python tools/backup.py --schedule                    # Start scheduled daily backup
"""

import argparse
import json
import shutil
import sys
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
_ICDEV_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ICDEV_ROOT))
from tools.db.init_db import get_connection, DB_PATH  # noqa: E402

ICDEV_DB = _ICDEV_ROOT / "data" / "icdev.db"
BACKUP_DIR = APP_DIR / "backups"


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_backup(notes="", backup_type="manual"):
    """Create a point-in-time backup ZIP."""
    BACKUP_DIR.mkdir(exist_ok=True)
    backup_id = str(uuid.uuid4())
    ts = _now().replace(":", "-").replace("T", "_")[:19]
    zip_name = f"nc-backup-{ts}.zip"
    zip_path = BACKUP_DIR / zip_name

    includes = []
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
        # Network Canvas DB
        if DB_PATH.exists():
            zf.write(str(DB_PATH), "network_canvas.db")
            includes.append("network_canvas.db")

        # ICDEV DB
        if ICDEV_DB.exists():
            zf.write(str(ICDEV_DB), "icdev.db")
            includes.append("icdev.db")

        # Config files
        args_dir = APP_DIR / "args"
        if args_dir.exists():
            for f in args_dir.glob("*"):
                if f.is_file():
                    zf.write(str(f), f"args/{f.name}")
                    includes.append(f"args/{f.name}")

        for extra in ["requirements.txt", "CLAUDE.md"]:
            ef = APP_DIR / extra
            if ef.exists():
                zf.write(str(ef), extra)
                includes.append(extra)

    file_size = zip_path.stat().st_size

    # Register in DB
    conn = get_connection()
    conn.execute(
        "INSERT INTO nc_backups (id, backup_type, file_path, file_size_bytes, includes_json, notes, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (backup_id, backup_type, str(zip_path), file_size, json.dumps(includes), notes, _now()),
    )
    conn.commit()
    conn.close()

    print(f"Backup created: {zip_name}")
    print(f"  ID: {backup_id}")
    print(f"  Size: {file_size:,} bytes")
    print(f"  Files: {len(includes)}")
    return backup_id


def list_backups():
    """List all backups."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM nc_backups ORDER BY created_at DESC").fetchall()
    conn.close()
    if not rows:
        print("No backups found.")
        return
    print(f"{'ID':<38} {'Type':<10} {'Size':>10} {'Created':<22} Notes")
    print("-" * 100)
    for r in rows:
        size_kb = (r["file_size_bytes"] or 0) / 1024
        print(
            f"{r['id']:<38} {r['backup_type']:<10} {size_kb:>8.1f}KB {(r['created_at'] or '')[:19]:<22} {r['notes'] or ''}"
        )


def restore_backup(backup_id):
    """Restore from a specific backup."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM nc_backups WHERE id=?", (backup_id,)).fetchone()
    conn.close()
    if not row:
        print(f"Backup not found: {backup_id}")
        sys.exit(1)

    zip_path = Path(row["file_path"])
    if not zip_path.exists():
        print(f"Backup file missing: {zip_path}")
        sys.exit(1)

    print(f"Restoring from: {zip_path.name}")
    print(f"  Created: {row['created_at']}")

    restored = []
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        for name in zf.namelist():
            if name == "network_canvas.db":
                target = APP_DIR / "network_canvas.db"
                if target.exists():
                    shutil.copy2(str(target), str(target) + ".pre-restore")
                zf.extract(name, str(APP_DIR))
                restored.append(name)
            elif name == "icdev.db":
                target = ICDEV_DB
                if target.exists():
                    shutil.copy2(str(target), str(target) + ".pre-restore")
                    zf.extract(name, str(target.parent))
                    restored.append(name)
            elif name.startswith("args/"):
                zf.extract(name, str(APP_DIR))
                restored.append(name)

    print(f"  Restored {len(restored)} files: {', '.join(restored)}")
    print("  NOTE: Restart the server to apply restored database.")


def run_scheduled(interval_hours=24):
    """Run scheduled backup loop."""
    print(f"Scheduled backup every {interval_hours}h. Press Ctrl+C to stop.")
    while True:
        create_backup(notes="scheduled", backup_type="scheduled")
        time.sleep(interval_hours * 3600)


def main():
    parser = argparse.ArgumentParser(description="Network Canvas Backup & Restore")
    parser.add_argument("--backup", action="store_true", help="Create backup now")
    parser.add_argument("--list", action="store_true", help="List all backups")
    parser.add_argument("--restore", type=str, help="Restore from backup ID")
    parser.add_argument("--schedule", action="store_true", help="Start scheduled daily backup")
    parser.add_argument("--notes", type=str, default="", help="Backup notes")
    parser.add_argument("--interval", type=int, default=24, help="Schedule interval (hours)")
    args = parser.parse_args()

    if args.backup:
        create_backup(notes=args.notes)
    elif args.list:
        list_backups()
    elif args.restore:
        restore_backup(args.restore)
    elif args.schedule:
        run_scheduled(args.interval)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
