#!/usr/bin/env python3
"""Collect and organize evidence artifacts for CSSP assessment.
Scans project directories and database records to build a comprehensive
evidence manifest mapping artifacts to DoD CSSP 8530.01 requirements.
Generates JSON manifest and markdown report with CUI markings."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CSSP_REQUIREMENTS_PATH = BASE_DIR / "context" / "compliance" / "dod_cssp_8530.json"

# Directories to skip during recursive scan
SKIP_DIRS = {".git", "__pycache__", "node_modules", "venv", "env", ".tox", ".tmp",
             ".mypy_cache", ".pytest_cache", ".eggs", "dist", "build", ".venv"}

# Evidence categories aligned with CSSP functional areas
EVIDENCE_CATEGORIES = {
    "access_control": {
        "description": "Access control and authentication evidence",
        "patterns": ["**/rbac*", "**/auth*", "**/permission*", "**/role*", "**/*cac*", "**/*pki*"],
        "file_patterns": [r"rbac", r"auth", r"permission", r"role", r"cac", r"pki", r"login"],
        "cssp_requirements": ["PR-1", "PR-8"],
    },
    "audit_monitoring": {
        "description": "Audit logging and monitoring evidence",
        "patterns": ["**/audit*", "**/monitor*", "**/log*config*", "**/siem*", "**/splunk*", "**/filebeat*"],
        "file_patterns": [r"audit", r"monitor", r"log.*config", r"siem", r"splunk", r"filebeat"],
        "cssp_requirements": ["DE-2", "DE-3", "DE-6"],
    },
    "configuration_mgmt": {
        "description": "Configuration management and IaC evidence",
        "patterns": ["**/*.tf", "**/ansible*", "**/k8s*", "**/Dockerfile*", "**/*stig*"],
        "file_patterns": [r"\.tf$", r"ansible", r"k8s", r"Dockerfile", r"stig"],
        "cssp_requirements": ["PR-6", "SU-3"],
    },
    "incident_response": {
        "description": "Incident response documentation",
        "patterns": ["**/*incident*", "**/*ir-plan*", "**/*response*plan*"],
        "file_patterns": [r"incident", r"ir.plan", r"response.*plan"],
        "cssp_requirements": ["RS-1", "RS-2", "RS-4"],
    },
    "security_assessment": {
        "description": "Security scan results and assessments",
        "patterns": ["**/*sast*", "**/*scan*", "**/*vuln*", "**/*sbom*", "**/*bandit*", "**/*trivy*"],
        "file_patterns": [r"sast", r"scan", r"vuln", r"sbom", r"bandit", r"trivy", r"bom"],
        "cssp_requirements": ["DE-7", "ID-2", "ID-5", "SU-1"],
    },
    "encryption": {
        "description": "Encryption and key management evidence",
        "patterns": ["**/*tls*", "**/*ssl*", "**/*cert*", "**/*encrypt*", "**/*fips*"],
        "file_patterns": [r"tls", r"ssl", r"cert", r"encrypt", r"fips", r"key.*mgmt"],
        "cssp_requirements": ["PR-2", "PR-3", "SU-7"],
    },
    "network_security": {
        "description": "Network security and segmentation evidence",
        "patterns": ["**/*network*policy*", "**/*firewall*", "**/*ingress*", "**/*egress*"],
        "file_patterns": [r"network.*policy", r"firewall", r"ingress", r"egress", r"security.*group"],
        "cssp_requirements": ["PR-5", "DE-5"],
    },
    "compliance_docs": {
        "description": "Compliance documentation (SSP, POAM, STIG results)",
        "patterns": ["**/ssp*", "**/poam*", "**/stig*", "**/compliance*"],
        "file_patterns": [r"ssp", r"poam", r"stig", r"compliance"],
        "cssp_requirements": ["SU-5", "SU-6"],
    },
}


def _get_connection(db_path=None):
    """Get a database connection with row factory."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_cui_config():
    """Load CUI marking configuration from cui_marker module."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from cui_marker import load_cui_config
        return load_cui_config()
    except ImportError:
        return {
            "document_header": (
                "////////////////////////////////////////////////////////////////////\n"
                "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
                "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
                "////////////////////////////////////////////////////////////////////"
            ),
            "document_footer": (
                "////////////////////////////////////////////////////////////////////\n"
                "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
                "////////////////////////////////////////////////////////////////////"
            ),
        }


def _hash_file(file_path):
    """Compute SHA-256 hash of a file, reading in 8KB chunks.

    Returns:
        Hex digest string, or None if the file cannot be read.
    """
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                sha256.update(chunk)
        return sha256.hexdigest()
    except (OSError, PermissionError):
        return None


def _scan_directory(project_dir, category_name, file_patterns):
    """Walk a project directory and find files matching evidence patterns.

    Args:
        project_dir: Root directory to scan.
        category_name: Evidence category name (for labeling).
        file_patterns: List of regex patterns to match against file paths.

    Returns:
        List of dicts with file metadata for each matched artifact.
    """
    artifacts = []
    project_dir = Path(project_dir)
    compiled = [re.compile(p, re.IGNORECASE) for p in file_patterns]

    for root, dirs, files in os.walk(project_dir):
        # Skip hidden and non-project directories
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]

        for fname in files:
            fpath = Path(root) / fname
            rel_path = str(fpath.relative_to(project_dir))

            # Check if filename or relative path matches any pattern
            matched = False
            for pattern in compiled:
                if pattern.search(fname) or pattern.search(rel_path):
                    matched = True
                    break

            if not matched:
                continue

            try:
                stat = fpath.stat()
                artifact = {
                    "name": fname,
                    "path": str(fpath),
                    "relative_path": rel_path,
                    "type": "file",
                    "sha256": _hash_file(fpath),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "category": category_name,
                }
                artifacts.append(artifact)
            except (OSError, PermissionError):
                continue

    return artifacts


def _query_db_evidence(conn, project_id):
    """Query database tables for compliance evidence records.

    Returns:
        Dict with counts and lists of database evidence by table.
    """
    evidence = {
        "stig_assessments": {"count": 0, "records": []},
        "ssp_documents": {"count": 0, "records": []},
        "poam_items": {"count": 0, "records": []},
        "sbom_records": {"count": 0, "records": []},
        "cssp_assessments": {"count": 0, "records": []},
        "vuln_scans": {"count": 0, "records": []},
    }

    # STIG findings
    try:
        rows = conn.execute(
            """SELECT finding_id, stig_id, severity, status, assessed_at
               FROM stig_findings WHERE project_id = ?
               ORDER BY assessed_at DESC""",
            (project_id,),
        ).fetchall()
        evidence["stig_assessments"]["count"] = len(rows)
        evidence["stig_assessments"]["records"] = [
            {"finding_id": r["finding_id"], "stig_id": r["stig_id"],
             "severity": r["severity"], "status": r["status"],
             "assessed_at": r["assessed_at"]}
            for r in rows
        ]
    except sqlite3.OperationalError:
        pass

    # SSP documents
    try:
        rows = conn.execute(
            """SELECT version, system_name, status, file_path, created_at
               FROM ssp_documents WHERE project_id = ?
               ORDER BY created_at DESC""",
            (project_id,),
        ).fetchall()
        evidence["ssp_documents"]["count"] = len(rows)
        evidence["ssp_documents"]["records"] = [
            {"version": r["version"], "system_name": r["system_name"],
             "status": r["status"], "file_path": r["file_path"],
             "created_at": r["created_at"]}
            for r in rows
        ]
    except sqlite3.OperationalError:
        pass

    # POA&M items
    try:
        rows = conn.execute(
            """SELECT weakness_id, weakness_description, severity, status, created_at
               FROM poam_items WHERE project_id = ?
               ORDER BY created_at DESC""",
            (project_id,),
        ).fetchall()
        evidence["poam_items"]["count"] = len(rows)
        evidence["poam_items"]["records"] = [
            {"weakness_id": r["weakness_id"], "severity": r["severity"],
             "status": r["status"], "created_at": r["created_at"]}
            for r in rows
        ]
    except sqlite3.OperationalError:
        pass

    # SBOM records
    try:
        rows = conn.execute(
            """SELECT version, format, file_path, component_count,
                      vulnerability_count, generated_at
               FROM sbom_records WHERE project_id = ?
               ORDER BY generated_at DESC""",
            (project_id,),
        ).fetchall()
        evidence["sbom_records"]["count"] = len(rows)
        evidence["sbom_records"]["records"] = [
            {"version": r["version"], "format": r["format"],
             "file_path": r["file_path"], "component_count": r["component_count"],
             "vulnerability_count": r["vulnerability_count"],
             "generated_at": r["generated_at"]}
            for r in rows
        ]
    except sqlite3.OperationalError:
        pass

    # CSSP assessments
    try:
        rows = conn.execute(
            """SELECT requirement_id, functional_area, status, evidence_description,
                      assessment_date
               FROM cssp_assessments WHERE project_id = ?
               ORDER BY assessment_date DESC""",
            (project_id,),
        ).fetchall()
        evidence["cssp_assessments"]["count"] = len(rows)
        evidence["cssp_assessments"]["records"] = [
            {"requirement_id": r["requirement_id"],
             "functional_area": r["functional_area"],
             "status": r["status"],
             "evidence_description": r["evidence_description"],
             "assessment_date": r["assessment_date"]}
            for r in rows
        ]
    except sqlite3.OperationalError:
        pass

    # Vulnerability scans
    try:
        rows = conn.execute(
            """SELECT scan_type, scanner, scan_date, total_findings,
                      critical_count, high_count, medium_count, low_count,
                      report_path
               FROM cssp_vuln_management WHERE project_id = ?
               ORDER BY scan_date DESC""",
            (project_id,),
        ).fetchall()
        evidence["vuln_scans"]["count"] = len(rows)
        evidence["vuln_scans"]["records"] = [
            {"scan_type": r["scan_type"], "scanner": r["scanner"],
             "scan_date": r["scan_date"], "total_findings": r["total_findings"],
             "critical_count": r["critical_count"], "high_count": r["high_count"],
             "report_path": r["report_path"]}
            for r in rows
        ]
    except sqlite3.OperationalError:
        pass

    return evidence


def _compute_coverage(categories_result):
    """Compute CSSP requirement coverage from collected evidence.

    Returns:
        Dict with requirements_with_evidence, requirements_without_evidence,
        coverage_pct, and detail lists.
    """
    all_requirements = set()
    covered_requirements = set()

    for cat_name, cat_data in categories_result.items():
        reqs = cat_data.get("cssp_requirements", [])
        all_requirements.update(reqs)
        if cat_data.get("status") in ("evidence_found", "partial"):
            covered_requirements.update(reqs)

    total = len(all_requirements)
    covered = len(covered_requirements)
    missing = all_requirements - covered_requirements

    return {
        "requirements_with_evidence": covered,
        "requirements_without_evidence": total - covered,
        "coverage_pct": round((covered / total * 100), 1) if total > 0 else 0.0,
        "covered": sorted(covered_requirements),
        "missing": sorted(missing),
    }


def _determine_category_status(artifacts, db_evidence_count):
    """Determine evidence status for a category.

    Returns:
        'evidence_found' if file artifacts exist, 'partial' if only DB records,
        'no_evidence' if nothing found.
    """
    if artifacts:
        return "evidence_found"
    if db_evidence_count > 0:
        return "partial"
    return "no_evidence"


def _generate_report(manifest, cui_config):
    """Generate a markdown evidence index report with CUI markings.

    Args:
        manifest: The complete evidence manifest dict.
        cui_config: CUI configuration for header/footer banners.

    Returns:
        String containing the full markdown report.
    """
    doc_header = cui_config.get("document_header", "CUI // SP-CTI").strip()
    doc_footer = cui_config.get("document_footer", "CUI // SP-CTI").strip()
    now = manifest["metadata"]["collection_date"]

    lines = [
        doc_header,
        "",
        "# CSSP Evidence Collection Report",
        "",
        f"**Project:** {manifest['metadata']['project_id']}",
        f"**Collection Date:** {now}",
        f"**Classification:** {manifest['metadata']['classification']}",
        f"**Total Artifacts:** {manifest['metadata']['total_artifacts']}",
        f"**Collector:** {manifest['metadata']['collector']}",
        "",
        "---",
        "",
        "## Evidence Summary by Category",
        "",
        "| Category | Description | Artifacts | Status | CSSP Requirements |",
        "|----------|-------------|-----------|--------|-------------------|",
    ]

    for cat_name, cat_data in manifest["categories"].items():
        artifact_count = len(cat_data.get("artifacts", []))
        status = cat_data.get("status", "no_evidence")
        reqs = ", ".join(cat_data.get("cssp_requirements", []))
        desc = cat_data.get("description", "")
        lines.append(f"| {cat_name} | {desc} | {artifact_count} | {status} | {reqs} |")

    lines.extend(["", "---", ""])

    # Database evidence summary
    db_ev = manifest.get("database_evidence", {})
    lines.extend([
        "## Database Evidence Summary",
        "",
        "| Evidence Source | Record Count |",
        "|---------------|-------------|",
        f"| STIG Assessments | {db_ev.get('stig_assessments', 0)} |",
        f"| SSP Documents | {db_ev.get('ssp_documents', 0)} |",
        f"| POA&M Items | {db_ev.get('poam_items', 0)} |",
        f"| SBOM Records | {db_ev.get('sbom_records', 0)} |",
        f"| CSSP Assessments | {db_ev.get('cssp_assessments', 0)} |",
        f"| Vulnerability Scans | {db_ev.get('vuln_scans', 0)} |",
        "",
        "---",
        "",
    ])

    # Detailed listing per category
    lines.append("## Detailed Evidence by Category")
    lines.append("")

    for cat_name, cat_data in manifest["categories"].items():
        lines.append(f"### {cat_name.replace('_', ' ').title()}")
        lines.append("")
        lines.append(f"*{cat_data.get('description', '')}*")
        lines.append("")

        artifacts = cat_data.get("artifacts", [])
        if artifacts:
            lines.append("| File | Size | Modified | SHA-256 (first 16) |")
            lines.append("|------|------|----------|-------------------|")
            for a in artifacts:
                size_kb = round(a.get("size", 0) / 1024, 1)
                sha_short = (a.get("sha256") or "N/A")[:16]
                modified = a.get("modified", "N/A")
                rel = a.get("relative_path", a.get("name", "unknown"))
                lines.append(f"| `{rel}` | {size_kb} KB | {modified} | `{sha_short}` |")
        else:
            lines.append("*No file artifacts found for this category.*")

        lines.extend(["", ""])

    # Coverage analysis
    coverage = manifest.get("coverage", {})
    lines.extend([
        "---",
        "",
        "## CSSP Requirement Coverage Analysis",
        "",
        f"**Requirements with evidence:** {coverage.get('requirements_with_evidence', 0)}",
        f"**Requirements without evidence:** {coverage.get('requirements_without_evidence', 0)}",
        f"**Coverage:** {coverage.get('coverage_pct', 0)}%",
        "",
    ])

    covered = coverage.get("covered", [])
    if covered:
        lines.append("### Requirements with Supporting Evidence")
        lines.append("")
        for req in covered:
            lines.append(f"- {req}")
        lines.append("")

    missing = coverage.get("missing", [])
    if missing:
        lines.append("### Requirements Missing Evidence")
        lines.append("")
        for req in missing:
            lines.append(f"- **{req}** -- evidence needed")
        lines.append("")

    lines.extend([
        "---",
        "",
        doc_footer,
        "",
    ])

    return "\n".join(lines)


def _log_audit_event(conn, project_id, action, details, affected_files=None):
    """Log an audit trail event for CSSP evidence collection."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "cssp_evidence_collected",
                "icdev-compliance-engine",
                action,
                json.dumps(details, default=str),
                json.dumps(affected_files) if affected_files else None,
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


