# CUI // SP-CTI
"""tools/dashboard/api/jise.py — JISE Portal REST API.

Exposes ICDEV™ data feeds for downstream consumption by the
Joint Intelligence Support Element (JISE) portal.

Routes (all under /api/v1/jise):
  GET  /status          Connectivity health check
  GET  /requirements    Requirements feed (title, status, classification)
  GET  /intelligence    Intelligence assessments and findings
  GET  /compliance      Compliance posture summary (controls, POAM counts)

NIST 800-53: SI-12 (Information Management and Retention),
             AC-3 (Access Enforcement), AU-2 (Audit Events)
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from tools.db.storage import get_connection

logger = get_logger("icdev.api.jise")

jise_api = Blueprint("jise_api", __name__, url_prefix="/api/v1/jise")


def _db():
    return get_connection()


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------

@jise_api.route("/status", methods=["GET"])
def status():
    """Connectivity and health check for the JISE portal."""
    try:
        conn = _db()
        try:
            conn.execute("SELECT 1").fetchone()
            db_ok = True
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("JISE status DB probe failed: %s", exc)
        db_ok = False

    return jsonify({
        "service": "ICDEV JISE Feed",
        "status": "healthy" if db_ok else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0",
        "classification": "CUI",
    }), 200 if db_ok else 503


# ---------------------------------------------------------------------------
# GET /requirements
# ---------------------------------------------------------------------------

@jise_api.route("/requirements", methods=["GET"])
def requirements():
    """Return requirements feed for JISE downstream consumption.

    Query params:
      status        filter by status (e.g. open, closed, in_progress)
      limit         max records (default 100, max 500)
    """
    status_filter = request.args.get("status")
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except (TypeError, ValueError):
        limit = 100

    conn = _db()
    try:
        query = (
            "SELECT id, title, description, status, priority, "
            "classification, created_at, updated_at "
            "FROM requirements"
        )
        params: list = []
        if status_filter:
            query += " WHERE status = ?"
            params.append(status_filter)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        try:
            rows = conn.execute(query, params).fetchall()
            items = [dict(r) for r in rows]
        except Exception:
            items = []

        return jsonify({
            "feed": "requirements",
            "total": len(items),
            "limit": limit,
            "classification": "CUI",
            "items": items,
        })
    except Exception as exc:
        logger.error("JISE /requirements error: %s", exc)
        return jsonify({"error": "requirements feed unavailable", "detail": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /intelligence
# ---------------------------------------------------------------------------

@jise_api.route("/intelligence", methods=["GET"])
def intelligence():
    """Return intelligence assessments and security findings for JISE.

    Aggregates from: security_scan_results, compliance_assessments (if present).
    Query params:
      severity  filter by severity (critical, high, medium, low)
      limit     max records (default 50, max 200)
    """
    severity_filter = request.args.get("severity")
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (TypeError, ValueError):
        limit = 50

    conn = _db()
    try:
        # Pull from security_scan_results if available
        query = (
            "SELECT id, scan_type, severity, title, description, "
            "status, created_at "
            "FROM security_scan_results"
        )
        params: list = []
        if severity_filter:
            query += " WHERE severity = ?"
            params.append(severity_filter)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        try:
            rows = conn.execute(query, params).fetchall()
            findings = [dict(r) for r in rows]
        except Exception:
            findings = []

        return jsonify({
            "feed": "intelligence",
            "total": len(findings),
            "limit": limit,
            "classification": "CUI",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "findings": findings,
        })
    except Exception as exc:
        logger.error("JISE /intelligence error: %s", exc)
        return jsonify({"error": "intelligence feed unavailable", "detail": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /compliance
# ---------------------------------------------------------------------------

@jise_api.route("/compliance", methods=["GET"])
def compliance():
    """Return compliance posture summary for JISE downstream consumption."""
    conn = _db()
    try:
        # POAM open items
        try:
            poam_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM poam_items WHERE status = 'open'"
            ).fetchone()
            open_poam = poam_row["cnt"] if poam_row else 0
        except Exception:
            open_poam = None

        # Compliance control counts
        try:
            ctrl_rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM compliance_controls GROUP BY status"
            ).fetchall()
            control_summary = {r["status"]: r["cnt"] for r in ctrl_rows}
        except Exception:
            control_summary = {}

        # Projects count
        try:
            proj_row = conn.execute("SELECT COUNT(*) AS cnt FROM projects").fetchone()
            project_count = proj_row["cnt"] if proj_row else 0
        except Exception:
            project_count = None

        return jsonify({
            "feed": "compliance",
            "classification": "CUI",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "posture": {
                "open_poam_items": open_poam,
                "control_summary": control_summary,
                "project_count": project_count,
            },
        })
    except Exception as exc:
        logger.error("JISE /compliance error: %s", exc)
        return jsonify({"error": "compliance feed unavailable", "detail": str(exc)}), 500
    finally:
        conn.close()
