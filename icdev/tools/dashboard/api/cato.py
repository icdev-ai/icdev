#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Continuous ATO (cATO)."""

import sys
from pathlib import Path
from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists  # noqa: E402
from tools.dashboard.auth import require_role  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

# Evidence-refresh mutation is restricted to security/compliance roles,
# mirroring GOVCON_WRITE_ROLES in api/govcon.py.
COMPLIANCE_WRITE_ROLES = ("admin", "isso", "ciso")

cato_api = Blueprint("cato_api", __name__, url_prefix="/api/cato")


def _is_pg(conn):
    """Check if connection is PostgreSQL."""
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


# ---------------------------------------------------------------------------
# GET /api/cato/health — Real-time ATO health score
# ---------------------------------------------------------------------------
@cato_api.route("/health", methods=["GET"])
def cato_health():
    """Calculate overall ATO health score from evidence, controls, POAMs, certs."""
    conn = _get_db()
    try:
        project_id = request.args.get("project_id")

        # --- Evidence freshness score ---
        evidence_score = 0.0
        if _table_exists(conn, "cato_evidence"):
            where = " WHERE project_id = ?" if project_id else ""
            params = (project_id,) if project_id else ()
            total = _count(
                conn,
                f"SELECT COUNT(*) AS cnt FROM cato_evidence{where}",
                params,  # nosec B608 -- table/column names are internal constants, not user input
            )
            if total > 0:
                current = _count(
                    conn,
                    f"SELECT COUNT(*) AS cnt FROM cato_evidence{where}"  # nosec B608 -- table/column names are internal constants, not user input
                    + (" AND" if project_id else " WHERE")
                    + " status = 'current'",
                    params,
                )
                evidence_score = (current / total) * 100

        # --- Control implementation score ---
        control_score = 0.0
        if _table_exists(conn, "project_controls"):
            where = " WHERE project_id = ?" if project_id else ""
            params = (project_id,) if project_id else ()
            total = _count(
                conn,
                f"SELECT COUNT(*) AS cnt FROM project_controls{where}",
                params,  # nosec B608 -- table/column names are internal constants, not user input
            )
            if total > 0:
                implemented = _count(
                    conn,
                    f"SELECT COUNT(*) AS cnt FROM project_controls{where}"  # nosec B608 -- table/column names are internal constants, not user input
                    + (" AND" if project_id else " WHERE")
                    + " implementation_status IN ('implemented', 'not_applicable')",
                    params,
                )
                control_score = (implemented / total) * 100

        # --- POAM resolution score (% closed) ---
        # nav-comp-06: absent POAM data is "not assessed", NOT a perfect 100. A
        # 100.0 default silently inflated overall health at 25% weight, making a
        # system with no POAM tracking at all look healthier than one that tracks
        # and has open items. Exclude an unassessed POAM from the weighted
        # composite and re-normalize the remaining weights.
        poam_score = 0.0
        poam_assessed = False
        if _table_exists(conn, "poam_items"):
            where = " WHERE project_id = ?" if project_id else ""
            params = (project_id,) if project_id else ()
            total = _count(
                conn,
                f"SELECT COUNT(*) AS cnt FROM poam_items{where}",
                params,  # nosec B608 -- table/column names are internal constants, not user input
            )
            if total > 0:
                closed = _count(
                    conn,
                    f"SELECT COUNT(*) AS cnt FROM poam_items{where}"  # nosec B608 -- table/column names are internal constants, not user input
                    + (" AND" if project_id else " WHERE")
                    + " status IN ('completed', 'accepted_risk')",
                    params,
                )
                poam_score = (closed / total) * 100
                poam_assessed = True

        # --- Certification status score ---
        cert_score = 0.0
        if _table_exists(conn, "cssp_certifications"):
            where = " WHERE project_id = ?" if project_id else ""
            params = (project_id,) if project_id else ()
            total = _count(
                conn,
                f"SELECT COUNT(*) AS cnt FROM cssp_certifications{where}",
                params,  # nosec B608 -- table/column names are internal constants, not user input
            )
            if total > 0:
                certified = _count(
                    conn,
                    f"SELECT COUNT(*) AS cnt FROM cssp_certifications{where}"  # nosec B608 -- table/column names are internal constants, not user input
                    + (" AND" if project_id else " WHERE")
                    + " status = 'certified'",
                    params,
                )
                cert_score = (certified / total) * 100

        # Weighted composite over ASSESSED components only, with weights
        # re-normalized so an unassessed component neither inflates nor deflates
        # the score (nav-comp-06). POAM is excluded when its data is absent.
        parts = [
            (0.30, evidence_score),
            (0.30, control_score),
            (0.15, cert_score),
        ]
        if poam_assessed:
            parts.append((0.25, poam_score))
        weight_sum = sum(w for w, _ in parts)
        overall = round(sum(w * s for w, s in parts) / weight_sum, 1) if weight_sum else 0.0

        if overall >= 80:
            category = "green"
        elif overall >= 50:
            category = "yellow"
        else:
            category = "red"

        return jsonify(
            {
                "health_score": overall,
                "category": category,
                "components": {
                    "evidence_freshness": round(evidence_score, 1),
                    "control_implementation": round(control_score, 1),
                    "poam_resolution": round(poam_score, 1) if poam_assessed else None,
                    "certification_status": round(cert_score, 1),
                },
                # Explicit assessment status so consumers can see that an absent
                # POAM is excluded from health, not scored as perfect.
                "poam": "assessed" if poam_assessed else "not_assessed",
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /api/cato/evidence — Evidence freshness dashboard
# ---------------------------------------------------------------------------
@cato_api.route("/evidence", methods=["GET"])
def cato_evidence():
    """List cato_evidence with optional filters and pagination."""
    conn = _get_db()
    try:
        if not _table_exists(conn, "cato_evidence"):
            return jsonify({"evidence": [], "total": 0, "page": 1, "per_page": 50})

        project_id = request.args.get("project_id")
        status = request.args.get("status")
        evidence_type = request.args.get("evidence_type")
        page = max(int(request.args.get("page", 1)), 1)
        per_page = min(int(request.args.get("per_page", 50)), 200)

        query = "SELECT * FROM cato_evidence WHERE 1=1"
        count_query = "SELECT COUNT(*) AS cnt FROM cato_evidence WHERE 1=1"
        params = []

        if project_id:
            query += " AND project_id = ?"
            count_query += " AND project_id = ?"
            params.append(project_id)
        if status:
            query += " AND status = ?"
            count_query += " AND status = ?"
            params.append(status)
        if evidence_type:
            query += " AND evidence_type = ?"
            count_query += " AND evidence_type = ?"
            params.append(evidence_type)

        total = _count(conn, count_query, params)

        query += " ORDER BY collected_at DESC LIMIT ? OFFSET ?"
        params.extend([per_page, (page - 1) * per_page])

        rows = conn.execute(query, params).fetchall()
        return jsonify(
            {
                "evidence": [dict(r) for r in rows],
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /api/cato/controls — Control implementation status by family
# ---------------------------------------------------------------------------
@cato_api.route("/controls", methods=["GET"])
def cato_controls():
    """Control implementation grouped by NIST family."""
    conn = _get_db()
    try:
        if not _table_exists(conn, "project_controls"):
            return jsonify({"families": []})

        project_id = request.args.get("project_id")

        # Join project_controls with compliance_controls for family info
        # Check table exists AND has rows — empty table causes LEFT JOIN
        # to produce NULL families, dumping everything into "OTHER".
        has_cc = _table_exists(conn, "compliance_controls")
        if has_cc:
            cc_count = _count(conn, "SELECT COUNT(*) AS cnt FROM compliance_controls")
            has_cc = cc_count > 0

        if has_cc:
            query = (
                "SELECT cc.family, cc.title AS family_title, "
                "pc.implementation_status, COUNT(*) AS cnt "
                "FROM project_controls pc "
                "LEFT JOIN compliance_controls cc ON pc.control_id = cc.id "
                "WHERE 1=1"
            )
        else:
            query = (
                "SELECT SUBSTR(pc.control_id, 1, 2) AS family, "
                "'' AS family_title, "
                "pc.implementation_status, COUNT(*) AS cnt "
                "FROM project_controls pc WHERE 1=1"
            )

        params = []
        if project_id:
            query += " AND pc.project_id = ?"
            params.append(project_id)

        if has_cc:
            query += " GROUP BY cc.family, cc.title, pc.implementation_status ORDER BY cc.family"
        else:
            query += " GROUP BY family, pc.implementation_status ORDER BY family"

        rows = conn.execute(query, params).fetchall()

        # Aggregate into family-level summaries
        families = {}
        for row in rows:
            fam = dict(row)
            family_key = fam["family"] or "OTHER"
            if family_key not in families:
                families[family_key] = {
                    "family": family_key,
                    "title": fam.get("family_title", ""),
                    "total": 0,
                    "implemented": 0,
                    "partially_implemented": 0,
                    "planned": 0,
                    "not_applicable": 0,
                    "compensating": 0,
                }
            families[family_key]["total"] += fam["cnt"]
            status_key = fam["implementation_status"]
            if status_key in families[family_key]:
                families[family_key][status_key] += fam["cnt"]

        # Add implementation percentage
        for fam in families.values():
            if fam["total"] > 0:
                fam["implementation_pct"] = round((fam["implemented"] + fam["not_applicable"]) / fam["total"] * 100, 1)
            else:
                fam["implementation_pct"] = 0.0

        return jsonify({"families": list(families.values())})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /api/cato/certifications — ATO certification status
# ---------------------------------------------------------------------------
@cato_api.route("/certifications", methods=["GET"])
def cato_certifications():
    """List ATO certifications with days until expiration."""
    conn = _get_db()
    try:
        if not _table_exists(conn, "cssp_certifications"):
            return jsonify({"certifications": []})

        project_id = request.args.get("project_id")
        if _is_pg(conn):
            days_expr = "CAST(EXTRACT(EPOCH FROM (expiration_date::timestamp - NOW())) / 86400 AS INTEGER)"
        else:
            days_expr = "CAST(julianday(expiration_date) - julianday('now') AS INTEGER)"
        query = (
            f"SELECT *, {days_expr} AS days_remaining "  # nosec B608 -- table/column names are internal constants, not user input
            "FROM cssp_certifications WHERE 1=1"
        )
        params = []
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        query += " ORDER BY expiration_date ASC"

        rows = conn.execute(query, params).fetchall()
        certs = []
        for r in rows:
            cert = dict(r)
            days = cert.get("days_remaining")
            if days is not None and days < 0:
                cert["expiration_warning"] = "expired"
            elif days is not None and days <= 90:
                cert["expiration_warning"] = "expiring_soon"
            else:
                cert["expiration_warning"] = "ok"
            certs.append(cert)

        return jsonify({"certifications": certs})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /api/cato/timeline — Evidence collection over time
# ---------------------------------------------------------------------------
@cato_api.route("/timeline", methods=["GET"])
def cato_timeline():
    """Weekly evidence collection events for charting."""
    conn = _get_db()
    try:
        if not _table_exists(conn, "cato_evidence"):
            return jsonify({"weeks": []})

        project_id = request.args.get("project_id")
        if _is_pg(conn):
            week_expr = "to_char(collected_at::timestamp, 'IYYY-\"W\"IW')"
        else:
            week_expr = "strftime('%Y-W%W', collected_at)"
        query = (
            f"SELECT {week_expr} AS week, "  # nosec B608 -- table/column names are internal constants, not user input
            "COUNT(*) AS count "
            "FROM cato_evidence WHERE collected_at IS NOT NULL"
        )
        params = []
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        query += " GROUP BY week ORDER BY week DESC LIMIT 52"

        rows = conn.execute(query, params).fetchall()
        return jsonify({"weeks": [dict(r) for r in rows]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POST /api/cato/refresh — Trigger evidence refresh
# ---------------------------------------------------------------------------
@cato_api.route("/refresh", methods=["POST"])
@require_role(*COMPLIANCE_WRITE_ROLES)
def cato_refresh():
    """Mark stale evidence as needing refresh for a project."""
    conn = _get_db()
    try:
        data = request.get_json(force=True, silent=True) or {}
        project_id = data.get("project_id")
        if not project_id:
            return jsonify({"error": "project_id is required"}), 400

        if not _table_exists(conn, "cato_evidence"):
            return jsonify({"refreshed": 0, "message": "cato_evidence table not found"})

        # Use ? so _PGCompatConn rewrites to %s on PostgreSQL while SQLite
        # keeps its qmark paramstyle. A raw %s literal raised on the default
        # SQLite backend, so /refresh previously always errored there.
        cursor = conn.execute(
            "UPDATE cato_evidence SET status = 'stale' WHERE project_id = ? AND status = 'expired'",
            (project_id,),
        )
        conn.commit()
        return jsonify(
            {
                "refreshed": cursor.rowcount,
                "project_id": project_id,
                "message": f"Marked {cursor.rowcount} expired evidence items as stale for refresh",
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()