def collect_evidence(project_id, project_dir=None, output_dir=None, db_path=None):
    """Collect and organize CSSP evidence artifacts for a project.

    Scans the project directory for files matching evidence category patterns,
    queries database tables for compliance records, builds a JSON manifest and
    a CUI-marked markdown report.

    Args:
        project_id: The project identifier in the ICDEV database.
        project_dir: Override project directory (defaults to DB project record).
        output_dir: Output directory for manifest and report files.
        db_path: Override database path.

    Returns:
        Dict with manifest_path, report_path, and summary information.
    """
    conn = _get_connection(db_path)
    try:
        # Load project data
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Project '{project_id}' not found in database.")
        project = dict(row)

        # Determine project directory
        if project_dir:
            scan_dir = Path(project_dir)
        else:
            dir_path = project.get("directory_path", "")
            if dir_path:
                scan_dir = Path(dir_path)
            else:
                scan_dir = None

        can_scan = scan_dir is not None and scan_dir.is_dir()
        now = datetime.utcnow()

        # Scan project directory for evidence artifacts per category
        categories_result = {}
        total_artifacts = 0

        for cat_name, cat_def in EVIDENCE_CATEGORIES.items():
            artifacts = []
            if can_scan:
                artifacts = _scan_directory(scan_dir, cat_name, cat_def["file_patterns"])
            total_artifacts += len(artifacts)

            categories_result[cat_name] = {
                "description": cat_def["description"],
                "cssp_requirements": cat_def["cssp_requirements"],
                "artifacts": artifacts,
                "status": "no_evidence",  # updated below after DB scan
            }

        # Query database for evidence records
        db_evidence = _query_db_evidence(conn, project_id)

        # Determine category status using both file and DB evidence
        # Map DB evidence types to relevant categories for status enrichment
        db_category_map = {
            "access_control": [],
            "audit_monitoring": [],
            "configuration_mgmt": ["stig_assessments"],
            "incident_response": [],
            "security_assessment": ["stig_assessments", "vuln_scans", "sbom_records"],
            "encryption": [],
            "network_security": [],
            "compliance_docs": ["ssp_documents", "poam_items", "cssp_assessments"],
        }

        for cat_name, cat_data in categories_result.items():
            db_count = 0
            for db_key in db_category_map.get(cat_name, []):
                db_count += db_evidence.get(db_key, {}).get("count", 0)
            cat_data["status"] = _determine_category_status(
                cat_data["artifacts"], db_count
            )

        # Compute coverage
        coverage = _compute_coverage(categories_result)

        # Build the evidence manifest
        manifest = {
            "metadata": {
                "project_id": project_id,
                "project_name": project.get("name", project_id),
                "collection_date": now.strftime("%Y-%m-%d %H:%M UTC"),
                "classification": "CUI // SP-CTI",
                "total_artifacts": total_artifacts,
                "collector": "icdev-compliance-engine",
                "scanned_directory": str(scan_dir) if can_scan else None,
            },
            "categories": {},
            "database_evidence": {
                "stig_assessments": db_evidence["stig_assessments"]["count"],
                "ssp_documents": db_evidence["ssp_documents"]["count"],
                "poam_items": db_evidence["poam_items"]["count"],
                "sbom_records": db_evidence["sbom_records"]["count"],
                "cssp_assessments": db_evidence["cssp_assessments"]["count"],
                "vuln_scans": db_evidence["vuln_scans"]["count"],
            },
            "coverage": {
                "requirements_with_evidence": coverage["requirements_with_evidence"],
                "requirements_without_evidence": coverage["requirements_without_evidence"],
                "coverage_pct": coverage["coverage_pct"],
                "covered": coverage["covered"],
                "missing": coverage["missing"],
            },
        }

        # Populate categories in manifest (serialize artifacts for JSON)
        for cat_name, cat_data in categories_result.items():
            manifest["categories"][cat_name] = {
                "description": cat_data["description"],
                "cssp_requirements": cat_data["cssp_requirements"],
                "artifacts": [
                    {
                        "name": a["name"],
                        "path": a["path"],
                        "type": a["type"],
                        "sha256": a["sha256"],
                        "size": a["size"],
                        "modified": a["modified"],
                    }
                    for a in cat_data["artifacts"]
                ],
                "status": cat_data["status"],
            }

        # Determine output directory
        if output_dir:
            out_dir = Path(output_dir)
        else:
            dir_path = project.get("directory_path", "")
            if dir_path:
                out_dir = Path(dir_path) / "compliance" / "evidence"
            else:
                out_dir = BASE_DIR / ".tmp" / "compliance" / project_id / "evidence"
        out_dir.mkdir(parents=True, exist_ok=True)

        timestamp = now.strftime("%Y%m%d_%H%M%S")

        # Save manifest JSON
        manifest_path = out_dir / f"cssp_evidence_manifest_{project_id}_{timestamp}.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, default=str)

        # Generate and save markdown report
        cui_config = _load_cui_config()
        report_content = _generate_report(manifest, cui_config)
        report_path = out_dir / f"cssp_evidence_report_{project_id}_{timestamp}.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_content)

        # Log audit event
        _log_audit_event(conn, project_id, "CSSP evidence collected", {
            "total_artifacts": total_artifacts,
            "categories_with_evidence": sum(
                1 for c in categories_result.values() if c["status"] != "no_evidence"
            ),
            "coverage_pct": coverage["coverage_pct"],
            "manifest_path": str(manifest_path),
            "report_path": str(report_path),
        }, [str(manifest_path), str(report_path)])

        # Print summary
        print(f"CSSP evidence collection completed:")
        print(f"  Project: {project.get('name', project_id)} ({project_id})")
        print(f"  Scanned: {scan_dir if can_scan else 'N/A (no project directory)'}")
        print(f"  Total file artifacts: {total_artifacts}")
        print(f"  DB records: STIG={db_evidence['stig_assessments']['count']}"
              f" SSP={db_evidence['ssp_documents']['count']}"
              f" POAM={db_evidence['poam_items']['count']}"
              f" SBOM={db_evidence['sbom_records']['count']}"
              f" CSSP={db_evidence['cssp_assessments']['count']}"
              f" Vulns={db_evidence['vuln_scans']['count']}")
        print(f"  Coverage: {coverage['coverage_pct']}%"
              f" ({coverage['requirements_with_evidence']}/{coverage['requirements_with_evidence'] + coverage['requirements_without_evidence']} requirements)")
        print(f"  Manifest: {manifest_path}")
        print(f"  Report: {report_path}")

        return {
            "manifest_path": str(manifest_path),
            "report_path": str(report_path),
            "summary": {
                "total_artifacts": total_artifacts,
                "database_evidence": manifest["database_evidence"],
                "coverage_pct": coverage["coverage_pct"],
                "requirements_with_evidence": coverage["requirements_with_evidence"],
                "requirements_without_evidence": coverage["requirements_without_evidence"],
                "covered_requirements": coverage["covered"],
                "missing_requirements": coverage["missing"],
            },
        }

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect CSSP evidence artifacts")
    parser.add_argument("--project-id", required=True, help="Project ID in ICDEV database")
    parser.add_argument("--project-dir", type=Path, help="Project directory to scan (overrides DB)")
    parser.add_argument("--output-dir", type=Path, help="Output directory for evidence manifest and report")
    parser.add_argument("--db-path", type=Path, default=DB_PATH, help="Database path")
    args = parser.parse_args()

    try:
        result = collect_evidence(
            project_id=args.project_id,
            project_dir=args.project_dir,
            output_dir=args.output_dir,
            db_path=args.db_path,
        )
        print(json.dumps(result, indent=2, default=str))
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
