#!/usr/bin/env python3
# CUI // SP-CTI
"""Xacta 360 file-based export for batch import.

Generates OSCAL JSON and CSV exports compatible with Xacta 360's
import functionality. Used as fallback when API is unavailable or
for bulk data transfer.

Formats:
    - OSCAL JSON: NIST Open Security Controls Assessment Language
    - CSV: Tabular exports for findings, POA&M, controls
    - Evidence ZIP: Bundled evidence package with manifest

Usage:
    python tools/compliance/xacta/xacta_export.py --project-id proj-123 --format oscal
    python tools/compliance/xacta/xacta_export.py --project-id proj-123 --format csv
    python tools/compliance/xacta/xacta_export.py --project-id proj-123 --format all
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


def _get_connection(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_project(conn, project_id):
    """Load project data."""
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found.")
    return dict(row)


def _log_audit(conn, project_id, action, details=None):
    """Log export event to audit trail."""
    conn.execute(
        """INSERT INTO audit_trail
           (project_id, event_type, actor, action, details, classification, created_at)
           VALUES (?, 'xacta_export', 'icdev-xacta-export', ?, ?, 'CUI', datetime('now'))""",
        (project_id, action, json.dumps(details) if details else None),
    )
    conn.commit()


def _ensure_output_dir(project_id, output_dir=None):
    """Ensure output directory exists."""
    if output_dir:
        out = Path(output_dir)
    else:
        out = BASE_DIR / "compliance" / "xacta-exports" / project_id
    out.mkdir(parents=True, exist_ok=True)
    return out


# ============================================================
# OSCAL Exports
# ============================================================

def export_controls_oscal(project_id, output_dir=None, db_path=None):
    """Export control implementations in OSCAL System Security Plan format.

    Args:
        project_id: ICDEV project ID
        output_dir: Output directory (optional)
        db_path: Database path (optional)

    Returns:
        Path to generated OSCAL JSON file.
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)
        controls = conn.execute(
            "SELECT * FROM project_controls WHERE project_id = ? ORDER BY control_id",
            (project_id,),
        ).fetchall()

        oscal_doc = {
            "system-security-plan": {
                "uuid": f"icdev-ssp-{project_id}-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                "metadata": {
                    "title": f"System Security Plan — {project.get('name', project_id)}",
                    "last-modified": datetime.now(timezone.utc).isoformat() + "Z",
                    "version": "1.0",
                    "oscal-version": "1.1.2",
                    "props": [
                        {"name": "classification", "value": "CUI // SP-CTI"},
                        {"name": "source", "value": "ICDEV Compliance Engine"},
                    ],
                },
                "system-characteristics": {
                    "system-name": project.get("name", project_id),
                    "system-id": project_id,
                    "description": project.get("description", ""),
                    "security-sensitivity-level": "moderate",
                    "system-information": {
                        "information-types": [{
                            "title": "Controlled Technical Information",
                            "categorization": "CUI // SP-CTI",
                            "confidentiality-impact": {"base": "moderate"},
                            "integrity-impact": {"base": "moderate"},
                            "availability-impact": {"base": "low"},
                        }],
                    },
                    "status": {"state": project.get("status", "operational")},
                },
                "control-implementation": {
                    "description": "NIST SP 800-53 Rev 5 control implementations",
                    "implemented-requirements": [
                        {
                            "uuid": f"impl-{c['control_id']}-{project_id}",
                            "control-id": c["control_id"].lower().replace("-", "."),
                            "props": [
                                {"name": "implementation-status", "value": c["implementation_status"]},
                            ],
                            "statements": [{
                                "statement-id": f"{c['control_id'].lower()}_stmt",
                                "description": c.get("implementation_description") or "Planned",
                                "responsible-roles": [
                                    {"role-id": c.get("responsible_role") or "system-admin"}
                                ],
                            }],
                        }
                        for c in [dict(r) for r in controls]
                    ],
                },
            }
        }

        out_dir = _ensure_output_dir(project_id, output_dir)
        out_file = out_dir / f"oscal-ssp-{project_id}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(oscal_doc, f, indent=2)

        _log_audit(conn, project_id, "export_controls_oscal", {
            "control_count": len(controls),
            "output_file": str(out_file),
        })
        return str(out_file)
    finally:
        conn.close()


