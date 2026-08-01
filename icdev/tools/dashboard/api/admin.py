# [TEMPLATE: CUI // SP-CTI]
"""
Admin API blueprint (Phase 30 — D169-D172).

Provides user CRUD, dashboard API key management, and auth log queries.
All endpoints require 'admin' role.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from tools.db.storage import get_connection, sql_placeholder

from flask import Blueprint, jsonify, render_template, request

from tools.dashboard.auth import (
    create_api_key_for_user,
    create_user,
    list_api_keys_for_user,
    list_users,
    reactivate_user,
    require_role,
    revoke_api_key,
    suspend_user,
)
from tools.dashboard.config import DB_PATH
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.dashboard.api.admin")

admin_api = Blueprint("admin_api", __name__, url_prefix="/admin")

# ---------------------------------------------------------------------------
# Admin creation anomaly detection (aiify-rm-a3344-phase-49:
# hardcoded_threshold -> anomaly_detection).
#
# The external AI-ify scan flagged paperless-ngx
# src/documents/management/commands/manage_superuser.py — a Django management
# command that creates/updates a superuser account using hardcoded validation
# rules (fixed username patterns, static privilege checks).  A fixed ruleset
# is brittle: it cannot adapt to context (time of day, volume of recent
# escalations, existing privilege density) and silently passes suspicious
# creations that a fixed rule never anticipated.
#
# The ICDEV analog lands here, applied to the admin user creation endpoint.
# Instead of a single hardcoded guard, we detect a family of contextual
# anomaly signals on admin-role assignments:
#
#   admin_count_high   – total active admins already exceeds the soft cap;
#                        legitimate orgs rarely need more than _ADMIN_MAX_TOTAL
#                        admin-role accounts.
#   admin_burst        – more than _ADMIN_BURST_MAX admin accounts were created
#                        within the last _ADMIN_BURST_WINDOW_SECONDS; a rapid
#                        burst suggests an automated escalation or insider threat.
#   off_hours          – the creation falls outside normal operating hours
#                        (UTC); out-of-band admin provisioning is a common
#                        attack pattern.
#   self_promotion     – the requesting user is granting admin to their own
#                        email; legitimate self-promotion is rare and warrants
#                        HITL review.
#
# Signals are ADVISORY ONLY — they are returned in the response as
# `anomaly_signal` but never block the creation.  Callers (SOC dashboards,
# audit ingesters) should treat a non-null signal as a HITL review flag.
# ---------------------------------------------------------------------------

# Configurable thresholds — never hardcoded in logic below.
_ADMIN_MAX_TOTAL = 10          # soft cap on total active admins
_ADMIN_BURST_WINDOW_SECONDS = 300   # look-back window for burst detection
_ADMIN_BURST_MAX = 3           # max new admins in window before flagging
_OFF_HOURS_START_UTC = 20      # inclusive hour (UTC) when off-hours begin
_OFF_HOURS_END_UTC = 7         # exclusive hour (UTC) when off-hours end


def _detect_admin_creation_anomaly(
    new_email: str,
    requesting_email: str | None,
    db_path: str,
) -> str | None:
    """Return the first anomaly signal for a new admin-role assignment, or None.

    Checks (in priority order): admin_count_high, admin_burst, off_hours,
    self_promotion.  Returns the signal name as a string, or None when no
    anomaly is detected.  Result is advisory — callers MUST NOT block on it.
    """
    try:
        conn = get_connection(db_path=db_path)
        try:
            # 1. Total active admin count
            rows = conn.execute(
                "SELECT COUNT(*) FROM dashboard_users WHERE role = %s AND status = 'active'",
                ("admin",),
            ).fetchone()
            admin_count = rows[0] if rows else 0
            if admin_count >= _ADMIN_MAX_TOTAL:
                return "admin_count_high"

            # 2. Burst: recent admin creations in look-back window.
            # Compute the cutoff in Python for cross-backend compat: the
            # SQLite-only datetime(ts, modifier) form raises on PostgreSQL
            # (the primary backend), which previously disabled this control
            # silently. Pass an ISO cutoff as a bound parameter instead.
            cutoff = (
                datetime.now(timezone.utc)
                - timedelta(seconds=_ADMIN_BURST_WINDOW_SECONDS)
            ).isoformat()
            burst_rows = conn.execute(
                """
                SELECT COUNT(*) FROM dashboard_users
                WHERE role = 'admin'
                  AND created_at >= %s
                """,
                (cutoff,),
            ).fetchone()
            recent_admins = burst_rows[0] if burst_rows else 0
            if recent_admins >= _ADMIN_BURST_MAX:
                return "admin_burst"

            # 3. Off-hours: UTC hour outside normal operating window
            utc_hour = datetime.now(timezone.utc).hour
            if _OFF_HOURS_START_UTC <= utc_hour or utc_hour < _OFF_HOURS_END_UTC:
                return "off_hours"

        finally:
            conn.close()
    except Exception:
        # Advisory control: never let anomaly detection crash the creation
        # path, but log VISIBLY so a query failure (e.g. a backend dialect
        # mismatch) does not silently disable burst/anomaly detection in
        # production.
        logger.exception(
            "Admin creation anomaly detection failed; degrading to no signal "
            "(advisory control, creation not blocked)"
        )
        return None

    # 4. Self-promotion: requester granting admin to themselves
    if requesting_email and new_email.lower() == requesting_email.lower():
        return "self_promotion"

    return None


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------


@admin_api.route("/users")
@require_role("admin")
def users_page():
    """Admin user management page."""
    users = list_users()
    return render_template("admin/users.html", users=users)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@admin_api.route("/api/users", methods=["GET"])
@require_role("admin")
def api_list_users():
    """List all dashboard users."""
    status_filter = request.args.get("status")
    users = list_users(status=status_filter)
    return jsonify({"users": users, "total": len(users)})


@admin_api.route("/api/users", methods=["POST"])
@require_role("admin")
def api_create_user():
    """Create a new dashboard user."""
    from flask import g

    data = request.get_json(force=True)
    email = data.get("email", "").strip()
    display_name = data.get("display_name", "").strip()
    role = data.get("role", "developer")

    if not email or not display_name:
        return jsonify({"error": "email and display_name required"}), 400

    if role not in ("admin", "pm", "developer", "isso", "co"):
        return jsonify({"error": f"Invalid role: {role}"}), 400

    admin_user = getattr(g, "current_user", {})

    # Anomaly detection for admin-role assignments (advisory, never blocking).
    anomaly_signal: str | None = None
    if role == "admin":
        anomaly_signal = _detect_admin_creation_anomaly(
            new_email=email,
            requesting_email=admin_user.get("email"),
            db_path=str(DB_PATH),
        )

    try:
        user = create_user(email, display_name, role, created_by=admin_user.get("id"))
    except sqlite3.IntegrityError:
        return jsonify({"error": f"User with email {email} already exists"}), 409

    response: dict = {"user": user}
    if anomaly_signal:
        response["anomaly_signal"] = anomaly_signal
    return jsonify(response), 201


@admin_api.route("/api/users/<user_id>/keys", methods=["GET"])
@require_role("admin")
def api_list_keys(user_id):
    """List API keys for a user."""
    keys = list_api_keys_for_user(user_id)
    return jsonify({"keys": keys})


@admin_api.route("/api/users/<user_id>/keys", methods=["POST"])
@require_role("admin")
def api_create_key(user_id):
    """Generate a new API key for a user."""
    from flask import g

    data = request.get_json(force=True) if request.is_json else {}
    label = data.get("label", "")
    expires_at = data.get("expires_at")

    admin_user = getattr(g, "current_user", {})
    key_info = create_api_key_for_user(user_id, label=label, created_by=admin_user.get("id"), expires_at=expires_at)
    return jsonify(
        {
            "key_id": key_info["key_id"],
            "raw_key": key_info["raw_key"],
            "prefix": key_info["prefix"],
            "message": "Save this key now — it will not be shown again.",
        }
    ), 201


@admin_api.route("/api/keys/<key_id>/revoke", methods=["POST"])
@require_role("admin")
def api_revoke_key(key_id):
    """Revoke an API key."""
    from flask import g

    admin_user = getattr(g, "current_user", {})
    revoke_api_key(key_id, revoked_by=admin_user.get("id"))
    return jsonify({"status": "revoked", "key_id": key_id})


@admin_api.route("/api/users/<user_id>/suspend", methods=["POST"])
@require_role("admin")
def api_suspend_user(user_id):
    """Suspend a user."""
    from flask import g

    admin_user = getattr(g, "current_user", {})
    suspend_user(user_id, suspended_by=admin_user.get("id"))
    return jsonify({"status": "suspended", "user_id": user_id})


@admin_api.route("/api/users/<user_id>/reactivate", methods=["POST"])
@require_role("admin")
def api_reactivate_user(user_id):
    """Reactivate a suspended user."""
    from flask import g

    admin_user = getattr(g, "current_user", {})
    reactivate_user(user_id, reactivated_by=admin_user.get("id"))
    return jsonify({"status": "active", "user_id": user_id})


@admin_api.route("/api/auth-log", methods=["GET"])
@require_role("admin")
def api_auth_log():
    """Query auth event log."""
    limit = int(request.args.get("limit", "100"))
    user_id = request.args.get("user_id")
    event_type = request.args.get("event_type")

    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        query = "SELECT * FROM dashboard_auth_log WHERE 1=1"
        params = []
        if user_id:
            query += f" AND user_id = {ph}"
            params.append(user_id)
        if event_type:
            query += f" AND event_type = {ph}"
            params.append(event_type)
        query += f" ORDER BY created_at DESC LIMIT {ph}"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return jsonify({"events": [dict(r) for r in rows], "total": len(rows)})
    finally:
        conn.close()
