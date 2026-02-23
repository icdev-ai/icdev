#!/usr/bin/env python3
# CUI // SP-CTI
"""Production Audit API Blueprint — REST endpoints for audit & remediation (D291-D300).

Provides /api/prod-audit/* endpoints for the dashboard production audit page:
audit history, latest results, remediation log, and trigger actions.
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    from tools.compat.db_utils import get_db_connection
except ImportError:
    get_db_connection = None

prod_audit_api = Blueprint("prod_audit_api", __name__, url_prefix="/api/prod-audit")


def _get_db() -> sqlite3.Connection:
    if get_db_connection:
        return get_db_connection(DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ── Audit History ───────────────────────────────────────────────


@prod_audit_api.route("/history", methods=["GET"])
def audit_history():
    """List production audit history."""
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT id, overall_pass, total_checks, passed, failed, warned, skipped, "
            "categories_run, duration_ms, created_at FROM production_audits "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

        total = conn.execute("SELECT COUNT(*) FROM production_audits").fetchone()[0]
        conn.close()

        return jsonify({"audits": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset})
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


@prod_audit_api.route("/latest", methods=["GET"])
def latest_audit():
    """Get most recent production audit result with full detail."""
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM production_audits ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        conn.close()

        if not row:
            return jsonify({"error": "No audits found"}), 404

        result = dict(row)
        # Parse JSON fields for the frontend
        for field in ("blockers", "warnings", "report_json"):
            if result.get(field) and isinstance(result[field], str):
                try:
                    result[field] = json.loads(result[field])
                except (json.JSONDecodeError, TypeError):
                    pass

        return jsonify(result)
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


# ── Remediation Log ─────────────────────────────────────────────


@prod_audit_api.route("/remediation-log", methods=["GET"])
def remediation_log():
    """List remediation audit log entries."""
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))
    check_id = request.args.get("check_id")

    try:
        conn = _get_db()
        where = "WHERE check_id = ?" if check_id else ""
        params = (check_id,) if check_id else ()

        rows = conn.execute(
            f"SELECT id, source_audit_id, check_id, check_name, category, confidence, "
            f"tier, status, fix_strategy, message, duration_ms, "
            f"verification_status, dry_run, created_at "
            f"FROM remediation_audit_log {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + (limit, offset),
        ).fetchall()

        total = conn.execute(
            f"SELECT COUNT(*) FROM remediation_audit_log {where}", params
        ).fetchone()[0]

        conn.close()
        return jsonify({"remediations": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset})
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


# ── Trigger Actions ─────────────────────────────────────────────


@prod_audit_api.route("/run", methods=["POST"])
def run_audit():
    """Trigger a production audit run."""
    data = request.get_json(force=True, silent=True) or {}
    categories = data.get("categories")

    cmd = [sys.executable, str(BASE_DIR / "tools" / "testing" / "production_audit.py"), "--json"]
    if categories:
        cmd.extend(["--category", categories])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            stdin=subprocess.DEVNULL, cwd=str(BASE_DIR),
        )
        try:
            result = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError):
            result = {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}

        return jsonify(result), 200 if proc.returncode == 0 else 200
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Audit timed out (300s limit)"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@prod_audit_api.route("/remediate", methods=["POST"])
def run_remediation():
    """Trigger remediation of audit blockers."""
    data = request.get_json(force=True, silent=True) or {}
    dry_run = data.get("dry_run", False)
    check_id = data.get("check_id")

    cmd = [sys.executable, str(BASE_DIR / "tools" / "testing" / "production_remediate.py"), "--json"]
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd.append("--auto")
    if check_id:
        cmd.extend(["--check-id", check_id])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            stdin=subprocess.DEVNULL, cwd=str(BASE_DIR),
        )
        try:
            result = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError):
            result = {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}

        return jsonify(result)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Remediation timed out (300s limit)"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500
