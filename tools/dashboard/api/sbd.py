#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Secure by Design Assessment (CISA SbD)."""

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists  # noqa: E402
from tools.dashboard.auth import require_role  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

# SbD assessment mutation is restricted to security/compliance roles,
# mirroring GOVCON_WRITE_ROLES in api/govcon.py.
COMPLIANCE_WRITE_ROLES = ("admin", "isso", "ciso")

sbd_api = Blueprint("sbd_api", __name__, url_prefix="/api/sbd")

SBD_DOMAINS = [
    "memory_safety",
    "default_security",
    "secure_protocols",
    "secure_defaults",
    "known_vulnerabilities",
    "mfa",
    "intrusion_evidence",
    "vulnerability_reporting",
]

VALID_STATUSES = [
    "not_assessed",
    "satisfied",
    "partially_satisfied",
    "not_satisfied",
    "not_applicable",
    "risk_accepted",
]


def _is_pg(conn):
    """Return True if the connection is backed by PostgreSQL."""
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _table_exists(conn, name):
    """Check if a table exists (works for both SQLite and PostgreSQL)."""
    return table_exists(conn, name)


@sbd_api.route("/stats", methods=["GET"])
def sbd_stats():
    """GET /api/sbd/stats — Aggregate SbD assessment statistics."""
    conn = None
    try:
        conn = _get_db()
        if not _table_exists(conn, "sbd_assessments"):
            return jsonify(
                {
                    "total": 0,
                    "by_status": {},
                    "by_domain": {},
                    "posture_score": 0.0,
                    "domain_scores": {},
                }
            )

        # Total
        total = conn.execute("SELECT COUNT(*) FROM sbd_assessments").fetchone()[0]

        # By status
        rows = conn.execute("SELECT status, COUNT(*) FROM sbd_assessments GROUP BY status").fetchall()
        by_status = {r[0]: r[1] for r in rows}

        # By domain
        rows = conn.execute("SELECT domain, COUNT(*) FROM sbd_assessments GROUP BY domain").fetchall()
        by_domain = {r[0]: r[1] for r in rows}

        # Overall posture score
        if total > 0:
            passing = conn.execute(
                "SELECT COUNT(*) FROM sbd_assessments WHERE status IN ('satisfied', 'partially_satisfied')"
            ).fetchone()[0]
            posture_score = round(passing / total * 100, 1)
        else:
            posture_score = 0.0

        # Domain-level scores
        domain_scores = {}
        rows = conn.execute(
            "SELECT domain, "
            "  COUNT(*) AS total, "
            "  SUM(CASE WHEN status IN ('satisfied', 'partially_satisfied') THEN 1 ELSE 0 END) AS passing "
            "FROM sbd_assessments GROUP BY domain"
        ).fetchall()
        for r in rows:
            domain_name, dom_total, dom_passing = r[0], r[1], r[2]
            domain_scores[domain_name] = round(dom_passing / dom_total * 100, 1) if dom_total > 0 else 0.0

        return jsonify(
            {
                "total": total,
                "by_status": by_status,
                "by_domain": by_domain,
                "posture_score": posture_score,
                "domain_scores": domain_scores,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


@sbd_api.route("/assessments", methods=["GET"])
def sbd_assessments():
    """GET /api/sbd/assessments — List assessments with optional filters."""
    conn = None
    try:
        conn = _get_db()
        if not _table_exists(conn, "sbd_assessments"):
            return jsonify({"assessments": [], "total": 0, "page": 1, "per_page": 50})

        filters = []
        params = []

        project_id = request.args.get("project_id")
        if project_id:
            filters.append("project_id = ?")
            params.append(project_id)

        domain = request.args.get("domain")
        if domain:
            filters.append("domain = ?")
            params.append(domain)

        status = request.args.get("status")
        if status:
            filters.append("status = ?")
            params.append(status)

        where = ""
        if filters:
            where = "WHERE " + " AND ".join(filters)

        # Pagination
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(200, max(1, int(request.args.get("per_page", 50))))
        offset = (page - 1) * per_page

        # Total matching
        total = conn.execute(
            f"SELECT COUNT(*) FROM sbd_assessments {where}",
            params,  # nosec B608 -- table/column names are internal constants, not user input
        ).fetchone()[0]

        # Fetch page
        rows = conn.execute(
            f"SELECT id, project_id, assessment_date, assessor, domain, requirement_id, "  # nosec B608 -- table/column names are internal constants, not user input
            f"status, evidence_description, evidence_path, automation_result, "
            f"cisa_commitment, notes, created_at, updated_at "
            f"FROM sbd_assessments {where} ORDER BY updated_at DESC LIMIT %s OFFSET %s",
            params + [per_page, offset],
        ).fetchall()

        columns = [
            "id",
            "project_id",
            "assessment_date",
            "assessor",
            "domain",
            "requirement_id",
            "status",
            "evidence_description",
            "evidence_path",
            "automation_result",
            "cisa_commitment",
            "notes",
            "created_at",
            "updated_at",
        ]
        assessments = [dict(zip(columns, row)) for row in rows]

        return jsonify(
            {
                "assessments": assessments,
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


@sbd_api.route("/domains", methods=["GET"])
def sbd_domains():
    """GET /api/sbd/domains — Domain summary with status counts per domain."""
    conn = None
    try:
        conn = _get_db()
        if not _table_exists(conn, "sbd_assessments"):
            return jsonify({"domains": [{"domain": d, "total": 0, "by_status": {}} for d in SBD_DOMAINS]})

        rows = conn.execute("SELECT domain, status, COUNT(*) FROM sbd_assessments GROUP BY domain, status").fetchall()

        # Build nested structure
        domain_map = {d: {} for d in SBD_DOMAINS}
        for domain_name, status_val, count in rows:
            if domain_name not in domain_map:
                domain_map[domain_name] = {}
            domain_map[domain_name][status_val] = count

        domains = []
        for d in SBD_DOMAINS:
            status_counts = domain_map.get(d, {})
            total = sum(status_counts.values())
            domains.append(
                {
                    "domain": d,
                    "total": total,
                    "by_status": status_counts,
                }
            )

        return jsonify({"domains": domains})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


@sbd_api.route("/assess", methods=["POST"])
@require_role(*COMPLIANCE_WRITE_ROLES)
def sbd_assess():
    """POST /api/sbd/assess — Trigger SbD assessment for a project."""
    data = request.get_json(force=True, silent=True) or {}
    project_id = data.get("project_id") or request.args.get("project_id", "")

    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    try:
        from tools.compliance.sbd_assessor import assess_project

        result = assess_project(project_id=project_id)
        return jsonify(result)
    except ImportError:
        return jsonify({"error": "sbd_assessor module not available"}), 501
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@sbd_api.route("/exceptions", methods=["GET"])
def sbd_exceptions():
    """GET /api/sbd/exceptions — List risk-accepted items (exception lifecycle)."""
    conn = None
    try:
        conn = _get_db()
        if not _table_exists(conn, "sbd_assessments"):
            return jsonify({"exceptions": [], "total": 0})

        if _is_pg(conn):
            days_expr = (
                "CAST(EXTRACT(EPOCH FROM (NOW() - assessment_date::timestamp)) / 86400 AS INTEGER) AS days_since"
            )
        else:
            days_expr = "CAST(julianday('now') - julianday(assessment_date) AS INTEGER) AS days_since"
        rows = conn.execute(
            f"SELECT id, project_id, assessment_date, assessor, domain, requirement_id, "  # nosec B608 -- table/column names are internal constants, not user input
            f"evidence_description, evidence_path, notes, created_at, updated_at, "
            f"{days_expr} "
            f"FROM sbd_assessments WHERE status = 'risk_accepted' "
            f"ORDER BY assessment_date ASC"
        ).fetchall()

        columns = [
            "id",
            "project_id",
            "assessment_date",
            "assessor",
            "domain",
            "requirement_id",
            "evidence_description",
            "evidence_path",
            "notes",
            "created_at",
            "updated_at",
            "days_since",
        ]
        exceptions = []
        for row in rows:
            item = dict(zip(columns, row))
            days = item["days_since"] or 0
            item["days_remaining"] = max(0, 365 - days)
            item["expired"] = days > 365
            exceptions.append(item)

        return jsonify({"exceptions": exceptions, "total": len(exceptions)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()
