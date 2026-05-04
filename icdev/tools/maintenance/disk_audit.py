#!/usr/bin/env python3
# CUI // SP-CTI
"""OPT-29 — Disk Audit: stale data directory reporter.

Walks key directories, reports per-dir size, and flags untracked stale files/dirs
so cron jobs and the dashboard can act before disk space accumulates silently.

Audited targets (relative to project root):
  data/genesis/      — Genesis research exports
  data/scout/        — Scout run artifacts
  data/research/     — Research outputs
  context/research/  — Context research cache
  backups/           — Backup snapshots
  data/*.bak*        — Stale DB backups left by migrations
  .tmp/              — Scratch/worktree scratch space

Staleness threshold (default 1 day) — any path whose newest mtime is older
than --stale-days is flagged as stale.

JSON output (--json):
  {
    "generated_at": "<ISO-8601>",
    "stale_threshold_days": 1,
    "dirs": [
      {
        "path": "data/genesis",
        "size_bytes": 12345,
        "size_human": "12.1 KB",
        "file_count": 4,
        "newest_mtime": "2026-04-10T01:23:45+00:00",
        "stale": true,
        "exists": true
      },
      ...
    ],
    "loose_files": [
      {
        "path": "data/icdev.bak-pre-012",
        "size_bytes": 8192000,
        "size_human": "7.8 MB",
        "mtime": "2026-01-01T00:00:00+00:00",
        "stale": true
      },
      ...
    ],
    "summary": {
      "total_size_bytes": 987654,
      "total_size_human": "964.5 KB",
      "stale_dirs": 2,
      "stale_loose_files": 1,
      "total_stale_bytes": 12345,
      "total_stale_human": "12.1 KB"
    }
  }

Exit codes:
  0 — clean (no stale entries)
  1 — stale entries found (cron / CI gate)
  2 — runtime error

CLI:
    python tools/maintenance/disk_audit.py --json
    python tools/maintenance/disk_audit.py --stale-days 7 --json
    python tools/maintenance/disk_audit.py --gate            # exit 1 if stale

Scheduling (Linux cron):
    0 4 * * * /usr/bin/python /opt/icdev/tools/maintenance/disk_audit.py \
        --json >> /var/log/icdev/disk_audit.log 2>&1

Scheduling (Windows Task Scheduler):
    schtasks /Create /TN ICDEVDiskAudit /TR \
        "python.exe C:/ICDev/tools/maintenance/disk_audit.py --json" \
        /SC DAILY /ST 04:00
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Audit targets
# ---------------------------------------------------------------------------

# Directories to walk and measure (relative to BASE_DIR)
AUDIT_DIRS: list[str] = [
    "data/genesis",
    "data/scout",
    "data/research",
    "context/research",
    "backups",
    ".tmp",
]

# Glob patterns (relative to BASE_DIR) for loose stale files to flag
LOOSE_FILE_PATTERNS: list[str] = [
    "data/*.bak",
    "data/*.bak-*",
    "data/*.bak.*",
    "*.bak",
    "*.bak-*",
]

DEFAULT_STALE_DAYS = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _human_size(n_bytes: int) -> str:
    """Return a human-readable byte size string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n_bytes) < 1024.0:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024.0  # type: ignore[assignment]
    return f"{n_bytes:.1f} PB"


def _mtime_utc(p: Path) -> datetime:
    """Return path mtime as UTC-aware datetime."""
    ts = p.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _dir_stats(path: Path) -> tuple[int, int, datetime | None]:
    """Walk a directory and return (total_bytes, file_count, newest_mtime)."""
    total = 0
    count = 0
    newest: datetime | None = None
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    size = item.stat().st_size
                    mtime = _mtime_utc(item)
                    total += size
                    count += 1
                    if newest is None or mtime > newest:
                        newest = mtime
                except OSError:
                    pass
    except PermissionError:
        pass
    return total, count, newest


def _audit_dirs(stale_threshold: timedelta) -> list[dict[str, Any]]:
    """Audit each configured directory."""
    now = _now_utc()
    results: list[dict[str, Any]] = []

    for rel in AUDIT_DIRS:
        abs_path = BASE_DIR / rel
        entry: dict[str, Any] = {
            "path": rel,
            "exists": abs_path.exists(),
            "size_bytes": 0,
            "size_human": "0 B",
            "file_count": 0,
            "newest_mtime": None,
            "stale": False,
        }
        if abs_path.exists() and abs_path.is_dir():
            size, count, newest = _dir_stats(abs_path)
            entry["size_bytes"] = size
            entry["size_human"] = _human_size(size)
            entry["file_count"] = count
            if newest is not None:
                entry["newest_mtime"] = newest.isoformat()
                entry["stale"] = (now - newest) > stale_threshold
            else:
                # Empty dir — treat as stale
                entry["stale"] = True

        results.append(entry)

    return results


