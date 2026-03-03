#!/usr/bin/env python3
# CUI // SP-CTI
"""eMASS (Enterprise Mission Assurance Support Service) file-based export.

Generates CSV exports and ZIP artifact packages compatible with eMASS
bulk import functionality. Used as fallback when the eMASS REST API is
unavailable or for manual upload workflows.

Export types:
    - Controls CSV: Control implementations in eMASS import format
    - POA&M CSV: Plan of Action & Milestones in eMASS import format
    - Artifacts ZIP: Bundled compliance artifacts (SSP, POAM, STIG, SBOM, OSCAL)
    - Test Results CSV: SAST, dependency, and container scan results
    - All: Run every export in one pass

Usage:
    python tools/compliance/emass/emass_export.py --project-id proj-123 --type controls
    python tools/compliance/emass/emass_export.py --project-id proj-123 --type poam
    python tools/compliance/emass/emass_export.py --project-id proj-123 --type artifacts
    python tools/compliance/emass/emass_export.py --project-id proj-123 --type test-results
    python tools/compliance/emass/emass_export.py --project-id proj-123 --type all
    python tools/compliance/emass/emass_export.py --project-id proj-123 --type all --output-dir .tmp/emass
"""

import argparse
import csv
import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


# ============================================================
# Database helpers
# ============================================================

def _get_connection(db_path=None):
    """Get a database connection.

    Args:
        db_path: Optional override for database file path.

    Returns:
        sqlite3.Connection with row_factory set to sqlite3.Row.

    Raises:
        FileNotFoundError: If the database file does not exist.
    """
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_project(conn, project_id):
    """Load project data from the database.

    Args:
        conn: Active database connection.
        project_id: ICDEV project identifier.

    Returns:
        Dict of project row data.

    Raises:
        ValueError: If the project is not found.
    """
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found.")
    return dict(row)


def _log_audit(conn, project_id, action, details=None):
    """Log an eMASS export event to the immutable audit trail.

    Args:
        conn: Active database connection.
        project_id: ICDEV project identifier.
        action: Short action description (e.g., ``export_controls``).
        details: Optional dict of additional context.
    """
    conn.execute(
        """INSERT INTO audit_trail
           (project_id, event_type, actor, action, details, classification, created_at)
           VALUES (?, 'emass_push', 'icdev-emass-export', ?, ?, 'CUI', datetime('now'))""",
        (project_id, action, json.dumps(details) if details else None),
    )
    conn.commit()


def _ensure_output_dir(project_id, output_dir=None):
    """Ensure the output directory exists and return it.

    Args:
        project_id: ICDEV project identifier (used for default sub-path).
        output_dir: Explicit output directory. Falls back to
                    ``compliance/emass-exports/<project_id>``.

    Returns:
        pathlib.Path to the output directory.
    """
    if output_dir:
        out = Path(output_dir)
    else:
        out = BASE_DIR / "compliance" / "emass-exports" / project_id
    out.mkdir(parents=True, exist_ok=True)
    return out


# ============================================================
# Status mapping helpers
# ============================================================

def _map_impl_status(status):
    """Map ICDEV implementation status to eMASS-compatible value.

    eMASS accepts: Planned, Implemented, Inherited, Not Applicable,
    Manually Inherited.

    Args:
        status: ICDEV-internal implementation status string.

    Returns:
        eMASS-compatible status string.
    """
    mapping = {
        "planned": "Planned",
        "implemented": "Implemented",
        "partially_implemented": "Planned",
        "not_implemented": "Planned",
        "inherited": "Inherited",
        "not_applicable": "Not Applicable",
        "manually_inherited": "Manually Inherited",
    }
    if not status:
        return "Planned"
    return mapping.get(status.lower().strip(), status)


def _map_poam_status(status):
    """Map ICDEV POA&M status to eMASS-compatible value.

    eMASS accepts: Ongoing, Completed, Risk Accepted, Delayed, Cancelled.

    Args:
        status: ICDEV-internal POA&M status string.

    Returns:
        eMASS-compatible POA&M status string.
    """
    mapping = {
        "open": "Ongoing",
        "ongoing": "Ongoing",
        "in_progress": "Ongoing",
        "closed": "Completed",
        "completed": "Completed",
        "risk_accepted": "Risk Accepted",
        "delayed": "Delayed",
        "cancelled": "Cancelled",
        "mitigated": "Completed",
    }
    if not status:
        return "Ongoing"
    return mapping.get(status.lower().strip(), status)


