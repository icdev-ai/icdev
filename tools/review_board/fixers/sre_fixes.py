#!/usr/bin/env python3
# CUI // SP-CTI
"""SRE auto-fix handlers — backup, disk cleanup, VACUUM.

Each function takes a finding dict and returns a result dict.
Verification functions take the same finding and return {"passed": bool, ...}.
"""

import subprocess
import sys
from tools.db.storage import get_connection
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent


def fix_backup_freshness(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Run database backup to fix stale backup finding."""
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "tools" / "db" / "backup.py"), "--backup", "--all", "--json"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return {"action": "backup_created", "output": result.stdout[:500]}
        return {"action": "backup_failed", "error": result.stderr[:500]}
    except subprocess.TimeoutExpired:
        return {"action": "backup_timeout", "error": "Backup timed out after 120s"}
    except Exception as e:
        return {"action": "backup_error", "error": str(e)}


def verify_backup_freshness(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Verify backup was created recently."""
    from datetime import datetime, timezone

    backup_dir = BASE_DIR / "data" / "backups"
    if not backup_dir.exists():
        return {"passed": False, "reason": "Backup directory does not exist"}

    backup_files = sorted(
        backup_dir.glob("*.bak"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    if not backup_files:
        return {"passed": False, "reason": "No backup files found"}

    newest = backup_files[0]
    age_hours = (datetime.now(timezone.utc).timestamp() - newest.stat().st_mtime) / 3600
    return {
        "passed": age_hours < 1.0,
        "newest_backup": newest.name,
        "age_hours": round(age_hours, 1),
    }


def fix_disk_usage(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Run VACUUM on oversized SQLite databases."""
    data_dir = BASE_DIR / "data"
    vacuumed = []
    errors = []

    if not data_dir.exists():
        return {"action": "no_data_dir"}

    for db_file in data_dir.glob("*.db"):
        size_before_mb = db_file.stat().st_size / (1024 * 1024)
        if size_before_mb < 100:
            continue  # Only vacuum large DBs
        try:
            conn = get_connection()
            conn.execute("VACUUM")
            conn.close()
            size_after_mb = db_file.stat().st_size / (1024 * 1024)
            vacuumed.append(
                {
                    "file": db_file.name,
                    "before_mb": round(size_before_mb, 1),
                    "after_mb": round(size_after_mb, 1),
                    "saved_mb": round(size_before_mb - size_after_mb, 1),
                }
            )
        except Exception as e:
            errors.append({"file": db_file.name, "error": str(e)})

    return {"action": "vacuum_completed", "vacuumed": vacuumed, "errors": errors}


def verify_disk_usage(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Verify database sizes are within threshold after VACUUM."""
    data_dir = BASE_DIR / "data"
    max_size_mb = 500
    oversized = []

    if data_dir.exists():
        for db_file in data_dir.glob("*.db"):
            size_mb = db_file.stat().st_size / (1024 * 1024)
            if size_mb > max_size_mb:
                oversized.append({"file": db_file.name, "size_mb": round(size_mb, 1)})

    return {
        "passed": len(oversized) == 0,
        "oversized_remaining": oversized,
    }
