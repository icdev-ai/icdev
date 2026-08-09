#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Security Scan Results."""

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

security_scan_api = Blueprint("security_scan_api", __name__, url_prefix="/api/security-scan")


def _is_pg(conn):
    """Return True if the connection is backed by PostgreSQL."""
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists (works for both SQLite and PostgreSQL)."""
    return table_exists(conn, table_name)


@security_scan_api.route("/stats", methods=["GET"])
def scan_stats():
    """GET /api/security-scan/stats — Aggregate security scan statistics."""
    conn = _get_db()
    try:
        stats = {
            "total_findings": 0,
            "open_findings": 0,
            "remediated": 0,
            "stig": {"by_severity": {}, "by_status": {}},
            "dependency": {"by_severity": {}, "by_status": {}},
            "sbom": {"latest": []},
            "trends": [],
        }

        # --- STIG findings ---
        if _table_exists(conn, "stig_findings"):
            # Count by severity
            rows = conn.execute("SELECT severity, COUNT(*) FROM stig_findings GROUP BY severity").fetchall()
            for sev, cnt in rows:
                stats["stig"]["by_severity"][sev] = cnt
                stats["total_findings"] += cnt

            # Count by status
            rows = conn.execute("SELECT status, COUNT(*) FROM stig_findings GROUP BY status").fetchall()
            for st, cnt in rows:
                stats["stig"]["by_status"][st] = cnt

            # Open findings
            row = conn.execute("SELECT COUNT(*) FROM stig_findings WHERE status = 'Open'").fetchone()
            stats["open_findings"] += row[0]

            # Remediated (NotAFinding)
            row = conn.execute("SELECT COUNT(*) FROM stig_findings WHERE status = 'NotAFinding'").fetchone()
            stats["remediated"] += row[0]

        # --- Dependency vulnerabilities ---
        if _table_exists(conn, "dependency_vulnerabilities"):
            # Count by severity
            rows = conn.execute(
                "SELECT severity, COUNT(*) FROM dependency_vulnerabilities GROUP BY severity"
            ).fetchall()
            for sev, cnt in rows:
                stats["dependency"]["by_severity"][sev] = cnt
                stats["total_findings"] += cnt

            # Count by status
            rows = conn.execute("SELECT status, COUNT(*) FROM dependency_vulnerabilities GROUP BY status").fetchall()
            for st, cnt in rows:
                stats["dependency"]["by_status"][st] = cnt

            # Open findings
            row = conn.execute("SELECT COUNT(*) FROM dependency_vulnerabilities WHERE status = 'open'").fetchone()
            stats["open_findings"] += row[0]

            # Remediated
            row = conn.execute("SELECT COUNT(*) FROM dependency_vulnerabilities WHERE status = 'remediated'").fetchone()
            stats["remediated"] += row[0]

        # --- Latest SBOM per project ---
        if _table_exists(conn, "sbom_records"):
            rows = conn.execute(
                """SELECT s.project_id, s.version, s.format, s.component_count,
                          s.vulnerability_count, s.generated_at
                   FROM sbom_records s
                   INNER JOIN (
                       SELECT project_id, MAX(generated_at) AS max_ts
                       FROM sbom_records GROUP BY project_id
                   ) latest ON s.project_id = latest.project_id
                              AND s.generated_at = latest.max_ts
                   ORDER BY s.generated_at DESC"""
            ).fetchall()
            stats["sbom"]["latest"] = [
                {
                    "project_id": r[0],
                    "version": r[1],
                    "format": r[2],
                    "component_count": r[3],
                    "vulnerability_count": r[4],
                    "generated_at": r[5],
                }
                for r in rows
            ]

        return jsonify(stats)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@security_scan_api.route("/findings", methods=["GET"])
def scan_findings():
    """GET /api/security-scan/findings — Paginated unified findings list."""
    severity = request.args.get("severity")
    status = request.args.get("status")
    project_id = request.args.get("project_id")
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 50)), 1), 200)
    offset = (page - 1) * per_page

    conn = _get_db()
    try:
        union_parts = []
        params_count = []
        params_select = []

        # --- STIG findings ---
        if _table_exists(conn, "stig_findings"):
            where_stig = []
            p = []
            if severity:
                where_stig.append("severity = ?")
                p.append(severity)
            if status:
                where_stig.append("status = ?")
                p.append(status)
            if project_id:
                where_stig.append("project_id = ?")
                p.append(project_id)
            where_clause = (" WHERE " + " AND ".join(where_stig)) if where_stig else ""
            union_parts.append(
                f"""SELECT id, project_id, stig_id AS vuln_id, title, severity,
                           status, 'stig' AS source, CAST(created_at AS TEXT) AS created_at
                    FROM stig_findings{where_clause}"""  # nosec B608 -- table/column names are internal constants, not user input
            )
            params_count.extend(p)
            params_select.extend(p)

        # --- Dependency vulnerabilities ---
        if _table_exists(conn, "dependency_vulnerabilities"):
            where_dep = []
            p = []
            if severity:
                where_dep.append("severity = ?")
                p.append(severity)
            if status:
                where_dep.append("status = ?")
                p.append(status)
            if project_id:
                where_dep.append("project_id = ?")
                p.append(project_id)
            where_clause = (" WHERE " + " AND ".join(where_dep)) if where_dep else ""
            union_parts.append(
                f"""SELECT id, project_id, cve_id AS vuln_id, title, severity,
                           status, 'dependency' AS source, CAST(created_at AS TEXT) AS created_at
                    FROM dependency_vulnerabilities{where_clause}"""  # nosec B608 -- table/column names are internal constants, not user input
            )
            params_count.extend(p)
            params_select.extend(p)

        if not union_parts:
            return jsonify({"findings": [], "total": 0, "page": page, "per_page": per_page})

        union_sql = " UNION ALL ".join(union_parts)

        # Total count
        count_row = conn.execute(
            f"SELECT COUNT(*) FROM ({union_sql})",
            params_count,  # nosec B608 -- table/column names are internal constants, not user input
        ).fetchone()
        total = count_row[0]

        # Paginated results
        rows = conn.execute(
            f"{union_sql} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params_select + [per_page, offset],
        ).fetchall()

        findings = [
            {
                "id": r[0],
                "project_id": r[1],
                "vuln_id": r[2],
                "title": r[3],
                "severity": r[4],
                "status": r[5],
                "source": r[6],
                "created_at": r[7],
            }
            for r in rows
        ]

        return jsonify(
            {
                "findings": findings,
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@security_scan_api.route("/run", methods=["POST"])
def trigger_scan():
    """POST /api/security-scan/run — Trigger a security scan."""
    data = request.get_json(force=True, silent=True) or {}
    project_id = data.get("project_id")
    scan_types = data.get("scan_types", ["sast", "dependency", "secret", "container"])

    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    try:
        from tools.security.security_scanner import run_security_scan

        result = run_security_scan(project_id=project_id, scan_types=scan_types)
        return jsonify(result)
    except ImportError:
        return jsonify(
            {
                "error": "security_scanner module not available",
                "project_id": project_id,
                "scan_types": scan_types,
            }
        ), 501
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@security_scan_api.route("/sbom", methods=["GET"])
def list_sbom():
    """GET /api/security-scan/sbom — List SBOM records."""
    project_id = request.args.get("project_id")

    conn = _get_db()
    try:
        if not _table_exists(conn, "sbom_records"):
            return jsonify({"records": [], "total": 0})

        if project_id:
            rows = conn.execute(
                """SELECT id, project_id, version, format, file_path,
                          component_count, vulnerability_count, generated_at
                   FROM sbom_records WHERE project_id = %s
                   ORDER BY generated_at DESC""",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, project_id, version, format, file_path,
                          component_count, vulnerability_count, generated_at
                   FROM sbom_records ORDER BY generated_at DESC"""
            ).fetchall()

        records = [
            {
                "id": r[0],
                "project_id": r[1],
                "version": r[2],
                "format": r[3],
                "file_path": r[4],
                "component_count": r[5],
                "vulnerability_count": r[6],
                "generated_at": r[7],
            }
            for r in rows
        ]

        return jsonify({"records": records, "total": len(records)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@security_scan_api.route("/trends", methods=["GET"])
def scan_trends():
    """GET /api/security-scan/trends — Weekly scan trends for charting."""
    conn = _get_db()
    try:
        weekly = {}

        pg = _is_pg(conn)
        if pg:
            week_expr = "to_char(created_at::timestamp, 'IYYY-\"W\"IW')"
        else:
            week_expr = "strftime('%Y-W%W', created_at)"

        # --- STIG findings by week ---
        if _table_exists(conn, "stig_findings"):
            rows = conn.execute(
                f"""SELECT {week_expr} AS week, COUNT(*)
                   FROM stig_findings
                   WHERE created_at IS NOT NULL
                   GROUP BY week ORDER BY week"""  # nosec B608 -- table/column names are internal constants, not user input
            ).fetchall()
            for week, cnt in rows:
                if week:
                    weekly.setdefault(week, {"week": week, "stig": 0, "dependency": 0})
                    weekly[week]["stig"] = cnt

        # --- Dependency vulnerabilities by week ---
        if _table_exists(conn, "dependency_vulnerabilities"):
            rows = conn.execute(
                f"""SELECT {week_expr} AS week, COUNT(*)
                   FROM dependency_vulnerabilities
                   WHERE created_at IS NOT NULL
                   GROUP BY week ORDER BY week"""  # nosec B608 -- table/column names are internal constants, not user input
            ).fetchall()
            for week, cnt in rows:
                if week:
                    weekly.setdefault(week, {"week": week, "stig": 0, "dependency": 0})
                    weekly[week]["dependency"] = cnt

        # Add totals and sort
        trends = sorted(weekly.values(), key=lambda x: x["week"])
        for t in trends:
            t["total"] = t["stig"] + t["dependency"]

        return jsonify({"trends": trends, "total_weeks": len(trends)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()
