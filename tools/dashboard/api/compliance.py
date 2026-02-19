# CUI // SP-CTI
"""
Flask Blueprint for compliance API.
Queries ssp_documents, poam_items, stig_findings, sbom_records.
"""

import sqlite3
from flask import Blueprint, jsonify, request

from tools.dashboard.config import DB_PATH

compliance_api = Blueprint("compliance_api", __name__, url_prefix="/api/compliance")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@compliance_api.route("/ssp", methods=["GET"])
def list_ssp():
    """List all SSP documents, optionally filtered by project_id."""
    conn = _get_db()
    try:
        project_id = request.args.get("project_id")
        if project_id:
            rows = conn.execute(
                "SELECT id, project_id, version, system_name, status, "
                "approved_by, approved_at, classification, created_at "
                "FROM ssp_documents WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, project_id, version, system_name, status, "
                "approved_by, approved_at, classification, created_at "
                "FROM ssp_documents ORDER BY created_at DESC"
            ).fetchall()
        return jsonify({"ssp_documents": [dict(r) for r in rows]})
    finally:
        conn.close()


@compliance_api.route("/poam", methods=["GET"])
def list_poam():
    """List POAM items, optionally filtered by project_id and/or status."""
    conn = _get_db()
    try:
        project_id = request.args.get("project_id")
        status = request.args.get("status")

        query = "SELECT * FROM poam_items WHERE 1=1"
        params = []
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY severity, created_at DESC"

        rows = conn.execute(query, params).fetchall()
        return jsonify({"poam_items": [dict(r) for r in rows], "total": len(rows)})
    finally:
        conn.close()


@compliance_api.route("/stig", methods=["GET"])
def list_stig():
    """List STIG findings, optionally filtered by project_id and/or status."""
    conn = _get_db()
    try:
        project_id = request.args.get("project_id")
        status = request.args.get("status")

        query = "SELECT * FROM stig_findings WHERE 1=1"
        params = []
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY severity, created_at DESC"

        rows = conn.execute(query, params).fetchall()
        return jsonify({"stig_findings": [dict(r) for r in rows], "total": len(rows)})
    finally:
        conn.close()


@compliance_api.route("/sbom", methods=["GET"])
def list_sbom():
    """List SBOM records, optionally filtered by project_id."""
    conn = _get_db()
    try:
        project_id = request.args.get("project_id")
        if project_id:
            rows = conn.execute(
                "SELECT * FROM sbom_records WHERE project_id = ? ORDER BY generated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sbom_records ORDER BY generated_at DESC"
            ).fetchall()
        return jsonify({"sbom_records": [dict(r) for r in rows]})
    finally:
        conn.close()


@compliance_api.route("/controls", methods=["GET"])
def list_controls():
    """List project control implementations, optionally filtered by project_id."""
    conn = _get_db()
    try:
        project_id = request.args.get("project_id")
        if project_id:
            rows = conn.execute(
                "SELECT pc.*, cc.family, cc.title as control_title "
                "FROM project_controls pc "
                "LEFT JOIN compliance_controls cc ON pc.control_id = cc.id "
                "WHERE pc.project_id = ? ORDER BY pc.control_id",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT pc.*, cc.family, cc.title as control_title "
                "FROM project_controls pc "
                "LEFT JOIN compliance_controls cc ON pc.control_id = cc.id "
                "ORDER BY pc.control_id"
            ).fetchall()
        return jsonify({"controls": [dict(r) for r in rows]})
    finally:
        conn.close()


@compliance_api.route("/summary", methods=["GET"])
def compliance_summary():
    """Overall compliance summary across all projects."""
    conn = _get_db()
    try:
        # SSP counts by status
        ssp_stats = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM ssp_documents GROUP BY status"
        ).fetchall()

        # POAM counts by status
        poam_stats = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM poam_items GROUP BY status"
        ).fetchall()

        # STIG counts by severity
        stig_stats = conn.execute(
            "SELECT severity, status, COUNT(*) as cnt FROM stig_findings GROUP BY severity, status"
        ).fetchall()

        # Control implementation status
        control_stats = conn.execute(
            "SELECT implementation_status, COUNT(*) as cnt FROM project_controls GROUP BY implementation_status"
        ).fetchall()

        return jsonify({
            "ssp_by_status": {r["status"]: r["cnt"] for r in ssp_stats},
            "poam_by_status": {r["status"]: r["cnt"] for r in poam_stats},
            "stig_findings": [dict(r) for r in stig_stats],
            "controls_by_status": {r["implementation_status"]: r["cnt"] for r in control_stats},
        })
    finally:
        conn.close()
