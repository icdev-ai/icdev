#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: ATO Package Builder."""

import sys
from pathlib import Path
from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

ato_package_api = Blueprint("ato_package_api", __name__, url_prefix="/api/ato-package")


def _is_pg(conn):
    """Check if connection is PostgreSQL (vs SQLite)."""
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _scalar(row):
    """Extract scalar value from a row regardless of backend (tuple or dict)."""
    if row is None:
        return 0
    if isinstance(row, dict):
        return list(row.values())[0]
    return row[0]


def _count(conn, query, params=()):
    """Execute a COUNT query and return the scalar integer result."""
    return _scalar(conn.execute(query, params).fetchone())


def _date_now_expr(conn):
    """Return the SQL expression for 'current date' per backend."""
    return "CURRENT_DATE" if _is_pg(conn) else "DATE('now')"


def _instr_expr(conn, col, char):
    """Return INSTR/POSITION expression per backend."""
    if _is_pg(conn):
        return f"POSITION('{char}' IN {col})"
    return f"INSTR({col}, '{char}')"


PACKAGE_STEPS = [
    {"id": "ssp", "name": "System Security Plan", "required": True},
    {"id": "controls", "name": "Control Implementation", "required": True},
    {"id": "poam", "name": "Plan of Action & Milestones", "required": True},
    {"id": "sar", "name": "Security Assessment Report", "required": True},
    {"id": "stig", "name": "STIG Checklist", "required": True},
    {"id": "sbom", "name": "Software Bill of Materials", "required": True},
    {"id": "evidence", "name": "Evidence Collection", "required": True},
    {"id": "boundary", "name": "System Boundary Diagram", "required": False},
    {"id": "contingency", "name": "Contingency Plan", "required": False},
    {"id": "incident", "name": "Incident Response Plan", "required": False},
]


class _PGCompatConn:
    """Silently pre-translate ? → %s for PG so translate_sql never warns."""
    def __init__(self, conn):
        self._conn = conn
        self._pg = getattr(conn, "_backend", "sqlite") == "postgresql"
    def _fix(self, sql):
        return sql.replace("?", "%s") if self._pg and "?" in sql else sql
    def execute(self, sql, params=()):
        return self._conn.execute(self._fix(sql), params)
    def executemany(self, sql, seq):
        return self._conn.executemany(self._fix(sql), seq)
    def commit(self): return self._conn.commit()
    def rollback(self): return self._conn.rollback()
    def close(self): return self._conn.close()
    def __getattr__(self, name): return getattr(self._conn, name)


def _get_db():
    """Return a connection to icdev.db with Row factory."""
    conn = get_connection(db_path=str(DB_PATH))
    return _PGCompatConn(conn)


def _table_exists(conn, table_name):
    """Check if a table exists (works for both SQLite and PostgreSQL)."""
    return table_exists(conn, table_name)


