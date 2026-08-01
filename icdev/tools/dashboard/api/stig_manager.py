#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: STIG Benchmark Manager."""

import sys
from pathlib import Path
from flask import Blueprint, g, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists, sql_placeholder  # noqa: E402
from tools.dashboard.auth import require_role  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

# Flipping a STIG finding status (incl. CAT1 ATO-blockers) is a compliance
# posture mutation — restrict to security/compliance roles, mirroring
# GOVCON_WRITE_ROLES in api/govcon.py.
COMPLIANCE_WRITE_ROLES = ("admin", "isso", "ciso")

stig_manager_api = Blueprint("stig_manager_api", __name__, url_prefix="/api/stig-manager")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stig_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    stig_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('CAT1', 'CAT2', 'CAT3')),
    title TEXT NOT NULL,
    description TEXT,
    check_content TEXT,
    fix_text TEXT,
    status TEXT DEFAULT 'Open' CHECK(status IN ('Open', 'NotAFinding', 'Not_Applicable', 'Not_Reviewed')),
    comments TEXT,
    target_type TEXT,
    assessed_by TEXT,
    assessed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _current_actor() -> str:
    """Attribution for a compliance mutation — bound to the authenticated user,
    NEVER the request body (nav-comp-06). Taking ``assessed_by`` from the POST
    body let a caller attribute a STIG finding flip (incl. CAT1 ATO-blockers) to
    anyone. ``@require_role`` guarantees ``g.current_user`` is set here."""
    user = getattr(g, "current_user", None)
    if isinstance(user, dict):
        return user.get("email") or user.get("display_name") or user.get("id") or "dashboard"
    return "dashboard"


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists (works for both SQLite and PostgreSQL)."""
    return table_exists(conn, table_name)


@stig_manager_api.route("/stats", methods=["GET"])
def stats():
    """GET /api/stig-manager/stats — Overall STIG statistics."""
    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        if not _table_exists(conn, "stig_findings"):
            return jsonify(
                {
                    "total": 0,
                    "by_severity": {"CAT1": 0, "CAT2": 0, "CAT3": 0},
                    "by_status": {"Open": 0, "NotAFinding": 0, "Not_Applicable": 0, "Not_Reviewed": 0},
                    "coverage": 0.0,
                    "benchmarks_loaded": 0,
                    "targets_assessed": 0,
                    "cat1_open": 0,
                }
            )

        project_id = request.args.get("project_id")
        where = ""
        params = []
        if project_id:
            where = f" WHERE project_id = {ph}"
            params = [project_id]

        # Total
        row = conn.execute(
            f"SELECT COUNT(*) FROM stig_findings{where}",
            params,  # nosec B608 -- table/column names are internal constants, not user input
        ).fetchone()
        total = row[0]

        # By severity
        by_severity = {"CAT1": 0, "CAT2": 0, "CAT3": 0}
        rows = conn.execute(
            f"SELECT severity, COUNT(*) FROM stig_findings{where} GROUP BY severity",  # nosec B608 -- table/column names are internal constants, not user input
            params,
        ).fetchall()
        for sev, cnt in rows:
            by_severity[sev] = cnt

        # By status
        by_status = {"Open": 0, "NotAFinding": 0, "Not_Applicable": 0, "Not_Reviewed": 0}
        rows = conn.execute(
            f"SELECT status, COUNT(*) FROM stig_findings{where} GROUP BY status",  # nosec B608 -- table/column names are internal constants, not user input
            params,
        ).fetchall()
        for st, cnt in rows:
            by_status[st] = cnt

        # Coverage: NotAFinding / (total - Not_Applicable) * 100
        applicable = total - by_status.get("Not_Applicable", 0)
        coverage = 0.0
        if applicable > 0:
            coverage = round(by_status.get("NotAFinding", 0) / applicable * 100, 1)

        # Benchmarks loaded (distinct stig_id)
        row = conn.execute(
            f"SELECT COUNT(DISTINCT stig_id) FROM stig_findings{where}",
            params,  # nosec B608 -- table/column names are internal constants, not user input
        ).fetchone()
        benchmarks_loaded = row[0]

        # Targets assessed (distinct target_type)
        row = conn.execute(
            f"SELECT COUNT(DISTINCT target_type) FROM stig_findings{where}",
            params,  # nosec B608 -- table/column names are internal constants, not user input
        ).fetchone()
        targets_assessed = row[0]

        # CAT1 Open
        cat1_where = " WHERE severity = 'CAT1' AND status = 'Open'"
        if project_id:
            cat1_where += f" AND project_id = {ph}"
        row = conn.execute(
            f"SELECT COUNT(*) FROM stig_findings{cat1_where}",  # nosec B608 -- table/column names are internal constants, not user input
            [project_id] if project_id else [],
        ).fetchone()
        cat1_open = row[0]

        return jsonify(
            {
                "total": total,
                "by_severity": by_severity,
                "by_status": by_status,
                "coverage": coverage,
                "benchmarks_loaded": benchmarks_loaded,
                "targets_assessed": targets_assessed,
                "cat1_open": cat1_open,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@stig_manager_api.route("/benchmarks", methods=["GET"])
def benchmarks():
    """GET /api/stig-manager/benchmarks — List distinct STIG benchmarks."""
    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        if not _table_exists(conn, "stig_findings"):
            return jsonify({"benchmarks": []})

        project_id = request.args.get("project_id")
        where = ""
        params = []
        if project_id:
            where = f" WHERE project_id = {ph}"
            params = [project_id]

        rows = conn.execute(
            f"""SELECT stig_id,
                       COUNT(*) AS total,
                       SUM(CASE WHEN severity = 'CAT1' THEN 1 ELSE 0 END) AS cat1,
                       SUM(CASE WHEN severity = 'CAT2' THEN 1 ELSE 0 END) AS cat2,
                       SUM(CASE WHEN severity = 'CAT3' THEN 1 ELSE 0 END) AS cat3,
                       SUM(CASE WHEN status = 'Open' THEN 1 ELSE 0 END) AS open_count,
                       SUM(CASE WHEN status = 'NotAFinding' THEN 1 ELSE 0 END) AS naf,
                       SUM(CASE WHEN status = 'Not_Applicable' THEN 1 ELSE 0 END) AS na,
                       SUM(CASE WHEN status = 'Not_Reviewed' THEN 1 ELSE 0 END) AS nr
                FROM stig_findings{where}
                GROUP BY stig_id
                ORDER BY stig_id""",  # nosec B608 -- table/column names are internal constants, not user input
            params,
        ).fetchall()

        benchmarks_list = []
        for r in rows:
            applicable = r["total"] - r["na"]
            coverage = round(r["naf"] / applicable * 100, 1) if applicable > 0 else 0.0
            benchmarks_list.append(
                {
                    "stig_id": r["stig_id"],
                    "total": r["total"],
                    "cat1": r["cat1"],
                    "cat2": r["cat2"],
                    "cat3": r["cat3"],
                    "open": r["open_count"],
                    "not_a_finding": r["naf"],
                    "not_applicable": r["na"],
                    "not_reviewed": r["nr"],
                    "coverage": coverage,
                }
            )

        return jsonify({"benchmarks": benchmarks_list})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@stig_manager_api.route("/findings", methods=["GET"])
def findings():
    """GET /api/stig-manager/findings — Paginated findings list."""
    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        if not _table_exists(conn, "stig_findings"):
            return jsonify({"findings": [], "total": 0, "page": 1, "per_page": 25})

        project_id = request.args.get("project_id")
        stig_id = request.args.get("stig_id")
        severity = request.args.get("severity")
        status = request.args.get("status")
        target_type = request.args.get("target_type")
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 25))))

        conditions = []
        params = []
        if project_id:
            conditions.append(f"project_id = {ph}")
            params.append(project_id)
        if stig_id:
            conditions.append(f"stig_id = {ph}")
            params.append(stig_id)
        if severity:
            conditions.append(f"severity = {ph}")
            params.append(severity)
        if status:
            conditions.append(f"status = {ph}")
            params.append(status)
        if target_type:
            conditions.append(f"target_type = {ph}")
            params.append(target_type)

        where = ""
        if conditions:
            where = " WHERE " + " AND ".join(conditions)

        # Total count
        row = conn.execute(
            f"SELECT COUNT(*) FROM stig_findings{where}",
            params,  # nosec B608 -- table/column names are internal constants, not user input
        ).fetchone()
        total = row[0]

        # Paginated results
        offset = (page - 1) * per_page
        rows = conn.execute(
            f"""SELECT id, project_id, stig_id, finding_id, rule_id, severity,
                       title, status, target_type, assessed_by, assessed_at,
                       created_at, updated_at
                FROM stig_findings{where}
                ORDER BY
                    CASE severity WHEN 'CAT1' THEN 1 WHEN 'CAT2' THEN 2 ELSE 3 END,
                    created_at DESC
                LIMIT %s OFFSET %s""",  # nosec B608 -- table/column names are internal constants, not user input
            params + [per_page, offset],
        ).fetchall()

        return jsonify(
            {
                "findings": [dict(r) for r in rows],
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@stig_manager_api.route("/findings/<finding_id>", methods=["GET"])
def finding_detail(finding_id):
    """GET /api/stig-manager/findings/<finding_id> — Full finding detail."""
    conn = _get_db()
    try:
        if not _table_exists(conn, "stig_findings"):
            return jsonify({"error": "Table not found"}), 404

        row = conn.execute(
            "SELECT * FROM stig_findings WHERE id = %s",
            (finding_id,),
        ).fetchone()

        if not row:
            return jsonify({"error": "Finding not found"}), 404

        return jsonify({"finding": dict(row)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@stig_manager_api.route("/coverage", methods=["GET"])
def coverage():
    """GET /api/stig-manager/coverage — Coverage heatmap data by target_type and severity."""
    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        if not _table_exists(conn, "stig_findings"):
            return jsonify({"coverage": [], "targets": [], "severities": ["CAT1", "CAT2", "CAT3"]})

        project_id = request.args.get("project_id")
        where = ""
        params = []
        if project_id:
            where = f" WHERE project_id = {ph}"
            params = [project_id]

        rows = conn.execute(
            f"""SELECT target_type, severity,
                       COUNT(*) AS total,
                       SUM(CASE WHEN status = 'NotAFinding' THEN 1 ELSE 0 END) AS naf,
                       SUM(CASE WHEN status = 'Not_Applicable' THEN 1 ELSE 0 END) AS na
                FROM stig_findings{where}
                GROUP BY target_type, severity
                ORDER BY target_type, severity""",  # nosec B608 -- table/column names are internal constants, not user input
            params,
        ).fetchall()

        grid = {}
        targets = set()
        for r in rows:
            tt = r["target_type"] or "Unknown"
            targets.add(tt)
            applicable = r["total"] - r["na"]
            cov = round(r["naf"] / applicable * 100, 1) if applicable > 0 else 0.0
            if tt not in grid:
                grid[tt] = {}
            grid[tt][r["severity"]] = {
                "total": r["total"],
                "not_a_finding": r["naf"],
                "not_applicable": r["na"],
                "coverage": cov,
            }

        coverage_list = []
        for tt in sorted(targets):
            row_data = {"target_type": tt}
            for sev in ["CAT1", "CAT2", "CAT3"]:
                row_data[sev] = grid.get(tt, {}).get(
                    sev, {"total": 0, "not_a_finding": 0, "not_applicable": 0, "coverage": 0.0}
                )
            coverage_list.append(row_data)

        return jsonify(
            {
                "coverage": coverage_list,
                "targets": sorted(targets),
                "severities": ["CAT1", "CAT2", "CAT3"],
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@stig_manager_api.route("/cat1", methods=["GET"])
def cat1():
    """GET /api/stig-manager/cat1 — Open CAT1 findings (ATO blockers)."""
    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        if not _table_exists(conn, "stig_findings"):
            return jsonify({"findings": [], "count": 0})

        project_id = request.args.get("project_id")
        where = " WHERE severity = 'CAT1' AND status = 'Open'"
        params = []
        if project_id:
            where += f" AND project_id = {ph}"
            params.append(project_id)

        rows = conn.execute(
            f"""SELECT id, project_id, stig_id, finding_id, rule_id, severity,
                       title, description, status, target_type, assessed_by,
                       assessed_at, created_at
                FROM stig_findings{where}
                ORDER BY created_at DESC""",  # nosec B608 -- table/column names are internal constants, not user input
            params,
        ).fetchall()

        return jsonify(
            {
                "findings": [dict(r) for r in rows],
                "count": len(rows),
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@stig_manager_api.route("/assess", methods=["POST"])
@require_role(*COMPLIANCE_WRITE_ROLES)
def assess():
    """POST /api/stig-manager/assess — Update finding status."""
    conn = _get_db()
    try:
        if not _table_exists(conn, "stig_findings"):
            return jsonify({"error": "Table not found"}), 404

        data = request.get_json()
        if not data:
            return jsonify({"error": "JSON body required"}), 400

        finding_id = data.get("finding_id")
        new_status = data.get("status")
        comments = data.get("comments", "")
        # nav-comp-06: attribution is the authenticated user, not the body.
        # Any body-supplied ``assessed_by`` is intentionally ignored.
        assessed_by = _current_actor()

        if not finding_id or not new_status:
            return jsonify({"error": "finding_id and status are required"}), 400

        valid_statuses = ("Open", "NotAFinding", "Not_Applicable", "Not_Reviewed")
        if new_status not in valid_statuses:
            return jsonify({"error": f"Invalid status. Must be one of: {valid_statuses}"}), 400

        # Verify finding exists
        row = conn.execute("SELECT id FROM stig_findings WHERE id = %s", (finding_id,)).fetchone()
        if not row:
            return jsonify({"error": "Finding not found"}), 404

        conn.execute(
            """UPDATE stig_findings
               SET status = %s, comments = %s, assessed_by = %s,
                   assessed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
               WHERE id = %s""",
            (new_status, comments, assessed_by, finding_id),
        )
        conn.commit()

        return jsonify({"ok": True, "finding_id": finding_id, "status": new_status})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()
