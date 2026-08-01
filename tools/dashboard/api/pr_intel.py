#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: PR Intelligence / Compliance Drift."""

import json
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists  # noqa: E402
from tools.dashboard.auth import require_role  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

# Triggering a PR compliance-drift analysis is a state-changing operation —
# restrict to security/compliance roles, mirroring GOVCON_WRITE_ROLES.
COMPLIANCE_WRITE_ROLES = ("admin", "isso", "ciso")

pr_intel_api = Blueprint("pr_intel_api", __name__, url_prefix="/api/pr-intel")


def _is_pg(conn):
    """Return True if the connection is backed by PostgreSQL."""
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _table_exists(conn, name):
    """Check if a table exists (works for both SQLite and PostgreSQL)."""
    return table_exists(conn, name)


def _safe_json_loads(value, default=None):
    """Parse a JSON string, returning *default* on failure."""
    if default is None:
        default = []
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


# ── GET /api/pr-intel/stats ─────────────────────────────────────────


@pr_intel_api.route("/stats", methods=["GET"])
def stats():
    """Aggregate PR intelligence statistics."""
    conn = _get_db()
    try:
        if not _table_exists(conn, "pr_intelligence_reports"):
            return jsonify(
                {
                    "total": 0,
                    "by_status": {},
                    "pass_rate": 0.0,
                    "total_security_findings": 0,
                    "total_compliance_impacts": 0,
                    "recent_trend": [],
                }
            )

        pg = _is_pg(conn)

        # Totals by status
        rows = conn.execute(
            "SELECT overall_status, COUNT(*) AS cnt FROM pr_intelligence_reports GROUP BY overall_status"
        ).fetchall()
        by_status = {}
        for r in rows:
            key = r["overall_status"] if isinstance(r, dict) else r[0]
            val = r["cnt"] if isinstance(r, dict) else r[1]
            by_status[key] = val
        total = sum(by_status.values())
        pass_rate = (by_status.get("pass", 0) / total * 100) if total else 0.0

        # Security findings + compliance impacts counts
        all_rows = conn.execute("SELECT security_findings, compliance_impacts FROM pr_intelligence_reports").fetchall()
        total_sec = 0
        total_comp = 0
        for row in all_rows:
            sf = row["security_findings"] if isinstance(row, dict) else row[0]
            ci = row["compliance_impacts"] if isinstance(row, dict) else row[1]
            total_sec += len(_safe_json_loads(sf))
            total_comp += len(_safe_json_loads(ci))

        # Recent 30-day trend
        if pg:
            date_expr = "CAST(created_at AS DATE)"
            since_expr = "NOW() - INTERVAL '30 days'"
            where_since = f"WHERE created_at::timestamp >= {since_expr}"
        else:
            date_expr = "DATE(created_at)"
            since_expr = "datetime('now', '-30 days')"
            where_since = f"WHERE created_at >= {since_expr}"
        trend = conn.execute(
            f"SELECT {date_expr} AS day, COUNT(*) AS cnt "  # nosec B608 -- table/column names are internal constants, not user input
            f"FROM pr_intelligence_reports "
            f"{where_since} "
            f"GROUP BY day ORDER BY day"
        ).fetchall()
        recent_trend = []
        for r in trend:
            day = r["day"] if isinstance(r, dict) else r[0]
            cnt = r["cnt"] if isinstance(r, dict) else r[1]
            recent_trend.append({"date": str(day), "count": cnt})

        return jsonify(
            {
                "total": total,
                "by_status": by_status,
                "pass_rate": round(pass_rate, 2),
                "total_security_findings": total_sec,
                "total_compliance_impacts": total_comp,
                "recent_trend": recent_trend,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── GET /api/pr-intel/reports ───────────────────────────────────────


@pr_intel_api.route("/reports", methods=["GET"])
def list_reports():
    """List PR intelligence reports with optional filters and pagination."""
    conn = _get_db()
    try:
        if not _table_exists(conn, "pr_intelligence_reports"):
            return jsonify({"reports": [], "total": 0, "page": 1, "per_page": 50})

        project_id = request.args.get("project_id")
        status = request.args.get("status")
        page = max(int(request.args.get("page", 1)), 1)
        per_page = min(max(int(request.args.get("per_page", 50)), 1), 200)

        conditions = []
        params = []
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if status:
            conditions.append("overall_status = ?")
            params.append(status)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        count_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM pr_intelligence_reports{where}",
            params,  # nosec B608 -- table/column names are internal constants, not user input
        ).fetchone()
        total = count_row["cnt"] if isinstance(count_row, dict) else count_row[0]

        offset = (page - 1) * per_page
        rows = conn.execute(
            f"SELECT id, project_id, pr_reference, diff_summary, files_changed, "  # nosec B608 -- table/column names are internal constants, not user input
            f"security_findings, compliance_impacts, overall_status, "
            f"classification, created_at "
            f"FROM pr_intelligence_reports{where} "
            f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [per_page, offset],
        ).fetchall()

        columns = [
            "id",
            "project_id",
            "pr_reference",
            "diff_summary",
            "files_changed",
            "security_findings",
            "compliance_impacts",
            "overall_status",
            "classification",
            "created_at",
        ]
        reports = []
        for r in rows:
            if isinstance(r, dict):
                d = r
            else:
                d = dict(zip(columns, r))
            sec = _safe_json_loads(d["security_findings"])
            comp = _safe_json_loads(d["compliance_impacts"])
            reports.append(
                {
                    "id": d["id"],
                    "project_id": d["project_id"],
                    "pr_reference": d["pr_reference"],
                    "diff_summary": d["diff_summary"],
                    "files_changed": d["files_changed"],
                    "security_findings_count": len(sec),
                    "compliance_impacts_count": len(comp),
                    "overall_status": d["overall_status"],
                    "classification": d["classification"],
                    "created_at": str(d["created_at"]),
                }
            )

        return jsonify(
            {
                "reports": reports,
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        )
    finally:
        conn.close()


# ── GET /api/pr-intel/reports/<report_id> ───────────────────────────


@pr_intel_api.route("/reports/<report_id>", methods=["GET"])
def report_detail(report_id):
    """Full detail for a single PR intelligence report."""
    conn = _get_db()
    try:
        if not _table_exists(conn, "pr_intelligence_reports"):
            return jsonify({"error": "table not found"}), 404

        row = conn.execute(
            "SELECT id, project_id, pr_reference, diff_summary, files_changed, "
            "security_findings, compliance_impacts, code_quality_delta, "
            "overall_status, report_json, classification, created_at "
            "FROM pr_intelligence_reports WHERE id = %s",
            (report_id,),
        ).fetchone()

        if not row:
            return jsonify({"error": "report not found"}), 404

        detail_cols = [
            "id",
            "project_id",
            "pr_reference",
            "diff_summary",
            "files_changed",
            "security_findings",
            "compliance_impacts",
            "code_quality_delta",
            "overall_status",
            "report_json",
            "classification",
            "created_at",
        ]
        d = row if isinstance(row, dict) else dict(zip(detail_cols, row))

        return jsonify(
            {
                "id": d["id"],
                "project_id": d["project_id"],
                "pr_reference": d["pr_reference"],
                "diff_summary": d["diff_summary"],
                "files_changed": d["files_changed"],
                "security_findings": _safe_json_loads(d["security_findings"]),
                "compliance_impacts": _safe_json_loads(d["compliance_impacts"]),
                "code_quality_delta": _safe_json_loads(d["code_quality_delta"], default={}),
                "overall_status": d["overall_status"],
                "report_json": _safe_json_loads(d["report_json"], default={}),
                "classification": d["classification"],
                "created_at": str(d["created_at"]),
            }
        )
    finally:
        conn.close()


# ── GET /api/pr-intel/drift ─────────────────────────────────────────


@pr_intel_api.route("/drift", methods=["GET"])
def compliance_drift():
    """Compliance drift analysis — top drifting controls by frequency."""
    conn = _get_db()
    try:
        if not _table_exists(conn, "pr_intelligence_reports"):
            return jsonify({"controls": [], "total_impacts": 0})

        rows = conn.execute("SELECT compliance_impacts FROM pr_intelligence_reports").fetchall()

        control_freq = {}
        total_impacts = 0
        for row in rows:
            raw = row["compliance_impacts"] if isinstance(row, dict) else row[0]
            impacts = _safe_json_loads(raw)
            for impact in impacts:
                total_impacts += 1
                # Extract control ID — accept dict with 'control_id'/'control'
                # key, or plain string
                if isinstance(impact, dict):
                    ctrl = impact.get("control_id") or impact.get("control") or "unknown"
                elif isinstance(impact, str):
                    ctrl = impact
                else:
                    continue
                # Derive control family from prefix (e.g. "AC-2" -> "AC")
                family = ctrl.split("-")[0] if "-" in ctrl else ctrl
                control_freq.setdefault(family, {"family": family, "controls": {}})
                control_freq[family]["controls"].setdefault(ctrl, 0)
                control_freq[family]["controls"][ctrl] += 1

        # Flatten and sort by total family frequency descending
        controls = []
        for fam in control_freq.values():
            fam_total = sum(fam["controls"].values())
            controls.append(
                {
                    "family": fam["family"],
                    "total": fam_total,
                    "controls": [
                        {"id": cid, "count": cnt}
                        for cid, cnt in sorted(fam["controls"].items(), key=lambda x: x[1], reverse=True)
                    ],
                }
            )
        controls.sort(key=lambda x: x["total"], reverse=True)

        return jsonify(
            {
                "controls": controls,
                "total_impacts": total_impacts,
            }
        )
    finally:
        conn.close()


# ── POST /api/pr-intel/analyze ──────────────────────────────────────


@pr_intel_api.route("/analyze", methods=["POST"])
@require_role(*COMPLIANCE_WRITE_ROLES)
def analyze():
    """Trigger PR analysis for a given project and PR reference."""
    data = request.get_json(force=True, silent=True) or {}
    project_id = data.get("project_id")
    pr_reference = data.get("pr_reference")

    if not project_id or not pr_reference:
        return jsonify({"error": "project_id and pr_reference required"}), 400

    try:
        from tools.security.pr_intelligence import analyze_pr

        result = analyze_pr(project_id=project_id, pr_reference=pr_reference)
        return jsonify(result)
    except ImportError:
        return jsonify({"error": "pr_intelligence module not available"}), 501
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
