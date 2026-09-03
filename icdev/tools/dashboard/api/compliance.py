# [TEMPLATE: CUI // SP-CTI]
"""
Flask Blueprint for compliance API.
Queries ssp_documents, poam_items, stig_findings, sbom_records.
"""

from tools.db.storage import get_connection, sql_placeholder
from flask import Blueprint, jsonify, request

from tools.dashboard.config import DB_PATH

compliance_api = Blueprint("compliance_api", __name__, url_prefix="/api/compliance")


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
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
                "FROM ssp_documents WHERE project_id = %s ORDER BY created_at DESC",
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
    ph = sql_placeholder(conn)
    try:
        project_id = request.args.get("project_id")
        status = request.args.get("status")

        query = "SELECT * FROM poam_items WHERE 1=1"
        params = []
        if project_id:
            query += f" AND project_id = {ph}"
            params.append(project_id)
        if status:
            query += f" AND status = {ph}"
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
    ph = sql_placeholder(conn)
    try:
        project_id = request.args.get("project_id")
        status = request.args.get("status")

        query = "SELECT * FROM stig_findings WHERE 1=1"
        params = []
        if project_id:
            query += f" AND project_id = {ph}"
            params.append(project_id)
        if status:
            query += f" AND status = {ph}"
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
                "SELECT * FROM sbom_records WHERE project_id = %s ORDER BY generated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM sbom_records ORDER BY generated_at DESC").fetchall()
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
                "WHERE pc.project_id = %s ORDER BY pc.control_id",
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
        ssp_stats = conn.execute("SELECT status, COUNT(*) as cnt FROM ssp_documents GROUP BY status").fetchall()

        # POAM counts by status
        poam_stats = conn.execute("SELECT status, COUNT(*) as cnt FROM poam_items GROUP BY status").fetchall()

        # STIG counts by severity
        stig_stats = conn.execute(
            "SELECT severity, status, COUNT(*) as cnt FROM stig_findings GROUP BY severity, status"
        ).fetchall()

        # Control implementation status
        control_stats = conn.execute(
            "SELECT implementation_status, COUNT(*) as cnt FROM project_controls GROUP BY implementation_status"
        ).fetchall()

        return jsonify(
            {
                "ssp_by_status": {r["status"]: r["cnt"] for r in ssp_stats},
                "poam_by_status": {r["status"]: r["cnt"] for r in poam_stats},
                "stig_findings": [dict(r) for r in stig_stats],
                "controls_by_status": {r["implementation_status"]: r["cnt"] for r in control_stats},
            }
        )
    finally:
        conn.close()


@compliance_api.route("/iqe-query", methods=["POST"])
def compliance_iqe_query():
    """Natural-language IQE query against compliance collections."""
    import logging as _log
    import tools.iqe.adapters.compliance  # noqa: F401 — registers compliance.* collections
    from tools.iqe.nl_to_iqe import nl_to_iqe
    from tools.iqe.parser import parse
    from tools.iqe.executor import execute_query

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    collections = ["compliance.snapshots", "compliance.controls", "compliance.violations"]
    iqe_str = ""
    try:
        result = nl_to_iqe(question, collections)
        iqe_str = result.get("iqe", "")
        explanation = result.get("explanation", "")
        ast = parse(iqe_str)
        conn = _get_db()
        try:
            rows = execute_query(ast, conn)
        finally:
            conn.close()
        return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation,
                        "results": rows, "row_count": len(rows)})
    except Exception as exc:
        _log.getLogger(__name__).warning("compliance IQE error: %s", exc)
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500
