# CUI // SP-CTI
"""
Admin API blueprint (Phase 30 — D169-D172).

Provides user CRUD, dashboard API key management, and auth log queries.
All endpoints require 'admin' role.
"""

import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, request

from tools.dashboard.auth import (
    create_api_key_for_user,
    create_user,
    list_api_keys_for_user,
    list_users,
    log_auth_event,
    reactivate_user,
    require_role,
    revoke_api_key,
    suspend_user,
)
from tools.dashboard.config import DB_PATH

admin_api = Blueprint("admin_api", __name__, url_prefix="/admin")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
    try:
        user = create_user(email, display_name, role, created_by=admin_user.get("id"))
    except sqlite3.IntegrityError:
        return jsonify({"error": f"User with email {email} already exists"}), 409

    return jsonify({"user": user}), 201


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
    key_info = create_api_key_for_user(
        user_id, label=label, created_by=admin_user.get("id"), expires_at=expires_at
    )
    return jsonify({
        "key_id": key_info["key_id"],
        "raw_key": key_info["raw_key"],
        "prefix": key_info["prefix"],
        "message": "Save this key now — it will not be shown again.",
    }), 201


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
    try:
        query = "SELECT * FROM dashboard_auth_log WHERE 1=1"
        params = []
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return jsonify({"events": [dict(r) for r in rows], "total": len(rows)})
    finally:
        conn.close()
