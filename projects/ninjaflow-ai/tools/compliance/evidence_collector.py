#!/usr/bin/env python3
# CUI // SP-CTI
"""Universal Compliance Evidence Auto-Collector (Phase 56, D347).

Extends the cssp_evidence_collector.py pattern to all compliance frameworks.
Uses the crosswalk engine for multi-framework evidence mapping. Collects
evidence from both file system artifacts and database records.

Architecture Decisions:
  D347: Evidence collector extends cssp_evidence_collector.py pattern to all
        frameworks. Uses crosswalk engine for multi-framework mapping.

Usage:
  python tools/compliance/evidence_collector.py --project-id "proj-123" --json
  python tools/compliance/evidence_collector.py --project-id "proj-123" --freshness --json
  python tools/compliance/evidence_collector.py --project-id "proj-123" --framework fedramp --json
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Evidence category definitions — maps frameworks to DB tables and file patterns
# ---------------------------------------------------------------------------
FRAMEWORK_EVIDENCE_MAP: Dict[str, Dict[str, Any]] = {
    "nist_800_53": {
        "description": "NIST 800-53 Rev 5 Control Evidence",
        "tables": ["control_implementations", "audit_trail", "stig_results"],
        "file_patterns": ["**/ssp_*.json", "**/poam_*.json", "**/stig_*.json"],
        "required": True,
    },
    "fedramp": {
        "description": "FedRAMP Authorization Evidence",
        "tables": ["fedramp_assessments", "control_implementations", "oscal_validation_log"],
        "file_patterns": ["**/fedramp_*.json", "**/oscal_*.json"],
        "required": True,
    },
    "cmmc": {
        "description": "CMMC Level 2/3 Practice Evidence",
        "tables": ["cmmc_assessments", "control_implementations"],
        "file_patterns": ["**/cmmc_*.json"],
        "required": False,
    },
    "hipaa": {
        "description": "HIPAA Security Rule Safeguard Evidence",
        "tables": ["hipaa_assessments"],
        "file_patterns": ["**/hipaa_*.json"],
        "required": False,
    },
    "cjis": {
        "description": "CJIS Security Policy Evidence",
        "tables": ["cjis_assessments"],
        "file_patterns": ["**/cjis_*.json"],
        "required": False,
    },
    "pci_dss": {
        "description": "PCI DSS v4.0 Evidence",
        "tables": ["pci_dss_assessments"],
        "file_patterns": ["**/pci_*.json"],
        "required": False,
    },
    "iso27001": {
        "description": "ISO/IEC 27001:2022 Evidence",
        "tables": ["iso27001_assessments"],
        "file_patterns": ["**/iso27001_*.json"],
        "required": False,
    },
    "soc2": {
        "description": "SOC 2 Type II Trust Criteria Evidence",
        "tables": ["soc2_assessments"],
        "file_patterns": ["**/soc2_*.json"],
        "required": False,
    },
    "nist_800_207": {
        "description": "NIST 800-207 Zero Trust Architecture Evidence",
        "tables": ["nist_800_207_assessments", "zta_maturity_scores"],
        "file_patterns": ["**/zta_*.json"],
        "required": False,
    },
    "atlas": {
        "description": "MITRE ATLAS AI Threat Evidence",
        "tables": ["atlas_assessments", "atlas_red_team_results"],
        "file_patterns": ["**/atlas_*.json"],
        "required": False,
    },
    "ai_transparency": {
        "description": "AI Transparency & Accountability Evidence",
        "tables": [
            "omb_m25_21_assessments", "omb_m26_04_assessments",
            "nist_ai_600_1_assessments", "gao_ai_assessments",
            "model_cards", "system_cards", "ai_use_case_inventory",
        ],
        "file_patterns": ["**/ai_transparency_*.json", "**/model_card_*.json"],
        "required": False,
    },
    "sbom": {
        "description": "Software Bill of Materials Evidence",
        "tables": ["sbom_records"],
        "file_patterns": ["**/sbom_*.json", "**/bom.json"],
        "required": True,
    },
    "audit_trail": {
        "description": "Audit Trail Completeness",
        "tables": ["audit_trail"],
        "file_patterns": [],
        "required": True,
    },
}


def _get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get SQLite connection with Row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check if a table exists in the database."""
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row[0] > 0


