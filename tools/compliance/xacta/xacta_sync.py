#!/usr/bin/env python3
# CUI // SP-CTI
"""Xacta 360 sync orchestrator — coordinates data flow between ICDEV and Xacta.

Supports three modes:
    - api: Push data directly via Xacta 360 REST API
    - export: Generate OSCAL/CSV files for batch import
    - hybrid: Try API first, fall back to export on failure

Usage:
    python tools/compliance/xacta/xacta_sync.py --project-id proj-123 --mode hybrid
    python tools/compliance/xacta/xacta_sync.py --project-id proj-123 --mode export
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

sys.path.insert(0, str(BASE_DIR))


def _get_connection(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _log_audit(conn, project_id, action, details=None):
    """Log sync event to audit trail."""
    conn.execute(
        """INSERT INTO audit_trail
           (project_id, event_type, actor, action, details, classification, created_at)
           VALUES (?, 'xacta_sync_completed', 'icdev-xacta-sync', ?, ?, 'CUI', datetime('now'))""",
        (project_id, action, json.dumps(details) if details else None),
    )
    conn.commit()


def _load_project_data(conn, project_id):
    """Load all project compliance data for sync."""
    data = {}

    # Project info
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found.")
    data["project"] = dict(row)

    # Control implementations
    rows = conn.execute(
        "SELECT * FROM project_controls WHERE project_id = ? ORDER BY control_id",
        (project_id,),
    ).fetchall()
    data["controls"] = [dict(r) for r in rows]

    # CSSP assessments
    rows = conn.execute(
        "SELECT * FROM cssp_assessments WHERE project_id = ? ORDER BY requirement_id",
        (project_id,),
    ).fetchall()
    data["cssp_assessments"] = [dict(r) for r in rows]

    # STIG findings
    rows = conn.execute(
        "SELECT * FROM stig_findings WHERE project_id = ? ORDER BY severity, finding_id",
        (project_id,),
    ).fetchall()
    data["stig_findings"] = [dict(r) for r in rows]

    # POA&M items
    rows = conn.execute(
        "SELECT * FROM poam_items WHERE project_id = ? ORDER BY severity",
        (project_id,),
    ).fetchall()
    data["poam_items"] = [dict(r) for r in rows]

    # Certification status
    row = conn.execute(
        "SELECT * FROM cssp_certifications WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    data["certification"] = dict(row) if row else None

    # Vulnerability management
    rows = conn.execute(
        "SELECT * FROM cssp_vuln_management WHERE project_id = ? ORDER BY scan_date DESC LIMIT 10",
        (project_id,),
    ).fetchall()
    data["vuln_scans"] = [dict(r) for r in rows]

    return data


def _sync_via_api(project_id, data, db_path=None):
    """Sync data to Xacta via REST API.

    Returns:
        Dict with sync results per data type.
    """
    try:
        from tools.compliance.xacta.xacta_client import XactaClient
    except ImportError:
        return {"status": "error", "error": "XactaClient import failed", "fallback": True}

    client = XactaClient(db_path=db_path)
    results = {"mode": "api", "steps": {}}

    try:
        # Step 1: Push/update system
        result = client.push_system(data["project"])
        results["steps"]["push_system"] = result
        if result and result.get("status") == "error":
            results["status"] = "partial"

        # Step 2: Push control implementations
        if data["controls"]:
            result = client.push_controls(project_id, data["controls"])
            results["steps"]["push_controls"] = {
                "count": len(data["controls"]),
                "result": result,
            }

        # Step 3: Push CSSP assessment results
        if data["cssp_assessments"]:
            result = client.push_assessment(project_id, data["cssp_assessments"])
            results["steps"]["push_assessment"] = {
                "count": len(data["cssp_assessments"]),
                "result": result,
            }

        # Step 4: Push STIG findings
        if data["stig_findings"]:
            result = client.push_findings(project_id, data["stig_findings"])
            results["steps"]["push_findings"] = {
                "count": len(data["stig_findings"]),
                "result": result,
            }

        # Step 5: Push POA&M items
        if data["poam_items"]:
            result = client.push_poam(project_id, data["poam_items"])
            results["steps"]["push_poam"] = {
                "count": len(data["poam_items"]),
                "result": result,
            }

        # Step 6: Pull back certification status
        cert_status = client.get_certification_status(project_id)
        if cert_status and cert_status.get("status") != "error":
            results["steps"]["pull_certification"] = cert_status
            # Update local certification record
            conn = _get_connection(db_path)
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO cssp_certifications
                       (project_id, status, xacta_system_id, last_xacta_sync, updated_at)
                       VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
                    (
                        project_id,
                        cert_status.get("certification_status", "in_progress"),
                        cert_status.get("xacta_system_id", ""),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        results["status"] = "success"

    except Exception as e:
        results["status"] = "error"
        results["error"] = str(e)
        results["fallback"] = True

    finally:
        client.close()

    return results


def _sync_via_export(project_id, output_dir=None, db_path=None):
    """Sync data to Xacta via file export.

    Returns:
        Dict with export results.
    """
    try:
        from tools.compliance.xacta.xacta_export import export_all
    except ImportError:
        return {"status": "error", "error": "xacta_export import failed"}

    result = export_all(project_id, export_format="all", output_dir=output_dir, db_path=db_path)
    result["mode"] = "export"
    result["status"] = "success"
    return result


def sync_to_xacta(project_id, mode="hybrid", output_dir=None, db_path=None):
    """Orchestrate full sync between ICDEV and Xacta 360.

    Args:
        project_id: ICDEV project ID
        mode: Sync mode — "api", "export", or "hybrid"
        output_dir: Output directory for export mode
        db_path: Database path

    Returns:
        Dict with comprehensive sync results.
    """
    conn = _get_connection(db_path)
    sync_start = datetime.utcnow().isoformat()

    try:
        # Load all project data
        data = _load_project_data(conn, project_id)

        summary = {
            "project_id": project_id,
            "mode": mode,
            "sync_start": sync_start,
            "data_counts": {
                "controls": len(data["controls"]),
                "cssp_assessments": len(data["cssp_assessments"]),
                "stig_findings": len(data["stig_findings"]),
                "poam_items": len(data["poam_items"]),
                "vuln_scans": len(data["vuln_scans"]),
            },
            "results": {},
        }

        if mode == "api":
            summary["results"] = _sync_via_api(project_id, data, db_path)

        elif mode == "export":
            summary["results"] = _sync_via_export(project_id, output_dir, db_path)

        elif mode == "hybrid":
            # Try API first
            api_result = _sync_via_api(project_id, data, db_path)
            summary["results"]["api"] = api_result

            # Fall back to export if API failed
            if api_result.get("fallback") or api_result.get("status") == "error":
                print("API sync failed, falling back to export mode...")
                export_result = _sync_via_export(project_id, output_dir, db_path)
                summary["results"]["export"] = export_result
                summary["mode_used"] = "export (fallback)"
            else:
                summary["mode_used"] = "api"

        summary["sync_end"] = datetime.utcnow().isoformat()
        summary["status"] = "completed"

        # Update last sync timestamp
        conn.execute(
            """INSERT OR REPLACE INTO cssp_certifications
               (project_id, last_xacta_sync, updated_at)
               VALUES (
                   ?,
                   datetime('now'),
                   datetime('now')
               )
               ON CONFLICT(project_id) DO UPDATE SET
                   last_xacta_sync = datetime('now'),
                   updated_at = datetime('now')""",
            (project_id,),
        )
        conn.commit()

        # Log sync completion
        _log_audit(conn, project_id, f"sync_completed_{mode}", {
            "mode": mode,
            "data_counts": summary["data_counts"],
            "status": summary["status"],
        })

        return summary

    except Exception as e:
        error_result = {
            "project_id": project_id,
            "mode": mode,
            "status": "error",
            "error": str(e),
            "sync_start": sync_start,
            "sync_end": datetime.utcnow().isoformat(),
        }
        try:
            _log_audit(conn, project_id, f"sync_failed_{mode}", {"error": str(e)})
        except Exception:
            pass
        return error_result

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Sync ICDEV compliance data to Xacta 360"
    )
    parser.add_argument("--project-id", required=True, help="ICDEV project ID")
    parser.add_argument(
        "--mode", default="hybrid", choices=["api", "export", "hybrid"],
        help="Sync mode (default: hybrid — try API, fall back to export)"
    )
    parser.add_argument("--output-dir", type=Path, help="Output directory for exports")
    parser.add_argument("--db-path", type=Path, default=DB_PATH, help="Database path")
    args = parser.parse_args()

    result = sync_to_xacta(args.project_id, args.mode, args.output_dir, args.db_path)
    print(json.dumps(result, indent=2, default=str))

    if result.get("status") == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()

# CUI // SP-CTI