def export_assessment_oscal(project_id, output_dir=None, db_path=None):
    """Export CSSP assessment results in OSCAL Assessment Results format.

    Args:
        project_id: ICDEV project ID
        output_dir: Output directory (optional)
        db_path: Database path (optional)

    Returns:
        Path to generated OSCAL JSON file.
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)
        assessments = conn.execute(
            "SELECT * FROM cssp_assessments WHERE project_id = ? ORDER BY requirement_id",
            (project_id,),
        ).fetchall()

        oscal_doc = {
            "assessment-results": {
                "uuid": f"icdev-ar-{project_id}-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                "metadata": {
                    "title": f"CSSP Assessment Results — {project.get('name', project_id)}",
                    "last-modified": datetime.now(timezone.utc).isoformat() + "Z",
                    "version": "1.0",
                    "oscal-version": "1.1.2",
                    "props": [
                        {"name": "classification", "value": "CUI // SP-CTI"},
                        {"name": "assessment-type", "value": "CSSP"},
                        {"name": "framework", "value": "DoD Instruction 8530.01"},
                        {"name": "source", "value": "ICDEV Compliance Engine"},
                    ],
                },
                "results": [{
                    "uuid": f"result-{project_id}-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                    "title": "CSSP Assessment",
                    "description": "DoD Instruction 8530.01 CSSP functional area assessment",
                    "start": datetime.now(timezone.utc).isoformat() + "Z",
                    "findings": [
                        {
                            "uuid": f"finding-{a['requirement_id']}-{project_id}",
                            "title": a.get("requirement_id", ""),
                            "description": a.get("evidence_description") or "Assessment pending",
                            "props": [
                                {"name": "functional-area", "value": a.get("functional_area", "")},
                                {"name": "status", "value": a.get("status", "not_assessed")},
                            ],
                            "target": {
                                "type": "requirement",
                                "target-id": a.get("requirement_id", ""),
                                "status": {"state": _map_status_to_oscal(a.get("status", ""))},
                            },
                        }
                        for a in [dict(r) for r in assessments]
                    ],
                }],
            }
        }

        out_dir = _ensure_output_dir(project_id, output_dir)
        out_file = out_dir / f"oscal-assessment-{project_id}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(oscal_doc, f, indent=2)

        _log_audit(conn, project_id, "export_assessment_oscal", {
            "assessment_count": len(assessments),
            "output_file": str(out_file),
        })
        return str(out_file)
    finally:
        conn.close()


def _map_status_to_oscal(status):
    """Map ICDEV status to OSCAL finding status."""
    mapping = {
        "satisfied": "satisfied",
        "partially_satisfied": "partially-satisfied",
        "not_satisfied": "not-satisfied",
        "not_assessed": "not-assessed",
        "not_applicable": "not-applicable",
        "risk_accepted": "risk-accepted",
    }
    return mapping.get(status, "not-assessed")


# ============================================================
# CSV Exports
# ============================================================

def export_findings_csv(project_id, output_dir=None, db_path=None):
    """Export STIG + security findings as CSV for Xacta import.

    Args:
        project_id: ICDEV project ID
        output_dir: Output directory (optional)
        db_path: Database path (optional)

    Returns:
        Path to generated CSV file.
    """
    conn = _get_connection(db_path)
    try:
        findings = conn.execute(
            "SELECT * FROM stig_findings WHERE project_id = ? ORDER BY severity, finding_id",
            (project_id,),
        ).fetchall()

        out_dir = _ensure_output_dir(project_id, output_dir)
        out_file = out_dir / f"findings-{project_id}.csv"

        with open(out_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Finding ID", "STIG ID", "Rule ID", "Severity",
                "Title", "Status", "Target Type", "Comments",
                "Assessed By", "Assessed At", "Classification",
            ])
            for row in findings:
                r = dict(row)
                writer.writerow([
                    r.get("finding_id", ""),
                    r.get("stig_id", ""),
                    r.get("rule_id", ""),
                    r.get("severity", ""),
                    r.get("title", ""),
                    r.get("status", ""),
                    r.get("target_type", ""),
                    r.get("comments", ""),
                    r.get("assessed_by", ""),
                    r.get("assessed_at", ""),
                    "CUI // SP-CTI",
                ])

        _log_audit(conn, project_id, "export_findings_csv", {
            "finding_count": len(findings),
            "output_file": str(out_file),
        })
        return str(out_file)
    finally:
        conn.close()


def export_poam_csv(project_id, output_dir=None, db_path=None):
    """Export POA&M items as CSV for Xacta import.

    Args:
        project_id: ICDEV project ID
        output_dir: Output directory (optional)
        db_path: Database path (optional)

    Returns:
        Path to generated CSV file.
    """
    conn = _get_connection(db_path)
    try:
        items = conn.execute(
            "SELECT * FROM poam_items WHERE project_id = ? ORDER BY severity, weakness_id",
            (project_id,),
        ).fetchall()

        out_dir = _ensure_output_dir(project_id, output_dir)
        out_file = out_dir / f"poam-{project_id}.csv"

        with open(out_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Weakness ID", "Description", "Severity", "Source",
                "Control ID", "Status", "Corrective Action",
                "Milestone Date", "Completion Date",
                "Responsible Party", "Resources Required", "Classification",
            ])
            for row in items:
                r = dict(row)
                writer.writerow([
                    r.get("weakness_id", ""),
                    r.get("weakness_description", ""),
                    r.get("severity", ""),
                    r.get("source", ""),
                    r.get("control_id", ""),
                    r.get("status", ""),
                    r.get("corrective_action", ""),
                    r.get("milestone_date", ""),
                    r.get("completion_date", ""),
                    r.get("responsible_party", ""),
                    r.get("resources_required", ""),
                    "CUI // SP-CTI",
                ])

        _log_audit(conn, project_id, "export_poam_csv", {
            "poam_count": len(items),
            "output_file": str(out_file),
        })
        return str(out_file)
    finally:
        conn.close()


def export_cssp_assessment_csv(project_id, output_dir=None, db_path=None):
    """Export CSSP assessment results as CSV for Xacta import.

    Args:
        project_id: ICDEV project ID
        output_dir: Output directory (optional)
        db_path: Database path (optional)

    Returns:
        Path to generated CSV file.
    """
    conn = _get_connection(db_path)
    try:
        assessments = conn.execute(
            "SELECT * FROM cssp_assessments WHERE project_id = ? ORDER BY functional_area, requirement_id",
            (project_id,),
        ).fetchall()

        out_dir = _ensure_output_dir(project_id, output_dir)
        out_file = out_dir / f"cssp-assessment-{project_id}.csv"

        with open(out_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Requirement ID", "Functional Area", "Status",
                "Assessment Date", "Assessor",
                "Evidence Description", "Evidence Path",
                "Notes", "Classification",
            ])
            for row in assessments:
                r = dict(row)
                writer.writerow([
                    r.get("requirement_id", ""),
                    r.get("functional_area", ""),
                    r.get("status", ""),
                    r.get("assessment_date", ""),
                    r.get("assessor", ""),
                    r.get("evidence_description", ""),
                    r.get("evidence_path", ""),
                    r.get("notes", ""),
                    "CUI // SP-CTI",
                ])

        _log_audit(conn, project_id, "export_cssp_csv", {
            "assessment_count": len(assessments),
            "output_file": str(out_file),
        })
        return str(out_file)
    finally:
        conn.close()


# ============================================================
# Evidence Package
# ============================================================

def export_evidence_package(project_id, evidence_manifest_path=None, output_dir=None, db_path=None):
    """Create ZIP evidence package for Xacta import.

    Args:
        project_id: ICDEV project ID
        evidence_manifest_path: Path to evidence manifest JSON (optional)
        output_dir: Output directory (optional)
        db_path: Database path (optional)

    Returns:
        Path to generated ZIP file.
    """
    conn = _get_connection(db_path)
    try:
        out_dir = _ensure_output_dir(project_id, output_dir)
        zip_path = out_dir / f"evidence-package-{project_id}.zip"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add manifest if provided
            if evidence_manifest_path:
                manifest_path = Path(evidence_manifest_path)
                if manifest_path.exists():
                    zf.write(manifest_path, "evidence-manifest.json")

            # Add compliance artifacts from DB
            ssp_rows = conn.execute(
                "SELECT file_path FROM ssp_documents WHERE project_id = ? AND file_path IS NOT NULL",
                (project_id,),
            ).fetchall()
            for row in ssp_rows:
                fp = Path(row["file_path"])
                if fp.exists():
                    zf.write(fp, f"ssp/{fp.name}")

            # Add SBOM records
            sbom_rows = conn.execute(
                "SELECT file_path FROM sbom_records WHERE project_id = ? AND file_path IS NOT NULL",
                (project_id,),
            ).fetchall()
            for row in sbom_rows:
                fp = Path(row["file_path"])
                if fp.exists():
                    zf.write(fp, f"sbom/{fp.name}")

            # Add classification marker
            zf.writestr(
                "CLASSIFICATION.txt",
                "CUI // SP-CTI\n"
                "This evidence package contains Controlled Unclassified Information.\n"
                "Handle in accordance with DoD CUI policy.\n"
            )

            # Add package metadata
            metadata = {
                "project_id": project_id,
                "export_date": datetime.now(timezone.utc).isoformat(),
                "classification": "CUI // SP-CTI",
                "source": "ICDEV Compliance Engine",
                "format": "Xacta 360 Evidence Package",
            }
            zf.writestr("metadata.json", json.dumps(metadata, indent=2))

        _log_audit(conn, project_id, "export_evidence_package", {
            "output_file": str(zip_path),
        })
        return str(zip_path)
    finally:
        conn.close()


# ============================================================
# Combined Export
# ============================================================

def export_all(project_id, export_format="all", output_dir=None, db_path=None):
    """Export all compliance data for Xacta import.

    Args:
        project_id: ICDEV project ID
        export_format: "oscal", "csv", or "all"
        output_dir: Output directory (optional)
        db_path: Database path (optional)

    Returns:
        Dict with all exported file paths.
    """
    results = {"project_id": project_id, "format": export_format, "files": {}}

    if export_format in ("oscal", "all"):
        try:
            results["files"]["oscal_ssp"] = export_controls_oscal(project_id, output_dir, db_path)
        except Exception as e:
            results["files"]["oscal_ssp"] = f"Error: {e}"

        try:
            results["files"]["oscal_assessment"] = export_assessment_oscal(project_id, output_dir, db_path)
        except Exception as e:
            results["files"]["oscal_assessment"] = f"Error: {e}"

    if export_format in ("csv", "all"):
        try:
            results["files"]["findings_csv"] = export_findings_csv(project_id, output_dir, db_path)
        except Exception as e:
            results["files"]["findings_csv"] = f"Error: {e}"

        try:
            results["files"]["poam_csv"] = export_poam_csv(project_id, output_dir, db_path)
        except Exception as e:
            results["files"]["poam_csv"] = f"Error: {e}"

        try:
            results["files"]["cssp_csv"] = export_cssp_assessment_csv(project_id, output_dir, db_path)
        except Exception as e:
            results["files"]["cssp_csv"] = f"Error: {e}"

    if export_format == "all":
        try:
            results["files"]["evidence_zip"] = export_evidence_package(project_id, None, output_dir, db_path)
        except Exception as e:
            results["files"]["evidence_zip"] = f"Error: {e}"

    return results


def main():
    parser = argparse.ArgumentParser(description="Export compliance data for Xacta 360 import")
    parser.add_argument("--project-id", required=True, help="ICDEV project ID")
    parser.add_argument("--format", default="all", choices=["oscal", "csv", "all"],
                        help="Export format (default: all)")
    parser.add_argument("--output-dir", type=Path, help="Output directory")
    parser.add_argument("--db-path", type=Path, default=DB_PATH, help="Database path")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    result = export_all(args.project_id, args.format, args.output_dir, args.db_path)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

# [TEMPLATE: CUI // SP-CTI]