def _count_project_records(
    conn: sqlite3.Connection, table_name: str, project_id: str
) -> Dict[str, Any]:
    """Count records for a project in a table, with freshness info."""
    if not _table_exists(conn, table_name):
        return {"table": table_name, "exists": False, "count": 0, "latest": None}

    # Check if table has project_id column
    cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
    if "project_id" not in cols:
        # Table without project_id — count all records
        row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
        count = row[0]
        # Try to get latest timestamp
        latest = None
        for ts_col in ["created_at", "collected_at", "assessed_at", "timestamp"]:
            if ts_col in cols:
                ts_row = conn.execute(
                    f"SELECT MAX({ts_col}) FROM {table_name}"
                ).fetchone()
                latest = ts_row[0] if ts_row else None
                break
        return {"table": table_name, "exists": True, "count": count, "latest": latest}

    row = conn.execute(
        f"SELECT COUNT(*) FROM {table_name} WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    count = row[0]

    # Get latest record timestamp
    latest = None
    for ts_col in ["created_at", "collected_at", "assessed_at", "timestamp"]:
        if ts_col in cols:
            ts_row = conn.execute(
                f"SELECT MAX({ts_col}) FROM {table_name} WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            latest = ts_row[0] if ts_row else None
            break

    return {"table": table_name, "exists": True, "count": count, "latest": latest}


def _hash_file(file_path: Path) -> Optional[str]:
    """Compute SHA-256 hash of a file."""
    try:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except (IOError, OSError):
        return None


def _compute_age_hours(timestamp_str: Optional[str]) -> Optional[float]:
    """Compute age in hours from a timestamp string."""
    if not timestamp_str:
        return None
    try:
        # Handle both ISO format and SQLite datetime format
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"]:
            try:
                ts = datetime.strptime(timestamp_str.replace("+00:00", "").replace("Z", ""), fmt)
                ts = ts.replace(tzinfo=timezone.utc)
                delta = datetime.now(timezone.utc) - ts
                return delta.total_seconds() / 3600
            except ValueError:
                continue
    except Exception:
        pass
    return None


def collect_evidence(
    project_id: str,
    project_dir: Optional[Path] = None,
    framework: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Collect compliance evidence across all frameworks.

    Args:
        project_id: Project identifier.
        project_dir: Optional project directory for file scanning.
        framework: Optional specific framework to collect for.
        db_path: Override database path.

    Returns:
        Evidence collection manifest with coverage metrics.
    """
    conn = _get_connection(db_path)
    frameworks = FRAMEWORK_EVIDENCE_MAP
    if framework:
        if framework not in frameworks:
            return {"error": f"Unknown framework: {framework}", "available": list(frameworks.keys())}
        frameworks = {framework: frameworks[framework]}

    results = {}
    total_tables = 0
    tables_with_data = 0
    total_files = 0

    for fw_id, fw_config in frameworks.items():
        fw_result = {
            "framework": fw_id,
            "description": fw_config["description"],
            "required": fw_config["required"],
            "db_evidence": [],
            "file_evidence": [],
            "status": "no_evidence",
        }

        # Check DB tables
        fw_table_count = 0
        fw_tables_with_data = 0
        for table_name in fw_config["tables"]:
            total_tables += 1
            record_info = _count_project_records(conn, table_name, project_id)
            fw_result["db_evidence"].append(record_info)
            fw_table_count += 1
            if record_info["count"] > 0:
                tables_with_data += 1
                fw_tables_with_data += 1

        # Scan files if project_dir provided
        if project_dir and Path(project_dir).exists():
            for pattern in fw_config["file_patterns"]:
                for match in Path(project_dir).glob(pattern):
                    total_files += 1
                    fw_result["file_evidence"].append({
                        "path": str(match),
                        "name": match.name,
                        "size": match.stat().st_size if match.exists() else 0,
                        "hash": _hash_file(match),
                    })

        # Determine status
        has_db = fw_tables_with_data > 0
        has_files = len(fw_result["file_evidence"]) > 0
        if has_db and has_files:
            fw_result["status"] = "evidence_found"
        elif has_db or has_files:
            fw_result["status"] = "partial"
        else:
            fw_result["status"] = "no_evidence"

        results[fw_id] = fw_result

    conn.close()

    # Compute summary
    frameworks_with_evidence = sum(
        1 for r in results.values() if r["status"] in ("evidence_found", "partial")
    )
    required_with_evidence = sum(
        1 for r in results.values()
        if r["required"] and r["status"] in ("evidence_found", "partial")
    )
    required_total = sum(1 for r in results.values() if r["required"])
    coverage_pct = (
        round(frameworks_with_evidence / len(results) * 100, 1) if results else 0
    )

    return {
        "project_id": project_id,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "frameworks": results,
        "summary": {
            "total_frameworks": len(results),
            "frameworks_with_evidence": frameworks_with_evidence,
            "required_frameworks": required_total,
            "required_with_evidence": required_with_evidence,
            "coverage_pct": coverage_pct,
            "total_db_tables_checked": total_tables,
            "tables_with_data": tables_with_data,
            "total_file_artifacts": total_files,
        },
    }


def check_freshness(
    project_id: str,
    max_age_hours: float = 48.0,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Check evidence freshness across all frameworks.

    Args:
        project_id: Project identifier.
        max_age_hours: Maximum acceptable age in hours.
        db_path: Override database path.

    Returns:
        Freshness report with stale/fresh/missing status per framework.
    """
    conn = _get_connection(db_path)
    results = {}
    stale_count = 0
    fresh_count = 0
    missing_count = 0

    for fw_id, fw_config in FRAMEWORK_EVIDENCE_MAP.items():
        latest_timestamp = None
        total_records = 0

        for table_name in fw_config["tables"]:
            info = _count_project_records(conn, table_name, project_id)
            total_records += info["count"]
            if info["latest"]:
                if latest_timestamp is None or info["latest"] > latest_timestamp:
                    latest_timestamp = info["latest"]

        age_hours = _compute_age_hours(latest_timestamp)

        if total_records == 0:
            status = "missing"
            missing_count += 1
        elif age_hours is not None and age_hours > max_age_hours:
            status = "stale"
            stale_count += 1
        else:
            status = "fresh"
            fresh_count += 1

        results[fw_id] = {
            "framework": fw_id,
            "description": fw_config["description"],
            "required": fw_config["required"],
            "total_records": total_records,
            "latest_timestamp": latest_timestamp,
            "age_hours": round(age_hours, 1) if age_hours is not None else None,
            "status": status,
        }

    conn.close()

    # Overall health
    required_stale = sum(
        1 for r in results.values() if r["required"] and r["status"] == "stale"
    )
    required_missing = sum(
        1 for r in results.values() if r["required"] and r["status"] == "missing"
    )
    if required_stale > 0 or required_missing > 0:
        overall = "unhealthy"
    elif stale_count > 0:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "project_id": project_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "max_age_hours": max_age_hours,
        "overall_status": overall,
        "frameworks": results,
        "summary": {
            "fresh": fresh_count,
            "stale": stale_count,
            "missing": missing_count,
            "total": len(results),
            "required_stale": required_stale,
            "required_missing": required_missing,
        },
    }


def list_frameworks() -> List[Dict[str, Any]]:
    """List all supported evidence frameworks."""
    return [
        {
            "id": fw_id,
            "description": fw_config["description"],
            "required": fw_config["required"],
            "tables": fw_config["tables"],
            "file_patterns": fw_config["file_patterns"],
        }
        for fw_id, fw_config in FRAMEWORK_EVIDENCE_MAP.items()
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def run_cli():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Universal Compliance Evidence Auto-Collector (D347)"
    )
    parser.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument("--project-dir", help="Project directory for file scanning")
    parser.add_argument("--framework", help="Specific framework to collect for")
    parser.add_argument("--freshness", action="store_true", help="Check evidence freshness")
    parser.add_argument("--max-age-hours", type=float, default=48.0, help="Max acceptable age (hours)")
    parser.add_argument("--list-frameworks", action="store_true", help="List supported frameworks")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    if args.list_frameworks:
        frameworks = list_frameworks()
        if args.json:
            print(json.dumps({"frameworks": frameworks, "total": len(frameworks)}, indent=2))
        else:
            for fw in frameworks:
                req = " [REQUIRED]" if fw["required"] else ""
                print(f"  {fw['id']}: {fw['description']}{req}")
        return

    if args.freshness:
        result = check_freshness(
            project_id=args.project_id,
            max_age_hours=args.max_age_hours,
        )
    else:
        result = collect_evidence(
            project_id=args.project_id,
            project_dir=Path(args.project_dir) if args.project_dir else None,
            framework=args.framework,
        )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    run_cli()
