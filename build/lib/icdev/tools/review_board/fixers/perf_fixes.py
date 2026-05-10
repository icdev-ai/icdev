#!/usr/bin/env python3
# CUI // SP-CTI
"""Performance auto-fix handlers — temp cleanup, cache pruning."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

# Directories safe to clean within .tmp/
SAFE_CLEAN_DIRS = [
    "test_runs",
    "screenshots",
    "worktrees",
    "genesis/worktrees",
]

# File extensions safe to delete
SAFE_CLEAN_EXTENSIONS = {".log", ".tmp", ".pyc"}

# Max age in days — only clean files older than this
MAX_AGE_DAYS = 7


def fix_temp_size(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Clean up .tmp directory — remove old test runs, screenshots, logs."""
    tmp_dir = BASE_DIR / ".tmp"
    if not tmp_dir.exists():
        return {"action": "no_tmp_dir"}

    cleaned_files = 0
    cleaned_bytes = 0
    errors = []
    now_ts = datetime.now(timezone.utc).timestamp()
    max_age_seconds = MAX_AGE_DAYS * 86400

    # Clean safe subdirectories
    for subdir_name in SAFE_CLEAN_DIRS:
        subdir = tmp_dir / subdir_name
        if not subdir.exists():
            continue
        try:
            for item in subdir.rglob("*"):
                if not item.is_file():
                    continue
                age = now_ts - item.stat().st_mtime
                if age > max_age_seconds:
                    size = item.stat().st_size
                    try:
                        item.unlink()
                        cleaned_files += 1
                        cleaned_bytes += size
                    except Exception as e:
                        errors.append(f"{item.name}: {e}")
        except Exception as e:
            errors.append(f"{subdir_name}: {e}")

    # Clean old log files in .tmp root
    for log_file in tmp_dir.glob("*.log"):
        age = now_ts - log_file.stat().st_mtime
        if age > max_age_seconds:
            size = log_file.stat().st_size
            try:
                log_file.unlink()
                cleaned_files += 1
                cleaned_bytes += size
            except Exception:
                pass

    return {
        "action": "temp_cleaned",
        "files_removed": cleaned_files,
        "bytes_freed": cleaned_bytes,
        "mb_freed": round(cleaned_bytes / (1024 * 1024), 1),
        "errors": errors[:10],
    }


def verify_temp_size(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Verify .tmp size is under threshold after cleanup."""
    tmp_dir = BASE_DIR / ".tmp"
    if not tmp_dir.exists():
        return {"passed": True, "size_mb": 0}

    total_size = sum(f.stat().st_size for f in tmp_dir.rglob("*") if f.is_file())
    size_mb = total_size / (1024 * 1024)

    return {
        "passed": size_mb < 500,
        "size_mb": round(size_mb, 1),
    }