def _map_severity(severity):
    """Map ICDEV severity to eMASS-compatible severity value.

    eMASS accepts: Very High, High, Moderate, Low, Very Low.

    Args:
        severity: ICDEV-internal severity string.

    Returns:
        eMASS-compatible severity string.
    """
    mapping = {
        "critical": "Very High",
        "cat1": "Very High",
        "cat_1": "Very High",
        "very_high": "Very High",
        "high": "High",
        "cat2": "High",
        "cat_2": "High",
        "moderate": "Moderate",
        "medium": "Moderate",
        "cat3": "Moderate",
        "cat_3": "Moderate",
        "low": "Low",
        "very_low": "Very Low",
        "informational": "Very Low",
    }
    if not severity:
        return "Moderate"
    return mapping.get(severity.lower().strip(), severity)


def _map_compliance_status(status):
    """Map ICDEV scan/test status to eMASS compliance status.

    eMASS accepts: Compliant, Non-Compliant, Not Applicable.

    Args:
        status: ICDEV-internal test/scan status string.

    Returns:
        eMASS-compatible compliance status string.
    """
    mapping = {
        "pass": "Compliant",
        "passed": "Compliant",
        "compliant": "Compliant",
        "satisfied": "Compliant",
        "open": "Non-Compliant",
        "fail": "Non-Compliant",
        "failed": "Non-Compliant",
        "non-compliant": "Non-Compliant",
        "non_compliant": "Non-Compliant",
        "not_satisfied": "Non-Compliant",
        "not_applicable": "Not Applicable",
        "n/a": "Not Applicable",
    }
    if not status:
        return "Non-Compliant"
    return mapping.get(status.lower().strip(), status)


# ============================================================
# Controls Export
# ============================================================

