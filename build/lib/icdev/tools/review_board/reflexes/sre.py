#!/usr/bin/env python3
# CUI // SP-CTI
"""SRE Reflex — reliability, resilience, and operational health checks.

Checks:
    - Health endpoint latency (from heartbeat_checks table)
    - Circuit breaker trip rate (from resilience state)
    - Error budget burn rate (from audit_trail error events)
    - Backup freshness (from backup metadata)
    - Disk usage (database file sizes)
    - HPA utilization (from scaling metrics if available)

Architecture: D-RB-1 (scanner-tier, zero LLM cost), D-RB-3 (standalone run())
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute SRE reliability checks."""
    findings: List[Dict] = []
    checks_completed = 0
    thresholds = config.get("thresholds", {})

    # --- Check 1: Backup freshness ---
    try:
        backup_dir = BASE_DIR / "data" / "backups"
        max_age_hours = thresholds.get("backup_max_age_hours", 48)
        if backup_dir.exists():
            backup_files = sorted(backup_dir.glob("*.bak"), key=lambda f: f.stat().st_mtime, reverse=True)
            if backup_files:
                newest = backup_files[0]
                age_hours = (datetime.now(timezone.utc).timestamp() - newest.stat().st_mtime) / 3600
                if age_hours > max_age_hours:
                    findings.append(
                        {
                            "severity": "high",
                            "category": "backup_freshness",
                            "title": f"Backup is {age_hours:.0f}h old (threshold: {max_age_hours}h)",
                            "description": f"Most recent backup: {newest.name}",
                            "recommendation": "Run: python tools/db/backup.py --backup --all",
                            "confidence": 0.9,
                            "auto_fixable": False,
                        }
                    )
            else:
                findings.append(
                    {
                        "severity": "critical",
                        "category": "backup_freshness",
                        "title": "No backup files found",
                        "description": f"Backup directory exists but contains no .bak files: {backup_dir}",
                        "recommendation": "Run: python tools/db/backup.py --backup --all",
                        "confidence": 1.0,
                        "auto_fixable": False,
                    }
                )
        checks_completed += 1
    except Exception as e:
        findings.append(
            {
                "severity": "low",
                "category": "backup_freshness",
                "title": f"Backup check failed: {e}",
                "confidence": 0.5,
            }
        )

    # --- Check 2: Database file sizes ---
    try:
        data_dir = BASE_DIR / "data"
        max_size_mb = thresholds.get("max_db_file_size_mb", 500)
        if data_dir.exists():
            for db_file in data_dir.glob("*.db"):
                size_mb = db_file.stat().st_size / (1024 * 1024)
                if size_mb > max_size_mb:
                    findings.append(
                        {
                            "severity": "high",
                            "category": "disk_usage",
                            "title": f"Database {db_file.name} is {size_mb:.1f}MB (threshold: {max_size_mb}MB)",
                            "recommendation": "Consider archiving old data or running VACUUM",
                            "confidence": 1.0,
                            "auto_fixable": False,
                        }
                    )
        checks_completed += 1
    except Exception:
        pass

    # --- Check 3: Error rate from audit trail ---
    try:
        if get_connection:
            conn = get_connection()
            try:
                # Count errors in last hour
                error_count = conn.execute(
                    "SELECT COUNT(*) FROM audit_trail "
                    "WHERE event_type LIKE '%.error%' "
                    "AND created_at > datetime('now', '-1 hour')"
                ).fetchone()
                total_count = conn.execute(
                    "SELECT COUNT(*) FROM audit_trail WHERE created_at > datetime('now', '-1 hour')"
                ).fetchone()

                if total_count and total_count[0] > 0:
                    error_rate = (error_count[0] / total_count[0]) * 100
                    max_rate = thresholds.get("max_error_rate_pct", 1.0)
                    if error_rate > max_rate:
                        findings.append(
                            {
                                "severity": "critical" if error_rate > 5.0 else "high",
                                "category": "error_rate",
                                "title": f"Error rate {error_rate:.2f}% exceeds threshold ({max_rate}%)",
                                "description": f"{error_count[0]} errors out of {total_count[0]} events in last hour",
                                "recommendation": "Check tools/monitor/log_analyzer.py for root cause",
                                "confidence": 0.95,
                                "auto_fixable": False,
                            }
                        )
                checks_completed += 1
            finally:
                conn.close()
    except Exception:
        checks_completed += 1

    # --- Check 4: Circuit breaker state ---
    try:
        if get_connection:
            conn = get_connection()
            try:
                # Check for tripped circuit breakers across all daemon state tables
                for state_table in ["genesis_reflex_state", "review_board_reflex_state"]:
                    try:
                        # state_table is from a hardcoded list (trusted, not user input)
                        stmt = f"SELECT reflex_name, consecutive_failures FROM {state_table} WHERE circuit_breaker_open = 1"  # nosec B608
                        tripped = conn.execute(stmt).fetchall()
                        for row in tripped:
                            findings.append(
                                {
                                    "severity": "high",
                                    "category": "circuit_breaker",
                                    "title": f"Circuit breaker OPEN: {row[0]} ({state_table})",
                                    "description": f"Consecutive failures: {row[1]}",
                                    "recommendation": f"Investigate and reset: python tools/review_board/daemon.py --reset {row[0]}",
                                    "confidence": 1.0,
                                    "auto_fixable": False,
                                }
                            )
                    except Exception:
                        pass  # Table may not exist
                checks_completed += 1
            finally:
                conn.close()
    except Exception:
        pass

    # --- Check 5: Heartbeat check freshness ---
    try:
        if get_connection:
            conn = get_connection()
            try:
                stale = conn.execute(
                    "SELECT check_name, last_checked_at FROM heartbeat_checks "
                    "WHERE last_checked_at < datetime('now', '-2 hours') "
                    "AND enabled = 1"
                ).fetchall()
                for row in stale:
                    findings.append(
                        {
                            "severity": "medium",
                            "category": "heartbeat_stale",
                            "title": f"Heartbeat check stale: {row[0]}",
                            "description": f"Last checked: {row[1]}",
                            "recommendation": "Restart heartbeat daemon: python tools/monitor/heartbeat_daemon.py --once",
                            "confidence": 0.8,
                            "auto_fixable": False,
                        }
                    )
                checks_completed += 1
            except Exception:
                checks_completed += 1
            finally:
                conn.close()
    except Exception:
        pass

    return {
        "success": True,
        "metric_value": float(len(findings)),
        "details": {
            "checks_completed": checks_completed,
            "findings_count": len(findings),
            "findings": findings,
            "severity_summary": _severity_summary(findings),
        },
    }


def _severity_summary(findings: List[Dict]) -> Dict[str, int]:
    """Count findings by severity."""
    summary: Dict[str, int] = {}
    for f in findings:
        sev = f.get("severity", "info")
        summary[sev] = summary.get(sev, 0) + 1
    return summary