def _check_step_status(conn, step_id, project_id):
    """Check status for a single package step. Returns (status, detail)."""
    where_project = " WHERE project_id = ?" if project_id else ""
    params = (project_id,) if project_id else ()

    if step_id == "ssp":
        if not _table_exists(conn, "ssp_documents"):
            return "incomplete", "SSP table not found"
        q = "SELECT COUNT(*) AS cnt FROM ssp_documents"
        if project_id:
            q += " WHERE project_id = ? AND status = 'approved'"
        else:
            q += " WHERE status = 'approved'"
        approved = _count(conn, q, params)
        if approved > 0:
            return "complete", f"{approved} approved SSP document(s)"
        # Check if any drafts exist
        total_q = "SELECT COUNT(*) AS cnt FROM ssp_documents" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total > 0:
            return "warning", f"{total} SSP document(s) but none approved"
        return "incomplete", "No SSP documents found"

    elif step_id == "controls":
        if not _table_exists(conn, "project_controls"):
            return "incomplete", "Controls table not found"
        total_q = "SELECT COUNT(*) AS cnt FROM project_controls" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total == 0:
            return "incomplete", "No controls assigned"
        impl_q = "SELECT COUNT(*) AS cnt FROM project_controls"
        if project_id:
            impl_q += " WHERE project_id = ? AND implementation_status IN ('implemented', 'not_applicable')"
        else:
            impl_q += " WHERE implementation_status IN ('implemented', 'not_applicable')"
        implemented = _count(conn, impl_q, params)
        pct = (implemented / total) * 100 if total > 0 else 0
        if pct >= 80:
            return "complete", f"{implemented}/{total} controls implemented ({pct:.0f}%)"
        elif pct >= 50:
            return "warning", f"{implemented}/{total} controls implemented ({pct:.0f}%) — need 80%"
        return "incomplete", f"{implemented}/{total} controls implemented ({pct:.0f}%) — need 80%"

    elif step_id == "poam":
        if not _table_exists(conn, "poam_items"):
            return "incomplete", "POAM table not found"
        total_q = "SELECT COUNT(*) AS cnt FROM poam_items" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total > 0:
            return "complete", f"{total} POAM item(s) documented"
        return "incomplete", "No POAM items — document due diligence"

    elif step_id == "sar":
        if not _table_exists(conn, "cato_evidence"):
            return "incomplete", "Evidence table not found"
        total_q = "SELECT COUNT(*) AS cnt FROM cato_evidence" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total > 0:
            return "complete", f"{total} assessment evidence record(s)"
        return "incomplete", "No assessment evidence collected"

    elif step_id == "stig":
        if not _table_exists(conn, "stig_findings"):
            return "incomplete", "STIG findings table not found"
        total_q = "SELECT COUNT(*) AS cnt FROM stig_findings" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total == 0:
            return "incomplete", "No STIG findings recorded"
        cat1_q = "SELECT COUNT(*) AS cnt FROM stig_findings"
        if project_id:
            cat1_q += " WHERE project_id = ? AND severity = 'CAT1' AND status = 'Open'"
        else:
            cat1_q += " WHERE severity = 'CAT1' AND status = 'Open'"
        cat1_open = _count(conn, cat1_q, params)
        if cat1_open > 0:
            return "warning", f"{cat1_open} CAT1 Open finding(s) — must remediate"
        return "complete", f"{total} findings recorded, 0 CAT1 Open"

    elif step_id == "sbom":
        if not _table_exists(conn, "sbom_records"):
            return "incomplete", "SBOM table not found"
        total_q = "SELECT COUNT(*) AS cnt FROM sbom_records" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total > 0:
            return "complete", f"{total} SBOM record(s) on file"
        return "incomplete", "No SBOM records generated"

    elif step_id == "evidence":
        if not _table_exists(conn, "cato_evidence"):
            return "incomplete", "Evidence table not found"
        total_q = "SELECT COUNT(*) AS cnt FROM cato_evidence" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total == 0:
            return "incomplete", "No evidence collected"
        current_q = "SELECT COUNT(*) AS cnt FROM cato_evidence"
        if project_id:
            current_q += " WHERE project_id = ? AND status = 'current'"
        else:
            current_q += " WHERE status = 'current'"
        current = _count(conn, current_q, params)
        pct = (current / total) * 100 if total > 0 else 0
        if pct >= 50:
            return "complete", f"{current}/{total} evidence items current ({pct:.0f}%)"
        return "warning", f"{current}/{total} evidence items current ({pct:.0f}%) — need 50%"

    elif step_id == "boundary":
        # Optional — check if SSP has system_boundary populated
        if not _table_exists(conn, "ssp_documents"):
            return "incomplete", "No boundary diagram available"
        q = "SELECT COUNT(*) AS cnt FROM ssp_documents"
        if project_id:
            q += " WHERE project_id = ? AND system_boundary IS NOT NULL AND system_boundary != ''"
        else:
            q += " WHERE system_boundary IS NOT NULL AND system_boundary != ''"
        count = _count(conn, q, params)
        if count > 0:
            return "complete", "System boundary diagram attached"
        return "incomplete", "No system boundary diagram"

    elif step_id == "contingency":
        # Optional — check compliance_controls for CP family
        if not _table_exists(conn, "project_controls"):
            return "incomplete", "No contingency plan controls"
        q = "SELECT COUNT(*) AS cnt FROM project_controls"
        like_params = list(params) + ["CP-%"]
        if project_id:
            q += " WHERE project_id = ? AND control_id LIKE ?"
        else:
            q += " WHERE control_id LIKE ?"
        count = _count(conn, q, tuple(like_params))
        if count > 0:
            return "complete", f"{count} contingency (CP) controls mapped"
        return "incomplete", "No contingency plan controls mapped"

    elif step_id == "incident":
        # Optional — check for IR family controls
        if not _table_exists(conn, "project_controls"):
            return "incomplete", "No incident response controls"
        q = "SELECT COUNT(*) AS cnt FROM project_controls"
        like_params = list(params) + ["IR-%"]
        if project_id:
            q += " WHERE project_id = ? AND control_id LIKE ?"
        else:
            q += " WHERE control_id LIKE ?"
        count = _count(conn, q, tuple(like_params))
        if count > 0:
            return "complete", f"{count} incident response (IR) controls mapped"
        return "incomplete", "No incident response controls mapped"

    return "incomplete", "Unknown step"