def _audit_loose_files(stale_threshold: timedelta) -> list[dict[str, Any]]:
    """Scan for loose .bak* files using glob patterns."""
    now = _now_utc()
    seen: set[Path] = set()
    results: list[dict[str, Any]] = []

    for pattern in LOOSE_FILE_PATTERNS:
        # Split into parent glob + file glob so we can iterate correctly
        parts = pattern.split("/")
        if len(parts) == 1:
            parent = BASE_DIR
            file_glob = parts[0]
        else:
            parent_pattern = "/".join(parts[:-1])
            file_glob = parts[-1]
            parent = BASE_DIR / parent_pattern

        # Resolve the parent (may itself be a glob — handle one level)
        try:
            candidates = list(parent.parent.glob(parent.name)) if "*" in str(parent) else [parent]
        except Exception:
            candidates = [parent]

        for base in candidates:
            if not base.is_dir():
                continue
            try:
                for f in base.iterdir():
                    if f in seen:
                        continue
                    if fnmatch.fnmatch(f.name, file_glob) and f.is_file():
                        seen.add(f)
                        try:
                            size = f.stat().st_size
                            mtime = _mtime_utc(f)
                            results.append(
                                {
                                    "path": str(f.relative_to(BASE_DIR)).replace("\\", "/"),
                                    "size_bytes": size,
                                    "size_human": _human_size(size),
                                    "mtime": mtime.isoformat(),
                                    "stale": (now - mtime) > stale_threshold,
                                }
                            )
                        except OSError:
                            pass
            except PermissionError:
                pass

    results.sort(key=lambda r: r["path"])
    return results


def _summary(dirs: list[dict], loose: list[dict]) -> dict[str, Any]:
    total_bytes = sum(d["size_bytes"] for d in dirs) + sum(f["size_bytes"] for f in loose)
    stale_bytes = sum(d["size_bytes"] for d in dirs if d["stale"]) + sum(
        f["size_bytes"] for f in loose if f["stale"]
    )
    return {
        "total_size_bytes": total_bytes,
        "total_size_human": _human_size(total_bytes),
        "stale_dirs": sum(1 for d in dirs if d["stale"]),
        "stale_loose_files": sum(1 for f in loose if f["stale"]),
        "total_stale_bytes": stale_bytes,
        "total_stale_human": _human_size(stale_bytes),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="disk_audit",
        description="OPT-29 — Walk key data dirs, report size, flag stale entries.",
    )
    p.add_argument(
        "--stale-days",
        type=float,
        default=DEFAULT_STALE_DAYS,
        metavar="N",
        help=f"Flag dirs/files not written in N days (default: {DEFAULT_STALE_DAYS})",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON report to stdout",
    )
    p.add_argument(
        "--gate",
        action="store_true",
        help="Exit 1 if any stale entries found (CI gate mode)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    stale_threshold = timedelta(days=args.stale_days)

    try:
        dirs = _audit_dirs(stale_threshold)
        loose = _audit_loose_files(stale_threshold)
        summary = _summary(dirs, loose)

        report: dict[str, Any] = {
            "generated_at": _now_utc().isoformat(),
            "stale_threshold_days": args.stale_days,
            "dirs": dirs,
            "loose_files": loose,
            "summary": summary,
        }

        if args.json:
            print(json.dumps(report, indent=2))
        else:
            # Human-readable table
            print(f"\nDisk Audit — {report['generated_at']}")
            print(f"Stale threshold: {args.stale_days} day(s)\n")
            print(f"{'Directory':<30} {'Size':>10} {'Files':>7} {'Newest mtime':<30} {'Stale?'}")
            print("-" * 90)
            for d in dirs:
                newest = d["newest_mtime"] or "—"
                stale_flag = "YES" if d["stale"] else "no"
                exists_tag = "" if d["exists"] else " (missing)"
                print(
                    f"{d['path'] + exists_tag:<30} {d['size_human']:>10} {d['file_count']:>7}"
                    f" {newest:<30} {stale_flag}"
                )

            if loose:
                print(f"\n{'Loose .bak files':<50} {'Size':>10} {'mtime':<30} {'Stale?'}")
                print("-" * 90)
                for f in loose:
                    stale_flag = "YES" if f["stale"] else "no"
                    print(f"{f['path']:<50} {f['size_human']:>10} {f['mtime']:<30} {stale_flag}")

            print(
                f"\nSummary: total={summary['total_size_human']}"
                f"  stale_dirs={summary['stale_dirs']}"
                f"  stale_loose={summary['stale_loose_files']}"
                f"  stale_size={summary['total_stale_human']}"
            )

        has_stale = summary["stale_dirs"] > 0 or summary["stale_loose_files"] > 0
        if args.gate and has_stale:
            return 1
        return 0

    except Exception as exc:
        err = {"error": str(exc), "generated_at": _now_utc().isoformat()}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
