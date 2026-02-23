# [TEMPLATE: CUI // SP-CTI]
"""
Flask Blueprint for project API endpoints.
Queries icdev.db for project data, compliance status, and audit trail entries.
"""

import sqlite3
from flask import Blueprint, jsonify

from tools.dashboard.config import DB_PATH

projects_api = Blueprint("projects_api", __name__, url_prefix="/api/projects")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@projects_api.route("", methods=["GET"])
def list_projects():
    """Return all projects."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, description, type, classification, status, "
            "tech_stack_backend, tech_stack_frontend, tech_stack_database, "
            "directory_path, created_by, created_at, updated_at "
            "FROM projects ORDER BY created_at DESC"
        ).fetchall()
        projects = [dict(r) for r in rows]
        return jsonify({"projects": projects, "total": len(projects)})
    finally:
        conn.close()


@projects_api.route("/<project_id>/status", methods=["GET"])
def project_status(project_id):
    """Detailed project status including counts for related entities."""
    conn = _get_db()
    try:
        # Project basics
        project = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not project:
            return jsonify({"error": "Project not found"}), 404

        data = dict(project)

        # Deployment count
        dep = conn.execute(
            "SELECT COUNT(*) as cnt FROM deployments WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        data["deployment_count"] = dep["cnt"] if dep else 0

        # Open POAM items
        poam = conn.execute(
            "SELECT COUNT(*) as cnt FROM poam_items WHERE project_id = ? AND status = 'open'",
            (project_id,),
        ).fetchone()
        data["open_poam_count"] = poam["cnt"] if poam else 0

        # Open STIG findings
        stig = conn.execute(
            "SELECT COUNT(*) as cnt FROM stig_findings WHERE project_id = ? AND status = 'Open'",
            (project_id,),
        ).fetchone()
        data["open_stig_count"] = stig["cnt"] if stig else 0

        # Open alerts
        alert = conn.execute(
            "SELECT COUNT(*) as cnt FROM alerts WHERE project_id = ? AND status = 'firing'",
            (project_id,),
        ).fetchone()
        data["open_alert_count"] = alert["cnt"] if alert else 0

        # Audit entry count
        audit = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_trail WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        data["audit_entry_count"] = audit["cnt"] if audit else 0

        # Latest deployment
        latest_dep = conn.execute(
            "SELECT * FROM deployments WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        data["latest_deployment"] = dict(latest_dep) if latest_dep else None

        return jsonify(data)
    finally:
        conn.close()


@projects_api.route("/<project_id>/compliance", methods=["GET"])
def project_compliance(project_id):
    """Compliance summary for a project."""
    conn = _get_db()
    try:
        # SSP documents
        ssps = conn.execute(
            "SELECT id, version, system_name, status, approved_by, approved_at, created_at "
            "FROM ssp_documents WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()

        # POAM items
        poams = conn.execute(
            "SELECT * FROM poam_items WHERE project_id = ? ORDER BY severity, created_at DESC",
            (project_id,),
        ).fetchall()

        # STIG findings
        stigs = conn.execute(
            "SELECT * FROM stig_findings WHERE project_id = ? ORDER BY severity, created_at DESC",
            (project_id,),
        ).fetchall()

        # SBOM records
        sboms = conn.execute(
            "SELECT * FROM sbom_records WHERE project_id = ? ORDER BY generated_at DESC",
            (project_id,),
        ).fetchall()

        # Control implementation status
        controls = conn.execute(
            "SELECT pc.*, cc.family, cc.title as control_title "
            "FROM project_controls pc "
            "LEFT JOIN compliance_controls cc ON pc.control_id = cc.id "
            "WHERE pc.project_id = ? ORDER BY pc.control_id",
            (project_id,),
        ).fetchall()

        # Summaries
        poam_summary = {"total": len(poams), "open": 0, "closed": 0, "by_severity": {}}
        for p in poams:
            pd = dict(p)
            if pd["status"] == "open":
                poam_summary["open"] += 1
            else:
                poam_summary["closed"] += 1
            sev = pd.get("severity", "unknown")
            poam_summary["by_severity"][sev] = poam_summary["by_severity"].get(sev, 0) + 1

        stig_summary = {"total": len(stigs), "open": 0, "closed": 0, "by_severity": {}}
        for s in stigs:
            sd = dict(s)
            if sd["status"] == "Open":
                stig_summary["open"] += 1
            else:
                stig_summary["closed"] += 1
            sev = sd.get("severity", "unknown")
            stig_summary["by_severity"][sev] = stig_summary["by_severity"].get(sev, 0) + 1

        control_summary = {"total": len(controls), "by_status": {}}
        for c in controls:
            cd = dict(c)
            st = cd.get("implementation_status", "planned")
            control_summary["by_status"][st] = control_summary["by_status"].get(st, 0) + 1

        return jsonify({
            "project_id": project_id,
            "ssp_documents": [dict(r) for r in ssps],
            "poam_summary": poam_summary,
            "poam_items": [dict(r) for r in poams],
            "stig_summary": stig_summary,
            "stig_findings": [dict(r) for r in stigs],
            "sbom_records": [dict(r) for r in sboms],
            "control_summary": control_summary,
            "controls": [dict(r) for r in controls],
        })
    finally:
        conn.close()


@projects_api.route("/<project_id>/audit-trail", methods=["GET"])
def project_audit_trail(project_id):
    """Audit trail entries for a project."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_trail WHERE project_id = ? ORDER BY created_at DESC LIMIT 100",
            (project_id,),
        ).fetchall()
        return jsonify({"project_id": project_id, "entries": [dict(r) for r in rows]})
    finally:
        conn.close()