# ---------------------------------------------------------------------------
# GET /api/ato-package/status — Package readiness for a project
# ---------------------------------------------------------------------------
@ato_package_api.route("/status", methods=["GET"])
def package_status():
    """Check ATO package readiness across all steps."""
    project_id = request.args.get("project_id")
    conn = _get_db()
    try:
        steps = []
        required_total = 0
        required_complete = 0
        all_complete = 0

        for step in PACKAGE_STEPS:
            status, detail = _check_step_status(conn, step["id"], project_id)
            step_result = {
                "id": step["id"],
                "name": step["name"],
                "required": step["required"],
                "status": status,
                "detail": detail,
            }
            steps.append(step_result)

            if step["required"]:
                required_total += 1
                if status == "complete":
                    required_complete += 1

            if status == "complete":
                all_complete += 1

        readiness_pct = (required_complete / required_total * 100) if required_total > 0 else 0

        return jsonify(
            {
                "project_id": project_id,
                "steps": steps,
                "readiness_pct": round(readiness_pct, 1),
                "required_complete": required_complete,
                "required_total": required_total,
                "total_complete": all_complete,
                "total_steps": len(PACKAGE_STEPS),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /api/ato-package/ssp — SSP documents for a project
# ---------------------------------------------------------------------------
@ato_package_api.route("/ssp", methods=["GET"])
def ssp_documents():
    """Return SSP documents for a project."""
    project_id = request.args.get("project_id")
    conn = _get_db()
    try:
        if not _table_exists(conn, "ssp_documents"):
            return jsonify({"ssp_documents": [], "message": "Table not found"})

        if project_id:
            rows = conn.execute(
                "SELECT id, project_id, version, system_name, system_boundary, "
                "authorization_type, status, approved_by, approved_at, "
                "classification, created_at "
                "FROM ssp_documents WHERE project_id = %s ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, project_id, version, system_name, system_boundary, "
                "authorization_type, status, approved_by, approved_at, "
                "classification, created_at "
                "FROM ssp_documents ORDER BY created_at DESC"
            ).fetchall()

        return jsonify({"ssp_documents": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /api/ato-package/controls-summary — Control implementation summary
# ---------------------------------------------------------------------------
@ato_package_api.route("/controls-summary", methods=["GET"])
def controls_summary():
    """Return control implementation summary grouped by family."""
    project_id = request.args.get("project_id")
    conn = _get_db()
    try:
        if not _table_exists(conn, "project_controls"):
            return jsonify(
                {
                    "families": [],
                    "totals": {
                        "total": 0,
                        "implemented": 0,
                        "partial": 0,
                        "planned": 0,
                    },
                }
            )

        # Build base query — extract family from control_id (e.g. AC-1 → AC)
        base_where = " WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()

        # Get family-level summary (INSTR is SQLite-only; PG uses POSITION)
        instr_expr = _instr_expr(conn, "control_id", "-")
        q = (
            "SELECT "  # nosec B608 -- table/column names are internal constants, not user input
            f"  CASE WHEN {instr_expr} > 0 "
            f"    THEN SUBSTR(control_id, 1, {instr_expr} - 1) "
            "    ELSE control_id END AS family, "
            "  COUNT(*) AS total, "
            "  SUM(CASE WHEN implementation_status IN ('implemented', 'not_applicable') THEN 1 ELSE 0 END) AS implemented, "
            "  SUM(CASE WHEN implementation_status = 'partial' THEN 1 ELSE 0 END) AS partial, "
            "  SUM(CASE WHEN implementation_status IN ('planned', 'not_implemented') THEN 1 ELSE 0 END) AS planned "
            "FROM project_controls" + base_where + " GROUP BY family ORDER BY family"
        )
        rows = conn.execute(q, params).fetchall()
        families = [dict(r) for r in rows]

        # Totals
        total = sum(f["total"] for f in families)
        implemented = sum(f["implemented"] for f in families)
        partial = sum(f["partial"] for f in families)
        planned = sum(f["planned"] for f in families)

        return jsonify(
            {
                "families": families,
                "totals": {
                    "total": total,
                    "implemented": implemented,
                    "partial": partial,
                    "planned": planned,
                },
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /api/ato-package/poam-summary — POAM summary
# ---------------------------------------------------------------------------
@ato_package_api.route("/poam-summary", methods=["GET"])
def poam_summary():
    """Return POAM summary with severity/status counts and overdue items."""
    project_id = request.args.get("project_id")
    conn = _get_db()
    try:
        if not _table_exists(conn, "poam_items"):
            return jsonify(
                {
                    "by_severity": {},
                    "by_status": {},
                    "overdue": 0,
                    "total": 0,
                }
            )

        base_where = " WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()

        # Count by severity
        q_sev = (
            "SELECT severity, COUNT(*) AS count FROM poam_items"  # nosec B608 -- table/column names are internal constants, not user input
            + base_where
            + " GROUP BY severity"
        )
        sev_rows = conn.execute(q_sev, params).fetchall()
        by_severity = {r["severity"]: r["count"] for r in sev_rows}

        # Count by status
        q_status = (
            "SELECT status, COUNT(*) AS count FROM poam_items"  # nosec B608 -- table/column names are internal constants, not user input
            + base_where
            + " GROUP BY status"
        )
        status_rows = conn.execute(q_status, params).fetchall()
        by_status = {r["status"]: r["count"] for r in status_rows}

        # Overdue count
        date_now = _date_now_expr(conn)
        q_overdue = (
            "SELECT COUNT(*) AS cnt FROM poam_items"  # nosec B608 -- table/column names are internal constants, not user input
            + base_where
            + (" AND" if project_id else " WHERE")
            + f" milestone_date < {date_now} AND status NOT IN ('closed', 'completed', 'resolved')"
        )
        overdue = _count(conn, q_overdue, params)

        # Total
        q_total = "SELECT COUNT(*) AS cnt FROM poam_items" + base_where  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, q_total, params)

        return jsonify(
            {
                "by_severity": by_severity,
                "by_status": by_status,
                "overdue": overdue,
                "total": total,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /api/ato-package/checklist — Pre-submission checklist
# ---------------------------------------------------------------------------
@ato_package_api.route("/checklist", methods=["GET"])
def pre_submission_checklist():
    """Verify pre-submission requirements for ATO package."""
    project_id = request.args.get("project_id")
    conn = _get_db()
    try:
        checks = []
        where_project = " WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()

        # 1. No CAT1 Open STIGs
        if _table_exists(conn, "stig_findings"):
            q = "SELECT COUNT(*) AS cnt FROM stig_findings"
            if project_id:
                q += " WHERE project_id = ? AND severity = 'CAT1' AND status = 'Open'"
            else:
                q += " WHERE severity = 'CAT1' AND status = 'Open'"
            cat1 = _count(conn, q, params)
            checks.append(
                {
                    "name": "No CAT1 Open STIG Findings",
                    "status": "PASS" if cat1 == 0 else "FAIL",
                    "detail": f"{cat1} CAT1 Open finding(s)" if cat1 > 0 else "All CAT1 findings remediated",
                }
            )
        else:
            checks.append(
                {
                    "name": "No CAT1 Open STIG Findings",
                    "status": "WARN",
                    "detail": "STIG findings table not found",
                }
            )

        # 2. SSP Approved
        if _table_exists(conn, "ssp_documents"):
            q = "SELECT COUNT(*) AS cnt FROM ssp_documents"
            if project_id:
                q += " WHERE project_id = ? AND status = 'approved'"
            else:
                q += " WHERE status = 'approved'"
            approved = _count(conn, q, params)
            checks.append(
                {
                    "name": "SSP Approved",
                    "status": "PASS" if approved > 0 else "FAIL",
                    "detail": f"{approved} approved SSP(s)" if approved > 0 else "No approved SSP document",
                }
            )
        else:
            checks.append(
                {
                    "name": "SSP Approved",
                    "status": "FAIL",
                    "detail": "SSP table not found",
                }
            )

        # 3. Controls >= 80% implemented
        if _table_exists(conn, "project_controls"):
            total_q = "SELECT COUNT(*) AS cnt FROM project_controls" + where_project  # nosec B608 -- table/column names are internal constants, not user input
            total = _count(conn, total_q, params)
            impl_q = "SELECT COUNT(*) AS cnt FROM project_controls"
            if project_id:
                impl_q += " WHERE project_id = ? AND implementation_status IN ('implemented', 'not_applicable')"
            else:
                impl_q += " WHERE implementation_status IN ('implemented', 'not_applicable')"
            implemented = _count(conn, impl_q, params)
            pct = (implemented / total * 100) if total > 0 else 0
            checks.append(
                {
                    "name": "Controls >= 80% Implemented",
                    "status": "PASS" if pct >= 80 else "FAIL",
                    "detail": f"{implemented}/{total} ({pct:.0f}%)",
                }
            )
        else:
            checks.append(
                {
                    "name": "Controls >= 80% Implemented",
                    "status": "FAIL",
                    "detail": "Controls table not found",
                }
            )

        # 4. No critical POAMs overdue
        date_now = _date_now_expr(conn)
        if _table_exists(conn, "poam_items"):
            q = "SELECT COUNT(*) AS cnt FROM poam_items"
            if project_id:
                q += f" WHERE project_id = ? AND severity IN ('critical', 'high') AND milestone_date < {date_now} AND status NOT IN ('closed', 'completed', 'resolved')"
            else:
                q += f" WHERE severity IN ('critical', 'high') AND milestone_date < {date_now} AND status NOT IN ('closed', 'completed', 'resolved')"
            overdue_crit = _count(conn, q, params)
            checks.append(
                {
                    "name": "No Critical/High POAMs Overdue",
                    "status": "PASS" if overdue_crit == 0 else "FAIL",
                    "detail": f"{overdue_crit} overdue critical/high POAM(s)"
                    if overdue_crit > 0
                    else "No overdue critical/high POAMs",
                }
            )
        else:
            checks.append(
                {
                    "name": "No Critical/High POAMs Overdue",
                    "status": "WARN",
                    "detail": "POAM table not found",
                }
            )

        # 5. Evidence fresh (>= 50% current)
        if _table_exists(conn, "cato_evidence"):
            total_q = "SELECT COUNT(*) AS cnt FROM cato_evidence" + where_project  # nosec B608 -- table/column names are internal constants, not user input
            total = _count(conn, total_q, params)
            if total > 0:
                current_q = "SELECT COUNT(*) AS cnt FROM cato_evidence"
                if project_id:
                    current_q += " WHERE project_id = ? AND status = 'current'"
                else:
                    current_q += " WHERE status = 'current'"
                current = _count(conn, current_q, params)
                pct = current / total * 100
                checks.append(
                    {
                        "name": "Evidence Freshness >= 50%",
                        "status": "PASS" if pct >= 50 else "WARN",
                        "detail": f"{current}/{total} current ({pct:.0f}%)",
                    }
                )
            else:
                checks.append(
                    {
                        "name": "Evidence Freshness >= 50%",
                        "status": "WARN",
                        "detail": "No evidence records found",
                    }
                )
        else:
            checks.append(
                {
                    "name": "Evidence Freshness >= 50%",
                    "status": "WARN",
                    "detail": "Evidence table not found",
                }
            )

        # 6. SBOM current
        if _table_exists(conn, "sbom_records"):
            total_q = "SELECT COUNT(*) AS cnt FROM sbom_records" + where_project  # nosec B608 -- table/column names are internal constants, not user input
            total = _count(conn, total_q, params)
            checks.append(
                {
                    "name": "SBOM Current",
                    "status": "PASS" if total > 0 else "FAIL",
                    "detail": f"{total} SBOM record(s) on file" if total > 0 else "No SBOM generated",
                }
            )
        else:
            checks.append(
                {
                    "name": "SBOM Current",
                    "status": "FAIL",
                    "detail": "SBOM table not found",
                }
            )

        all_pass = all(c["status"] == "PASS" for c in checks)
        any_fail = any(c["status"] == "FAIL" for c in checks)

        return jsonify(
            {
                "checks": checks,
                "all_pass": all_pass,
                "has_failures": any_fail,
                "pass_count": sum(1 for c in checks if c["status"] == "PASS"),
                "total_checks": len(checks),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POST /api/ato-package/generate — Generate ATO package
# ---------------------------------------------------------------------------
@ato_package_api.route("/generate", methods=["POST"])
def generate_package():
    """Generate an ATO package for a project."""
    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id")
    package_type = data.get("package_type", "initial")

    if not project_id:
        return jsonify({"error": "project_id is required"}), 400

    if package_type not in ("initial", "renewal", "cato"):
        return jsonify({"error": f"Invalid package_type: {package_type}. Must be initial, renewal, or cato"}), 400

    try:
        from tools.compliance.ato_packager import generate_package as _generate

        result = _generate(project_id=project_id, package_type=package_type)
        return jsonify(
            {
                "status": "success",
                "project_id": project_id,
                "package_type": package_type,
                "result": result,
            }
        )
    except ImportError:
        return jsonify(
            {
                "status": "not_implemented",
                "message": "ATO package generator module (tools.compliance.ato_packager) is not available yet.",
                "project_id": project_id,
                "package_type": package_type,
            }
        ), 501
    except Exception as e:
        return jsonify({"error": str(e)}), 500