def export_controls_emass(project_id, output_dir=None, db_path=None):
    """Export control implementations to eMASS-compatible CSV format.

    Generates a CSV file with columns matching the eMASS bulk import
    template for security control implementations.

    Columns:
        Control Number, Control Name, Implementation Status,
        Responsible Entity, Implementation Description,
        Evidence Reference, Assessment Date

    Args:
        project_id: ICDEV project ID.
        output_dir: Output directory (optional).
        db_path: Database path (optional).

    Returns:
        Path to generated CSV file.
    """
    conn = _get_connection(db_path)
    try:
        _get_project(conn, project_id)
        controls = conn.execute(
            "SELECT * FROM project_controls WHERE project_id = ? ORDER BY control_id",
            (project_id,),
        ).fetchall()

        out_dir = _ensure_output_dir(project_id, output_dir)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_file = out_dir / f"emass-controls-{project_id}-{timestamp}.csv"

        with open(out_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            # eMASS bulk import header row
            writer.writerow([
                "Control Number",
                "Control Name",
                "Implementation Status",
                "Responsible Entity",
                "Implementation Description",
                "Evidence Reference",
                "Assessment Date",
            ])
            for row in controls:
                r = dict(row)
                writer.writerow([
                    r.get("control_id", ""),
                    r.get("control_name", r.get("control_id", "")),
                    _map_impl_status(r.get("implementation_status", "")),
                    r.get("responsible_role", r.get("responsible_entity", "")),
                    r.get("implementation_description", "Planned"),
                    r.get("evidence_reference", r.get("evidence_path", "")),
                    r.get("assessment_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                ])

        _log_audit(conn, project_id, "export_controls_emass", {
            "control_count": len(controls),
            "output_file": str(out_file),
            "format": "csv",
        })

        print(f"[eMASS Export] Controls CSV: {out_file} ({len(controls)} controls)")
        return str(out_file)

    finally:
        conn.close()


# ============================================================
# POA&M Export
# ============================================================

def export_poam_emass(project_id, output_dir=None, db_path=None):
    """Export POA&M items in eMASS import format (CSV).

    Generates a CSV file with columns matching the eMASS bulk import
    template for Plan of Action & Milestones items.

    Columns:
        POAM ID, Weakness Name, Weakness Source, Severity,
        Scheduled Completion Date, Milestone Description,
        Status, Resources Required

    Args:
        project_id: ICDEV project ID.
        output_dir: Output directory (optional).
        db_path: Database path (optional).

    Returns:
        Path to generated CSV file.
    """
    conn = _get_connection(db_path)
    try:
        _get_project(conn, project_id)
        items = conn.execute(
            "SELECT * FROM poam_items WHERE project_id = ? ORDER BY severity, weakness_id",
            (project_id,),
        ).fetchall()

        out_dir = _ensure_output_dir(project_id, output_dir)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_file = out_dir / f"emass-poam-{project_id}-{timestamp}.csv"

        with open(out_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            # eMASS POA&M import header row
            writer.writerow([
                "POAM ID",
                "Weakness Name",
                "Weakness Source",
                "Severity",
                "Scheduled Completion Date",
                "Milestone Description",
                "Status",
                "Resources Required",
            ])
            for row in items:
                r = dict(row)
                # Build a milestone description from corrective action or fallback
                milestone = r.get("corrective_action", "")
                if not milestone:
                    milestone = r.get("weakness_description", "Remediation planned")

                writer.writerow([
                    r.get("weakness_id", r.get("poam_id", "")),
                    r.get("weakness_description", r.get("weakness_name", "")),
                    r.get("source", ""),
                    _map_severity(r.get("severity", "")),
                    r.get("milestone_date", r.get("scheduled_completion_date", "")),
                    milestone,
                    _map_poam_status(r.get("status", "")),
                    r.get("resources_required", ""),
                ])

        _log_audit(conn, project_id, "export_poam_emass", {
            "poam_count": len(items),
            "output_file": str(out_file),
            "format": "csv",
        })

        print(f"[eMASS Export] POA&M CSV: {out_file} ({len(items)} items)")
        return str(out_file)

    finally:
        conn.close()


# ============================================================
# Artifacts Export (ZIP archive)
# ============================================================

def export_artifacts_emass(project_id, output_dir=None, db_path=None):
    """Package all compliance artifacts into a ZIP archive for eMASS upload.

    Bundles SSP documents, POA&M exports, STIG checklists, SBOM records,
    and OSCAL JSON files into a single ZIP with a classification marker
    and metadata manifest suitable for eMASS artifact upload.

    Args:
        project_id: ICDEV project ID.
        output_dir: Output directory (optional).
        db_path: Database path (optional).

    Returns:
        Path to generated ZIP file.
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)
        out_dir = _ensure_output_dir(project_id, output_dir)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        zip_path = out_dir / f"emass-artifacts-{project_id}-{timestamp}.zip"

        artifact_manifest = []
        file_count = 0

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # --- SSP documents ---
            ssp_rows = conn.execute(
                "SELECT file_path FROM ssp_documents WHERE project_id = ? AND file_path IS NOT NULL",
                (project_id,),
            ).fetchall()
            for row in ssp_rows:
                fp = Path(row["file_path"])
                if fp.exists():
                    arc_name = f"ssp/{fp.name}"
                    zf.write(fp, arc_name)
                    artifact_manifest.append({
                        "filename": fp.name,
                        "type": "System Security Plan",
                        "category": "Authorization Package",
                        "path": arc_name,
                    })
                    file_count += 1

            # --- SBOM records ---
            sbom_rows = conn.execute(
                "SELECT file_path FROM sbom_records WHERE project_id = ? AND file_path IS NOT NULL",
                (project_id,),
            ).fetchall()
            for row in sbom_rows:
                fp = Path(row["file_path"])
                if fp.exists():
                    arc_name = f"sbom/{fp.name}"
                    zf.write(fp, arc_name)
                    artifact_manifest.append({
                        "filename": fp.name,
                        "type": "SBOM",
                        "category": "Supply Chain",
                        "path": arc_name,
                    })
                    file_count += 1

            # --- STIG checklists ---
            stig_rows = conn.execute(
                "SELECT DISTINCT stig_id, file_path FROM stig_findings "
                "WHERE project_id = ? AND file_path IS NOT NULL",
                (project_id,),
            ).fetchall()
            for row in stig_rows:
                fp = Path(row["file_path"])
                if fp.exists():
                    arc_name = f"stig/{fp.name}"
                    zf.write(fp, arc_name)
                    artifact_manifest.append({
                        "filename": fp.name,
                        "type": "STIG Checklist",
                        "category": "Assessment Evidence",
                        "path": arc_name,
                    })
                    file_count += 1

            # --- OSCAL files from compliance/emass-exports or xacta-exports ---
            for exports_subdir in ["emass-exports", "xacta-exports"]:
                oscal_dir = BASE_DIR / "compliance" / exports_subdir / project_id
                if oscal_dir.exists():
                    for oscal_file in oscal_dir.glob("oscal-*.json"):
                        arc_name = f"oscal/{oscal_file.name}"
                        zf.write(oscal_file, arc_name)
                        artifact_manifest.append({
                            "filename": oscal_file.name,
                            "type": "OSCAL Document",
                            "category": "Machine-Readable Compliance",
                            "path": arc_name,
                        })
                        file_count += 1

            # --- Classification marker (required for CUI handling) ---
            zf.writestr(
                "CLASSIFICATION.txt",
                "CUI // SP-CTI\n"
                "This artifact package contains Controlled Unclassified Information.\n"
                "Handle in accordance with DoD CUI policy (DoDI 5200.48).\n"
                "Dissemination is limited to authorized personnel only.\n"
            )

            # --- Package metadata / manifest ---
            metadata = {
                "project_id": project_id,
                "project_name": project.get("name", project_id),
                "export_date": datetime.now(timezone.utc).isoformat() + "Z",
                "classification": "CUI // SP-CTI",
                "source": "ICDEV Compliance Engine",
                "target_system": "eMASS (Enterprise Mission Assurance Support Service)",
                "artifact_count": file_count,
                "artifacts": artifact_manifest,
            }
            zf.writestr("manifest.json", json.dumps(metadata, indent=2))

        _log_audit(conn, project_id, "export_artifacts_emass", {
            "artifact_count": file_count,
            "output_file": str(zip_path),
            "format": "zip",
        })

        print(f"[eMASS Export] Artifacts ZIP: {zip_path} ({file_count} artifacts)")
        return str(zip_path)

    finally:
        conn.close()


# ============================================================
# Test Results Export
# ============================================================

def export_test_results_emass(project_id, output_dir=None, db_path=None):
    """Export scan results (SAST, dependency, container) in eMASS format.

    Queries STIG findings and security scan results, then writes them
    as a CSV compatible with the eMASS test results import template.

    Columns:
        CCI, Test Date, Tested By, Description, Compliance Status,
        Scan Type, Severity, Finding ID

    Args:
        project_id: ICDEV project ID.
        output_dir: Output directory (optional).
        db_path: Database path (optional).

    Returns:
        Path to generated CSV file.
    """
    conn = _get_connection(db_path)
    try:
        _get_project(conn, project_id)

        # Gather STIG findings as test results
        stig_findings = conn.execute(
            "SELECT * FROM stig_findings WHERE project_id = ? ORDER BY severity, finding_id",
            (project_id,),
        ).fetchall()

        # Gather vulnerability management / scan results if available
        vuln_scans = []
        try:
            vuln_scans = conn.execute(
                "SELECT * FROM cssp_vuln_management WHERE project_id = ? ORDER BY scan_date DESC",
                (project_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            # Table may not exist in all environments
            pass

        out_dir = _ensure_output_dir(project_id, output_dir)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_file = out_dir / f"emass-test-results-{project_id}-{timestamp}.csv"

        result_count = 0

        with open(out_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            # eMASS test results import header row
            writer.writerow([
                "CCI",
                "Test Date",
                "Tested By",
                "Description",
                "Compliance Status",
                "Scan Type",
                "Severity",
                "Finding ID",
            ])

            # Write STIG findings as test results
            for row in stig_findings:
                r = dict(row)
                writer.writerow([
                    r.get("cci", r.get("control_id", r.get("rule_id", ""))),
                    r.get("assessed_at", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                    r.get("assessed_by", "ICDEV Compliance Engine"),
                    r.get("title", r.get("description", "")),
                    _map_compliance_status(r.get("status", "")),
                    "STIG",
                    _map_severity(r.get("severity", "")),
                    r.get("finding_id", ""),
                ])
                result_count += 1

            # Write vulnerability scan results
            for row in vuln_scans:
                r = dict(row)
                writer.writerow([
                    r.get("cci", r.get("control_id", "")),
                    r.get("scan_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                    r.get("scanner", r.get("scanned_by", "ICDEV Security Scanner")),
                    r.get("description", r.get("scan_type", "Vulnerability Scan")),
                    _map_compliance_status(r.get("status", r.get("result", ""))),
                    r.get("scan_type", "Vulnerability"),
                    _map_severity(r.get("severity", r.get("risk_level", ""))),
                    r.get("finding_id", r.get("scan_id", "")),
                ])
                result_count += 1

        _log_audit(conn, project_id, "export_test_results_emass", {
            "result_count": result_count,
            "stig_findings": len(stig_findings),
            "vuln_scans": len(vuln_scans),
            "output_file": str(out_file),
            "format": "csv",
        })

        print(f"[eMASS Export] Test Results CSV: {out_file} ({result_count} results)")
        return str(out_file)

    finally:
        conn.close()


# ============================================================
# Combined Export
# ============================================================

def export_all_emass(project_id, output_dir=None, db_path=None):
    """Run all eMASS exports for a project.

    Executes each export function (controls, POA&M, artifacts, test
    results) and collects results. Individual export failures are
    captured but do not prevent other exports from running.

    Args:
        project_id: ICDEV project ID.
        output_dir: Output directory (optional).
        db_path: Database path (optional).

    Returns:
        Dict with all exported file paths and metadata.
    """
    results = {
        "project_id": project_id,
        "export_date": datetime.now(timezone.utc).isoformat() + "Z",
        "classification": "CUI // SP-CTI",
        "target_system": "eMASS",
        "files": {},
        "errors": [],
    }

    # Controls CSV
    try:
        results["files"]["controls_csv"] = export_controls_emass(project_id, output_dir, db_path)
    except Exception as e:
        results["files"]["controls_csv"] = None
        results["errors"].append({"export": "controls", "error": str(e)})

    # POA&M CSV
    try:
        results["files"]["poam_csv"] = export_poam_emass(project_id, output_dir, db_path)
    except Exception as e:
        results["files"]["poam_csv"] = None
        results["errors"].append({"export": "poam", "error": str(e)})

    # Artifacts ZIP
    try:
        results["files"]["artifacts_zip"] = export_artifacts_emass(project_id, output_dir, db_path)
    except Exception as e:
        results["files"]["artifacts_zip"] = None
        results["errors"].append({"export": "artifacts", "error": str(e)})

    # Test Results CSV
    try:
        results["files"]["test_results_csv"] = export_test_results_emass(project_id, output_dir, db_path)
    except Exception as e:
        results["files"]["test_results_csv"] = None
        results["errors"].append({"export": "test_results", "error": str(e)})

    # Determine overall status
    successful = sum(1 for v in results["files"].values() if v is not None)
    total = len(results["files"])
    if successful == total:
        results["status"] = "success"
    elif successful > 0:
        results["status"] = "partial"
    else:
        results["status"] = "error"

    results["summary"] = f"{successful}/{total} exports completed successfully"

    # Log the combined export
    try:
        conn = _get_connection(db_path)
        try:
            _log_audit(conn, project_id, "export_all_emass", {
                "status": results["status"],
                "summary": results["summary"],
                "errors": results["errors"] if results["errors"] else None,
            })
        finally:
            conn.close()
    except Exception:
        pass

    return results


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    """CLI entry point for eMASS export tool."""
    parser = argparse.ArgumentParser(
        description="Export ICDEV compliance data in eMASS-compatible formats"
    )
    parser.add_argument(
        "--project-id", required=True,
        help="ICDEV project ID",
    )
    parser.add_argument(
        "--type", default="all",
        choices=["controls", "poam", "artifacts", "test-results", "all"],
        help="Export type (default: all)",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        help="Output directory (default: compliance/emass-exports/<project-id>)",
    )
    parser.add_argument(
        "--db-path", type=Path, default=DB_PATH,
        help="Database path (default: data/icdev.db)",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    export_type = args.type
    project_id = args.project_id
    output_dir = args.output_dir
    db_path = args.db_path

    if export_type == "controls":
        result = export_controls_emass(project_id, output_dir, db_path)
        print(json.dumps({"status": "success", "file": result}, indent=2))

    elif export_type == "poam":
        result = export_poam_emass(project_id, output_dir, db_path)
        print(json.dumps({"status": "success", "file": result}, indent=2))

    elif export_type == "artifacts":
        result = export_artifacts_emass(project_id, output_dir, db_path)
        print(json.dumps({"status": "success", "file": result}, indent=2))

    elif export_type == "test-results":
        result = export_test_results_emass(project_id, output_dir, db_path)
        print(json.dumps({"status": "success", "file": result}, indent=2))

    elif export_type == "all":
        result = export_all_emass(project_id, output_dir, db_path)
        print(json.dumps(result, indent=2, default=str))

    else:
        parser.error(f"Unknown export type: {export_type}")


if __name__ == "__main__":
    main()

# [TEMPLATE: CUI // SP-CTI]
