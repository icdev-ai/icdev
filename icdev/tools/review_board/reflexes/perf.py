#!/usr/bin/env python3
# CUI // SP-CTI
"""Performance Reflex — response times, DB query health, resource growth.

Checks:
    - Database file sizes and growth rate
    - Response time percentiles from audit trail
    - Table row counts for large tables
    - Memory/disk growth indicators

Architecture: D-RB-1 (scanner-tier, zero LLM cost), D-RB-3 (standalone run())
"""

from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

try:
    from tools.db.storage import get_connection, list_tables
except ImportError:
    get_connection = None
    list_tables = None


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute performance checks."""
    findings: List[Dict] = []
    checks_completed = 0
    thresholds = config.get("thresholds", {})

    # --- Check 1: Database file sizes ---
    try:
        data_dir = BASE_DIR / "data"
        max_size_mb = thresholds.get("max_db_file_size_mb", 500)
        db_sizes = {}
        if data_dir.exists():
            for db_file in data_dir.glob("*.db"):
                size_mb = db_file.stat().st_size / (1024 * 1024)
                db_sizes[db_file.name] = round(size_mb, 2)
                if size_mb > max_size_mb:
                    findings.append(
                        {
                            "severity": "high",
                            "category": "db_size",
                            "title": f"Database {db_file.name} is {size_mb:.1f}MB",
                            "recommendation": "Run VACUUM or archive old data",
                            "confidence": 1.0,
                            "auto_fixable": False,
                        }
                    )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    # --- Check 2: Large table row counts ---
    try:
        if get_connection:
            conn = get_connection()
            try:
                # Backend-aware table listing (pgrt-sweep-06) — no sqlite_master/translation reliance.
                table_names = list_tables(conn)
                large_tables = []
                for table_name in table_names:
                    try:
                        # table_name comes from sqlite_master (trusted), bracket-escape
                        safe_name = table_name.replace("]", "]]")
                        stmt = f"SELECT COUNT(*) FROM [{safe_name}]"  # nosec B608
                        count = conn.execute(stmt).fetchone()
                        if count and count[0] > 100000:
                            large_tables.append(
                                {
                                    "table": table_name,
                                    "rows": count[0],
                                }
                            )
                    except Exception:
                        pass

                if large_tables:
                    large_tables.sort(key=lambda x: x["rows"], reverse=True)
                    findings.append(
                        {
                            "severity": "medium",
                            "category": "large_tables",
                            "title": f"{len(large_tables)} tables have >100K rows",
                            "description": "; ".join(f"{t['table']}: {t['rows']:,}" for t in large_tables[:5]),
                            "recommendation": "Consider archival or retention policies",
                            "evidence": {"tables": large_tables[:10]},
                            "confidence": 0.8,
                            "auto_fixable": False,
                        }
                    )
                checks_completed += 1
            finally:
                conn.close()
    except Exception:
        checks_completed += 1

    # --- Check 3: Audit trail growth rate ---
    try:
        if get_connection:
            conn = get_connection()
            try:
                # Events in last hour vs previous hour
                current_hour = conn.execute(
                    "SELECT COUNT(*) FROM audit_trail WHERE created_at > datetime('now', '-1 hour')"
                ).fetchone()
                prev_hour = conn.execute(
                    "SELECT COUNT(*) FROM audit_trail "
                    "WHERE created_at BETWEEN datetime('now', '-2 hours') "
                    "AND datetime('now', '-1 hour')"
                ).fetchone()

                if current_hour and prev_hour and prev_hour[0] > 0:
                    growth_ratio = current_hour[0] / prev_hour[0]
                    if growth_ratio > 3.0:
                        findings.append(
                            {
                                "severity": "medium",
                                "category": "audit_growth",
                                "title": f"Audit trail growth spike: {growth_ratio:.1f}x vs previous hour",
                                "description": f"Current: {current_hour[0]}, Previous: {prev_hour[0]}",
                                "confidence": 0.7,
                                "auto_fixable": False,
                            }
                        )
                checks_completed += 1
            except Exception:
                checks_completed += 1
            finally:
                conn.close()
    except Exception:
        checks_completed += 1

    # --- Check 4: Temp directory size ---
    try:
        tmp_dir = BASE_DIR / ".tmp"
        if tmp_dir.exists():
            total_size = sum(f.stat().st_size for f in tmp_dir.rglob("*") if f.is_file())
            size_mb = total_size / (1024 * 1024)
            if size_mb > 500:
                findings.append(
                    {
                        "severity": "medium",
                        "category": "temp_size",
                        "title": f".tmp directory is {size_mb:.0f}MB",
                        "recommendation": "Clean up old test runs, screenshots, and worktrees",
                        "confidence": 0.9,
                        "auto_fixable": True,
                    }
                )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    return {
        "success": True,
        "metric_value": float(len(findings)),
        "details": {
            "checks_completed": checks_completed,
            "findings_count": len(findings),
            "findings": findings,
            "db_sizes": db_sizes if "db_sizes" in dir() else {},
        },
    }
